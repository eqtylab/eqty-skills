#!/usr/bin/env python3
"""isolated_cfg.py — Mode 2 step 1: draw the CFG with fresh, isolated agents.

    python3 <skill-dir>/isolated_cfg.py <target-repo> --out <dir> [--model M] [--run "<user's run>"]

The orchestrator never draws the CFG and never dispatches it as an in-session
subagent: a subagent inherits the session's context — the repo path, git status,
recent commits, CLAUDE.md, memory and the skills list, which carries this skill's
own description of what the diagram is for. This script launches each level as a
separate `claude -p` process that sees only its prompt and a copy of the target:

- the copy lives in a neutral temp directory outside any git repo (no git status,
  no commit history, no project CLAUDE.md, no project memory);
- `--disable-slash-commands` (no skills), `--strict-mcp-config` (no MCP servers),
  `--setting-sources ""` (no user/project settings, so no plugin hooks), CLAUDE.md
  auto-discovery excluded, tools limited to Read/Write/Glob/Grep, no session saved;
- L1 runs first; L2 is a second fresh process given L1's diagram and L1's `Run:`
  line, carried verbatim — the only thing that flows from one level to the next.

Every run is then checked, and the result written to `<out>/isolation.json`:
the init record (no skills, MCP servers or slash commands), every tool call inside
the agent's own directory, the prompt free of project words, and the output shaped
as required. Exit 0 only if every check passes; otherwise the CFG must not be used.

Known residue: the account's `userEmail` line is injected at login and cannot be
removed without an API key (`--bare`). It is recorded in isolation.json.

Outputs: <out>/L1.md, <out>/L2.md, <out>/isolation.json. Assembling
<target>.cfg.html from them is step 1's last part (references/mode2.md).
Stdlib only; needs the `claude` CLI on PATH.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

L1_PROMPT = "Give me a high-level control flow diagram of what happens in this program."
L2_PROMPT = ("Go one level deeper — show the major functions and the branches between them, "
             "but don't go into individual statements.")

PROGRAM = ("**Program:** the repository in `./repo/` (relative to your working directory). "
           "Read only files in that directory{extra}. Do not read any other files, do not browse "
           "the web, and do not install anything.")

OUTPUT = """**Output:** write a single Markdown file to `./{name}` containing, in this order:
1. One line starting `Run:` — {run_rule}
2. The diagram as a Mermaid `flowchart TD` code block. Box labels are short: a function name and a half-sentence, e.g. "`_cast()` — turn the return value into bytes".
3. A table with one row per box: `box id | label | where it lives (file:function or file:line range) | what it does`. Every box must have a row, and every "where it lives" must name real code.
{tail}
The deliverable is a drawn diagram, not an essay. No other prose."""

L1_RUN_RULE = ("which run of the program you diagrammed: which file or function starts it and, if the "
               "program offers several commands or entry points, the command sequence. Choose it yourself from the code.")
L2_RUN_RULE = "the same `Run:` line as the high-level diagram, verbatim."

# Words that would tell a CFG agent what the diagram is for. None may appear in a prompt.
LEAK_WORDS = re.compile(r"eqty|lineage|provenance|manifest|instrument|attest|\bsdk\b|audit", re.I)

# What is copied: the working tree, minus version control and environments.
SKIP_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules", "__pycache__",
             ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".eqty", ".eqty_sdk"}


def copy_repo(src, dst):
    def ignore(d, names):
        return [n for n in names if n in SKIP_DIRS or n.endswith(".pyc")]
    shutil.copytree(src, dst, ignore=ignore, symlinks=True)


def inside_git(path):
    return subprocess.run(["git", "-C", path, "rev-parse", "--is-inside-work-tree"],
                          capture_output=True, text=True).returncode == 0


def run_agent(workdir, prompt, model):
    home = os.path.expanduser("~")
    settings = json.dumps({"claudeMdExcludes": [os.path.join(home, ".claude", "**"),
                                                "**/CLAUDE.md", "**/CLAUDE.local.md"]})
    cmd = ["claude", "-p", prompt,
           "--disable-slash-commands", "--strict-mcp-config", "--setting-sources", "",
           "--settings", settings, "--tools", "Read,Write,Glob,Grep",
           "--permission-mode", "acceptEdits", "--no-session-persistence",
           "--output-format", "stream-json", "--verbose"]
    if model:
        cmd += ["--model", model]
    proc = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True)
    events = []
    for line in proc.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return proc.returncode, events, proc.stderr


def check_run(level, workdir, prompt, events, out_name):
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), {})
    result = next((e for e in events if e.get("type") == "result"), {})
    tools = [c for e in events if e.get("type") == "assistant"
             for c in (e.get("message", {}).get("content") or []) if c.get("type") == "tool_use"]
    real = os.path.realpath(workdir)
    outside = []
    for t in tools:
        path = t["input"].get("file_path") or t["input"].get("path") or ""
        if path and not os.path.realpath(os.path.join(workdir, path)).startswith(real + os.sep):
            outside.append(f"{t['name']} {path}")
    out_path = os.path.join(workdir, out_name)
    text = open(out_path).read() if os.path.exists(out_path) else ""
    first = next((l for l in text.splitlines() if l.strip()), "")
    checks = {
        "prompt_has_no_project_words": not LEAK_WORDS.search(prompt),
        "workdir_outside_git": not inside_git(workdir),
        "no_skills": init.get("skills") == [],
        "no_mcp_servers": init.get("mcp_servers") == [],
        "no_slash_commands": init.get("slash_commands") == [],
        "only_builtin_plugins": all(p.get("path") == "builtin" for p in init.get("plugins", [])),
        "tools_limited": set(init.get("tools", [])) <= {"Read", "Write", "Glob", "Grep"},
        "every_tool_call_inside_workdir": not outside,
        "agent_succeeded": result.get("subtype") == "success" and not result.get("is_error"),
        "output_has_run_line": first.startswith("Run:"),
        "output_has_mermaid": "```mermaid" in text,
    }
    return {
        "level": level,
        "workdir": workdir,
        "model": init.get("model"),
        "claude_code_version": init.get("claude_code_version"),
        "init": {k: init.get(k) for k in ("cwd", "skills", "mcp_servers", "slash_commands", "tools")}
                | {"plugins": [p.get("name") for p in init.get("plugins", [])]},
        "tool_calls": [f"{t['name']} {t['input'].get('file_path') or t['input'].get('path') or t['input'].get('pattern') or ''}"
                       for t in tools],
        "outside_workdir": outside,
        "checks": checks,
        "passed": all(checks.values()),
        "known_residue": "account userEmail line is injected at login (not removable without an API key)",
    }, text


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("repo", help="the target repository (read, never modified)")
    ap.add_argument("--out", required=True, help="where L1.md, L2.md and isolation.json go")
    ap.add_argument("--model", help="model for both agents (default: the CLI's default)")
    ap.add_argument("--run", help="the run the user asked for; replaces L1's own choice")
    ap.add_argument("--keep", action="store_true", help="keep the temp workdirs")
    args = ap.parse_args()

    if not shutil.which("claude"):
        sys.exit("isolated_cfg: the `claude` CLI is not on PATH, so no isolated agent can be started. "
                 "Do not fall back to an in-session subagent; report this and stop.")
    repo = os.path.realpath(args.repo)
    os.makedirs(args.out, exist_ok=True)
    base = tempfile.mkdtemp(prefix="cfg-")
    report = {"target": repo, "levels": []}

    # L1
    l1_dir = os.path.join(base, "l1")
    copy_repo(repo, os.path.join(l1_dir, "repo"))
    run_rule = (f"exactly this line: `Run: {args.run}`" if args.run else L1_RUN_RULE)
    l1_prompt = "\n\n".join([L1_PROMPT, PROGRAM.format(extra=""), "**Budget:** about 9 boxes.",
                             OUTPUT.format(name="L1.md", run_rule=run_rule,
                                           tail="4. Under a heading `## Other entry points`, list the runnable entry points or paths your diagram does not cover.\n")])
    code, events, err = run_agent(l1_dir, l1_prompt, args.model)
    l1_report, l1_text = check_run("L1", l1_dir, l1_prompt, events, "L1.md")
    report["levels"].append(l1_report | {"prompt": l1_prompt})
    if not l1_report["passed"]:
        json.dump(report, open(os.path.join(args.out, "isolation.json"), "w"), indent=2)
        sys.exit(f"isolated_cfg: L1 failed its isolation or output checks; see {args.out}/isolation.json\n{err[-2000:]}")
    open(os.path.join(args.out, "L1.md"), "w").write(l1_text)
    run_line = next(l for l in l1_text.splitlines() if l.strip())

    # L2 — a second fresh process; gets L1's diagram and Run line, nothing else.
    l2_dir = os.path.join(base, "l2")
    copy_repo(repo, os.path.join(l2_dir, "repo"))
    open(os.path.join(l2_dir, "L1.md"), "w").write(l1_text)
    l2_prompt = "\n\n".join([
        L2_PROMPT,
        PROGRAM.format(extra=", plus `./L1.md`"),
        f"**One level up:** the high-level diagram for this program is `./L1.md`. Go one level deeper than that, on the same run:\n\n{run_line}",
        "**What a box is:** a major function, or a branch between major functions. Individual statements are not boxes; if a box would be one line of code, it belongs inside its caller. Draw branches as decision nodes with labelled edges.",
        "**Budget:** about 30 boxes.",
        OUTPUT.format(name="L2.md", run_rule=L2_RUN_RULE, tail=""),
    ])
    code, events, err = run_agent(l2_dir, l2_prompt, args.model)
    l2_report, l2_text = check_run("L2", l2_dir, l2_prompt, events, "L2.md")
    l2_report["checks"]["run_line_carried_verbatim"] = next(
        (l for l in l2_text.splitlines() if l.strip()), "") == run_line
    l2_report["passed"] = all(l2_report["checks"].values())
    report["levels"].append(l2_report | {"prompt": l2_prompt})
    json.dump(report, open(os.path.join(args.out, "isolation.json"), "w"), indent=2)
    if not l2_report["passed"]:
        sys.exit(f"isolated_cfg: L2 failed its isolation or output checks; see {args.out}/isolation.json\n{err[-2000:]}")
    open(os.path.join(args.out, "L2.md"), "w").write(l2_text)

    if not args.keep:
        shutil.rmtree(base, ignore_errors=True)
    for lv in report["levels"]:
        print(f"{lv['level']}: {'PASS' if lv['passed'] else 'FAIL'}  model={lv['model']}  "
              f"tool calls={len(lv['tool_calls'])}  outside workdir={len(lv['outside_workdir'])}")
    print(f"wrote {args.out}/L1.md, L2.md, isolation.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
