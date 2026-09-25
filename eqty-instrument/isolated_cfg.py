#!/usr/bin/env python3
"""isolated_cfg.py — Mode 2 step 1: draw the CFG with fresh, isolated agents.

    python3 <skill-dir>/isolated_cfg.py <target-repo> --out <dir> [--agent claude|codex] [--model M] [--run "<user's run>"]

The orchestrator never draws the CFG and never dispatches it as an in-session
subagent: a subagent inherits the session's context — the repo path, git status,
recent commits, CLAUDE.md or AGENTS.md, memory and the skills list, which carries
this skill's own description of what the diagram is for. This script launches
each level as a separate process that sees only its prompt and a copy of the
target, in a neutral temp directory outside any git repo (no git status, no commit
history, no project instructions, no project memory). L1 runs first; L2 is a
second fresh process given L1's diagram and L1's `Run:` line, carried verbatim —
the only thing that flows from one level to the next.

`--agent claude` (the default when `claude` is on PATH) runs `claude -p` with
`--disable-slash-commands` (no skills), `--strict-mcp-config` (no MCP servers),
`--setting-sources ""` (no user/project settings, so no plugin hooks), CLAUDE.md
auto-discovery excluded, tools limited to Read/Write/Glob/Grep, no session saved.

`--agent codex` runs `codex exec` with a fresh, empty CODEX_HOME holding only a
link to the user's `auth.json` — so no user config, skills, MCP servers, plugins,
hooks, memories or `~/.codex/AGENTS.md` — plus `--ignore-rules`, web search off,
the extra tool families disabled, a minimal shell environment, no session saved,
and the `workspace-write` sandbox, which *enforces* that writes stay in the
agent's directory and that there is no network.

Every run is then checked, and the result written to `<out>/isolation.json`: for
Claude the init record (no skills, MCP servers or slash commands) and every tool
call inside the agent's own directory; for Codex the home it ran with, every event
type (anything but messages, reasoning, shell commands and file writes fails), every
file write inside its directory, and every shell command free of outside paths;
for both, the prompt free of project words and the output shaped as required.
Exit 0 only if every check passes; otherwise the CFG must not be used.

Known residue, recorded in isolation.json: under Claude, the account's `userEmail`
line is injected at login and cannot be removed without an API key (`--bare`).
Under Codex, its built-in `.system` skills are installed into the fresh home (none
concerns this project), and its sandbox lets a shell command *read* anywhere, so
read containment is audited from the command text rather than enforced.

Outputs: <out>/L1.md, <out>/L2.md, <out>/isolation.json. Assembling
<target>.cfg.html from them is step 1's last part (references/mode2.md).
Stdlib only; needs the `claude` or `codex` CLI on PATH.
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
    return proc.returncode, parse_jsonl(proc.stdout), proc.stderr


def parse_jsonl(text):
    events = []
    for line in text.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return events


# Codex feature families a CFG agent has no use for; each is a way out of the copy.
CODEX_DISABLED = ["apps", "browser_use", "computer_use", "in_app_browser", "image_generation",
                  "plugins", "memories", "goals", "hooks"]
# The event items a contained Codex run produces. Anything else — a web search, an
# MCP call, a sub-agent whose commands the stream would not show — fails the run.
CODEX_ITEMS = {"agent_message", "reasoning", "command_execution", "file_change", "todo_list"}


def run_agent_codex(workdir, prompt, model):
    """One fresh `codex exec` process. Its events end with a synthetic
    `isolation.codex_home` record describing the home it ran with."""
    real_home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
    real_auth = os.path.join(real_home, "auth.json")
    home = tempfile.mkdtemp(prefix="codex-home-")
    auth = os.path.join(home, "auth.json")
    if os.path.exists(real_auth):
        os.symlink(real_auth, auth)
    cmd = ["codex", "exec", prompt, "--json", "--ephemeral", "--skip-git-repo-check",
           "-C", workdir, "-s", "workspace-write", "--ignore-rules",
           "-c", 'web_search="disabled"', "-c", 'shell_environment_policy.inherit="core"']
    for feature in CODEX_DISABLED:
        cmd += ["--disable", feature]
    if model:
        cmd += ["--model", model]
    env = dict(os.environ, CODEX_HOME=home)
    try:
        proc = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, env=env)
    finally:
        # A token refresh may replace the link with a new file; put it back where the
        # user's own Codex reads it, or the next login would present a stale token.
        if os.path.exists(auth) and not os.path.islink(auth):
            os.replace(auth, real_auth)
        skills_dir = os.path.join(home, "skills")
        record = {"type": "isolation.codex_home", "home": home,
                  "skills": sorted(os.listdir(skills_dir)) if os.path.isdir(skills_dir) else [],
                  "system_skills": sorted(os.listdir(os.path.join(skills_dir, ".system")))
                  if os.path.isdir(os.path.join(skills_dir, ".system")) else [],
                  "config_files": [f for f in ("config.toml", "AGENTS.md", "hooks.json")
                                   if os.path.exists(os.path.join(home, f))]}
        shutil.rmtree(home, ignore_errors=True)
    return proc.returncode, parse_jsonl(proc.stdout) + [record], proc.stderr


# A Codex command string arrives wrapped in the shell it ran under.
SHELL_WRAPPER = re.compile(r"^\S*/(?:ba|z)?sh\s+-l?c\s+")
HARMLESS_PATHS = ("/dev/null", "/dev/stdout", "/dev/stderr")


def command_escapes(command, workdir):
    """Why a shell command reaches outside `workdir`, or None. Heuristic: it reads the
    command text, so it catches paths that are written out, not ones computed at
    run time. The sandbox, not this, is what stops writes and network."""
    body = SHELL_WRAPPER.sub("", command, count=1)
    roots = {workdir, os.path.realpath(workdir)}
    for token in re.findall(r"(?<![\w.$-])/[^\s'\"`;|&)]*", body):
        if token in HARMLESS_PATHS or token == "/":
            continue
        if not any(token == r or token.startswith(r + os.sep) for r in roots):
            return "absolute path %s" % token
    if re.search(r"(?<![\w/])~|\$HOME\b|\$\{HOME\}", body):
        return "home directory"
    if re.search(r"(?:^|[\s'\"/=])\.\.(?:/|[\s'\"]|$)", body):
        return "parent directory"
    if re.search(r"\b(?:curl|wget|ssh|scp|nc|pip3?|npm|uv|git)\b", body):
        return "network or install command"
    return None


def read_output(workdir, out_name):
    out_path = os.path.join(workdir, out_name)
    text = open(out_path).read() if os.path.exists(out_path) else ""
    return text, next((l for l in text.splitlines() if l.strip()), "")


def check_run_codex(level, workdir, prompt, events, out_name):
    home = next((e for e in events if e.get("type") == "isolation.codex_home"), None)
    items = [e["item"] for e in events if e.get("type") == "item.completed" and "item" in e]
    commands = [i.get("command", "") for i in items if i.get("type") == "command_execution"]
    real = os.path.realpath(workdir)
    outside = [f"file_change {c.get('path')}" for i in items if i.get("type") == "file_change"
               for c in i.get("changes") or []
               if not os.path.realpath(os.path.join(workdir, c.get("path", ""))).startswith(real + os.sep)]
    outside += [f"command {c} ({why})" for c in commands for why in [command_escapes(c, workdir)] if why]
    unexpected = sorted({i.get("type") for i in items} - CODEX_ITEMS)
    failed = [e.get("type") for e in events if e.get("type") in ("turn.failed", "error")]
    text, first = read_output(workdir, out_name)
    checks = {
        "prompt_has_no_project_words": not LEAK_WORDS.search(prompt),
        "workdir_outside_git": not inside_git(workdir),
        "fresh_codex_home": bool(home) and not home["config_files"]
                            and set(home["skills"]) <= {".system"},
        "only_expected_event_items": not unexpected,
        "every_tool_call_inside_workdir": not outside,
        "agent_succeeded": any(e.get("type") == "turn.completed" for e in events) and not failed,
        "output_has_run_line": first.startswith("Run:"),
        "output_has_mermaid": "```mermaid" in text,
    }
    return {
        "level": level,
        "agent": "codex",
        "workdir": workdir,
        "init": {"cwd": workdir, "codex_home": home, "unexpected_items": unexpected},
        "tool_calls": commands + [f"file_change {c.get('path')}" for i in items
                                  if i.get("type") == "file_change" for c in i.get("changes") or []],
        "outside_workdir": outside,
        "checks": checks,
        "passed": all(checks.values()),
        "known_residue": "Codex's built-in .system skills (%s) are installed into the fresh home; "
                         "reads are audited from command text, not enforced by the sandbox"
                         % ", ".join(home["system_skills"] if home else []),
    }, text


def check_run(level, workdir, prompt, events, out_name, agent="claude"):
    if agent == "codex":
        return check_run_codex(level, workdir, prompt, events, out_name)
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
    text, first = read_output(workdir, out_name)
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
        "agent": "claude",
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


AGENTS = {"claude": run_agent, "codex": run_agent_codex}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("repo", help="the target repository (read, never modified)")
    ap.add_argument("--out", required=True, help="where L1.md, L2.md and isolation.json go")
    ap.add_argument("--agent", choices=AGENTS,
                    help="which CLI starts the agents (default: claude if on PATH, else codex)")
    ap.add_argument("--model", help="model for both agents (default: the CLI's default)")
    ap.add_argument("--run", help="the run the user asked for; replaces L1's own choice")
    ap.add_argument("--keep", action="store_true", help="keep the temp workdirs")
    args = ap.parse_args()

    agent = args.agent or next((a for a in AGENTS if shutil.which(a)), None)
    if not agent or not shutil.which(agent):
        sys.exit("isolated_cfg: %s not on PATH, so no isolated agent can be started. "
                 "Do not fall back to an in-session subagent; report this and stop."
                 % ("the `%s` CLI is" % agent if agent else "neither the `claude` nor the `codex` CLI is"))
    run = AGENTS[agent]
    repo = os.path.realpath(args.repo)
    os.makedirs(args.out, exist_ok=True)
    base = tempfile.mkdtemp(prefix="cfg-")
    report = {"target": repo, "agent": agent, "levels": []}

    # L1
    l1_dir = os.path.join(base, "l1")
    copy_repo(repo, os.path.join(l1_dir, "repo"))
    run_rule = (f"exactly this line: `Run: {args.run}`" if args.run else L1_RUN_RULE)
    l1_prompt = "\n\n".join([L1_PROMPT, PROGRAM.format(extra=""), "**Budget:** about 9 boxes.",
                             OUTPUT.format(name="L1.md", run_rule=run_rule,
                                           tail="4. Under a heading `## Other entry points`, list the runnable entry points or paths your diagram does not cover.\n")])
    code, events, err = run(l1_dir, l1_prompt, args.model)
    l1_report, l1_text = check_run("L1", l1_dir, l1_prompt, events, "L1.md", agent)
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
    code, events, err = run(l2_dir, l2_prompt, args.model)
    l2_report, l2_text = check_run("L2", l2_dir, l2_prompt, events, "L2.md", agent)
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
        print(f"{lv['level']}: {'PASS' if lv['passed'] else 'FAIL'}  agent={agent}  model={lv.get('model') or args.model or 'default'}  "
              f"tool calls={len(lv['tool_calls'])}  outside workdir={len(lv['outside_workdir'])}")
    print(f"wrote {args.out}/L1.md, L2.md, isolation.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
