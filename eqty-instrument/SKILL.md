---
name: eqty-instrument
description: Use this when the user wants their own code to emit an EQTY lineage manifest — "instrument this repo", "add EQTY to this codebase", "add lineage/provenance tracking to my agent", "make this agent emit a manifest", "wire up eqty_sdk here". Mode 1 covers Python repos already using LangChain, LangGraph or DeepAgents: the edit is deterministic — initialise the SDK once, attach one callback handler per invocation, content-address the repo's own tools, export a manifest. Mode 2 covers arbitrary Python, where no framework has fixed the node boundaries: derive a control-flow graph first, ask the user for auto or HITL selection, then instrument. Output is a patch a human reviews, never an auto-commit. Do NOT trigger on "write me a script" or on ordinary coding requests; instrumenting throwaway code is a separate mode the user must ask for explicitly. If a manifest already exists and the question is about reading, verifying or reporting on it, that is the other skill — use eqty-manifest instead.
---

# Instrumenting a codebase to emit EQTY lineage

This is the **write** side. Given a repo that knows nothing about EQTY, produce a
patch that makes it emit a signed, content-addressed record of what ran: inputs →
tool calls → model calls → outputs, verifiable by a third party without trusting
anyone's word for it. The read side — interpreting, verifying and reporting on a
manifest that already exists — is the separate `eqty-manifest` skill. This skill
can point at it once the patch is in place, but that is the user's call, not a
required step.

**What the manifest is provenance *of*.** Not only the run's data flow — **the
repo's own code**. Every computation carries a `Code` asset hashed from the
source of the function that ran, so a reader can hold the manifest against the
repository and check that the code it names is the code that is there. That is
why instrumentation is always an edit to the target's existing files: a decorator
applied to a new adapter function attests the adapter, the repo's real code goes
unrecorded, and the manifest describes a program nobody ships.

**Call this rule *decorate in place*.** The decorator goes on the repo's own
definition, in the file where it already lives — never on an adapter, a wrapper,
a re-export or an `eqty_*` twin. It is named by that phrase throughout this
skill and both references. What the decorator captures is `references/eqtysdk.md` **§6.0**;
the ladder for the cases where an original genuinely cannot take a decorator is
`references/mode2.md` step 4.

**And its companion: *preserve behavior, record only what happened*.** The
instrumented file is the program that ships, so editing it is allowed. Three
rules, detailed in `references/eqtysdk.md` **§6.10**:

1. **Preserve behavior.** Every caller, including the target's own command line,
   behaves exactly as before.
2. **Record only real data flow.** No parameter or return value added just to make
   an edge. A hand-off through a file is recorded by hashing that file inside the
   function that writes it and the one that reads it.
3. **No naming helpers.** Never add functions that build, name or type assets.
   Register assets inline, where their data is read or written.

What still cannot be recorded, such as `Custom` nodes or a missing edge, is
reported as a gap.

**The output is a patch, not a commit.** Instrumentation lands in the working
tree on a branch. A human reviews it and decides. Say so when you finish, and
print the review checklist alongside the diff (`references/mode1.md` for Mode 1, `references/mode2.md` for Mode 2) — three of
the rules below fail *quietly*, so a wrong manifest here still runs clean and
still looks well-formed.

## 1. Which mode am I in

**Mode 1** is a repo already built on LangChain, LangGraph or DeepAgents. The
framework has already decided where the node boundaries are — every graph node,
chat model call, tool call, retrieval and subagent already has a callback, and
EQTY already ships handler packages that register them. So the skill *recognises
and copies*; it derives nothing, and the edit is the same edit every time.

**Mode 2** is arbitrary Python, where "which operations deserve nodes" is a
judgment call no framework has made for you. It is a procedure, not a script:
derive a control-flow graph with fresh agents that have never heard of EQTY — separate,
isolated processes started by `isolated_cfg.py`, never in-session subagents — show
it to the user, ask **auto or HITL**, write the node selection down, and only then
touch code. The whole of it is `references/mode2.md`; the rules for which
operations become nodes are `references/mode2ideas.md`. Read both before starting.

Detection is **per module, not per repo**. A real codebase is LangGraph in one
place and plain Python for data prep and eval everywhere else; modules that do
not match fall through to Mode 2 rather than counting as failures.

```sh
python3 <skill-dir>/detect.py <path-to-repo>      # stdlib only, installs nothing
```

`<skill-dir>` is this skill's own directory — the one holding this SKILL.md.
Your agent shows its path when the skill loads; substitute it. The companion
`eqty-manifest` skill installs beside this one, so its scripts are at
`<skill-dir>/../eqty-manifest/`.

| What the module imports | Handler |
|---|---|
| `deepagents` | `EqtyDeepAgentsHandler` (`eqty_lineage.deepagents`) |
| `langchain*` / `langgraph` | `EqtyCallbackHandler` (`eqty_lineage.langchain`) |
| neither | Mode 2 — hand off to `references/mode2.md`, starting at its CFG dispatch |

`EqtyDeepAgentsHandler` subclasses `EqtyCallbackHandler`, so a deep agent gets
everything the base handler records plus the parts that live in graph state
(virtual filesystem, plan, skills). Only `langchain-core` is required at runtime;
neither handler imports `deepagents` or pins your `langchain`/`langgraph`
versions.

The detector exits **non-zero** when a repo has framework imports but no
attachable call site. That is a real limit, not an empty result — see §5.

## 2. The integration

Every Mode 1 edit is this edit. The full procedure, with each rule traced to its
source, is `references/mode1.md`; read it before editing anything.

**Before you edit: the SDK is a hard dependency, and it is public.**
`python -m pip install eqty_sdk` — no index URL, no credentials. Import it at
module top level and let the program fail if it is missing. **Never write your own
compute decorator** — import `eqty_sdk.compute` and apply it directly, with its
metadata written inline; no wrapper around the decorator and no helper that builds
names or metadata.
**And *decorate in place*** — apply it to the repo's own function definition,
in the file where that function already lives, never to a new function that
calls it, never in a new module. `@compute` hashes the source of whatever it decorates, so an adapter
makes the manifest attest the instrumenting agent's code instead of the repo's,
and leaves the real function emitting nothing (§6.0). **Nothing this skill itself
writes — runners, stubs, fixtures — is ever instrumented.**
**And never write a `try/except ImportError` fallback**, because a run that emits
nothing then looks exactly like a run that worked. Add
`eqty_sdk==<version>` to the target repo's dependency file as part of the patch.
Full rules, with the failure each one prevents, in `references/eqtysdk.md` §13.1. The SDK is
open source: [`eqtylab/integrity-py`](https://github.com/eqtylab/integrity-py). (Mode 1's
*handler* packages are a separate matter — those still resolve through EQTY's
private index.)

```python
from eqty_sdk import Context, Signer, init, set_active_signer
from eqty_lineage.deepagents import EqtyDeepAgentsHandler, eqty_tool   # or: from eqty_lineage.langchain import EqtyCallbackHandler, eqty_tool

# once, at process startup — not per run
cfg = init(default_context=Context.new("Deep Research")).set_store_all_blobs(True)
set_active_signer(Signer.load_or_create(name="deep_research"))

result = agent.invoke(
    {"messages": [{"role": "user", "content": question}]},
    config={"callbacks": [EqtyDeepAgentsHandler()]},   # one handler per concurrent run
)
```

Then export, once the run is done:

```python
cfg.get_default_context().export(Path("./manifests/run.json"))
```

**Merge into an existing `config=`, never replace it.** `recursion_limit`,
`configurable`, thread ids and the caller's own callbacks all live in that dict.
The detector reports which call sites already have one.

**Decorate nothing else.** No node, no subagent, no middleware. `ainvoke` /
`astream` need no different treatment: the handler implements the sync
callbacks and LangChain dispatches those from async runs.

## 3. What the handler does not get for free

Two things the callback stream cannot give you, both of which the skill must add:

**Tool sources.** A callback only ever receives a tool's *name*, so every Tool
asset defaults to a name/description stub — the manifest records *that* a file
was written, not by what code. Decorate the repo's own tools with `@eqty_tool`
(either side of LangChain's `@tool`) and the asset is content-addressed to its
source instead. DeepAgents' built-in belt (`write_file`, `edit_file`, `task`) is
built by middleware and is not yours to decorate; apply the same function to the
compiled agent:

```python
tools_node = getattr(agent.nodes.get("tools"), "bound", None)
for tool_obj in (getattr(tools_node, "tools_by_name", None) or {}).values():
    eqty_tool(tool_obj)
```

**`Path` state.** `pathlib.Path` values in graph state are registered as their
own Dataset assets via `Dataset.from_path`; plain strings are not. Keeping
filesystem references in state as `Path` is the opt-in, and it is the difference
between a file being an entity and being a substring of a state blob.

## 4. Three correctness rules — each fails quietly

None of these is caught by the run succeeding. Each produces a well-formed,
signed, *wrong* manifest. This is why the patch ships with a review checklist.

- **One handler per concurrent run.** File and plan versions are keyed per run.
  Share one handler between two runs in flight and run B's write of `/report.md`
  is recorded as a revision of run A's file, between runs that share nothing but
  the handler. The graph still looks well-formed, and it is wrong. Build the
  handler *at the call site* — inside the request handler,
  inside the retry loop. Reusing one **sequentially** across the turns of a
  checkpointed thread is the intended case, and is what chains turn 1's write to
  turn 3's edit.
- **`init()` at startup, not per run.** It is process-global. A second call does
  not raise and does not re-initialise: it logs `Config already initialized` and
  leaves the first store in place. A per-run `init()` looks like isolation and
  gives none.
- **`execute` (shell) drops the reconstruction caches.** A shell can write files
  without naming a path, so what it did cannot be recovered from the command. The
  handler deliberately registers nothing rather than asserting a version the file
  never held — so the write is simply absent from a manifest that is otherwise
  complete, and nothing in the file says a step is missing. Prefer the file tools over shell redirection when you want writes
  attested.

## 5. What Mode 1 cannot cover

**No in-process Python call site, no callback.** An agent that runs in a
`langgraph dev` subprocess, on LangSmith's platform from an `agent.json`, or
inside a container has nothing to pass `config={"callbacks": [...]}` to. Seven of
the fifteen upstream DeepAgents examples are in exactly this position
(`references/upstream/eqty-lineage-deepagents.EXAMPLES.md`). It is a
deployment-model limitation, not a handler one, and covering it needs a hook the
runtime exposes. **Say so plainly rather than emitting an empty plan** — that is
what the detector's non-zero exit is for.

A module-level graph with no invoke is the recoverable version of this: import it
and drive it yourself from a script that *does* have a call site.

**Backend affects completeness, not success.** Nothing errors and nothing is
misrecorded; recording is silently less complete as you move down:

| Class | Backend | What you get |
|---|---|---|
| **A** | default `StateBackend` | everything, including the full file version chain |
| **B** | `FilesystemBackend` | files via tool arguments; records the **virtual** path the agent saw |
| **C** | `CompositeBackend` / sandbox | as B, plus `execute` as an ordinary tool computation, with the cache-drop caveat above |

## 6. Verify what you emitted, then hand off

An instrumented repo is not done until a manifest has come out of it and been
checked. Run it and export. If the companion `eqty-manifest` skill is installed,
put the result through it:

```sh
uv run <skill-dir>/../eqty-manifest/summary.py <manifest>                     # every check in one block; exit 0 / 1 / 2
uv run <skill-dir>/../eqty-manifest/parse_manifest.py <manifest> summary      # statement counts, signers, integrity problems
uv run <skill-dir>/../eqty-manifest/parse_manifest.py <manifest> timeline     # is the DAG reconstructed, are the steps named
uv run <skill-dir>/../eqty-manifest/verify_credentials.py <manifest>          # do the signatures verify
```

If it is not installed, say so, name the manifest path, and leave these checks to
the user. Either way, the three things to check, in this order:

1. **Structure** — the computations carry recognisable names, and the file /
   plan / subagent chains are present for the backend class you're in.
2. **Content integrity** — every blob hashes to its CID.
3. **Signatures verify** — every credential's proof checks out, with no
   network access.

Verification is offline, but the contexts no longer have to come from the
manifest. `eqty_sdk` is open source and resolves the W3C JSON-LD contexts
(`credentials/v2`, `security/v2`, `security/v1`) from documents compiled into
the package, so a reader installs the SDK rather than needing the file to carry
them. `eqty-manifest`'s `verify_credentials.py` delegates to `verify_vc` /
`verify_statement` for exactly this reason.

**What the exporter still does:** it embeds EQTY's own vocabulary and nothing
else, so a manifest measures **`0 / N`** verifying from its own contexts alone.
That is a property of the emitter worth knowing, but it
is no longer a blocker, and there is no post-export repair step.

**Commit to heavy artifacts by CID, never by embedding them.** Weights,
datasets and code belong in the manifest as content-addressed references. A
manifest that inlines them stops being a record and starts being a copy.

**Emit the manifest, and nothing else.** One file, exactly as the SDK exported
it — no repaired copy, no context-embedded twin, no post-export rewrite of the
emitter's own output. A second copy is a second thing to keep in sync, and a
skill that patches what its emitter produced is fixing the problem one layer
too late; the fix belongs in the exporter. Anything derived *from* the manifest
— reports, narratives, timelines — is the read side's business, and it writes
one file too.

**Handoff.** Finish by naming the manifest path you produced. `eqty-manifest` is
the separate skill that interprets, verifies and reports on it — mention it as an
option if the user wants to go further, but do not assume they do.

**Deliver the change as a pull request when the repo has a remote.** The branch is
where the change already lives; a pull request is how a reviewer reads it. Push the
branch and open one against the repo's default branch. Never commit to that branch,
and never merge your own pull request — the human decides, which is the whole point
of shipping a proposal rather than a result.

Keep the body factual: what you instrumented, the manifest path the app will now
write, and the versions you ran against. Attach the review checklist from
`references/mode1.md` or `references/mode2.md`. **Do not summarise your own diff.** A reviewer is reading it
to judge whether the edit is right, and your account of it is not evidence — three
of the rules in §4 fail quietly, so a wrong edit describes itself just as
convincingly as a right one.

If the repo has no remote, no credential is available, or the push is refused, leave
the change on the branch, write the diff out beside it, say which of the three it
was, and stop. Do not add a remote, do not push anywhere the user did not name, and
do not open a pull request against a repository you were not pointed at.

## Files

| Path | What it is |
|---|---|
| `references/mode1.md` | the procedure: the edit, rule by rule, plus the review checklist |
| `references/mode2.md` | Mode 2 — arbitrary Python: the CFG-first procedure, auto and HITL, and the rules that fail quietly |
| `references/eqtysdk.md` | **required reading before a Mode 2 edit** — the SDK's surface, the rules that fail quietly, and §13.1's dependency rules. Mode 1 restates what it needs from it inline |
| `references/mode2ideas.md` | Mode 2 step 3 — which operations become nodes |
| `references/upstream/` | the `eqty-lineage` originals Mode 1 cites by `file:line`, verbatim, with `SOURCES.md` |
| `detect.py` | stdlib-only AST scan → JSON edit plan + human summary |
| `check_graph.py` | Mode 2's post-run graph checks on an emitted manifest |
| `isolated_cfg.py` | Mode 2 step 1: runs the L1 and L2 CFG agents as fresh, isolated `claude -p` or `codex exec` processes and checks nothing leaked |
| `examples/mode1/` | real Mode 1 runs — `*_before.py`, `*_after.py`, `changes.diff` |
| `examples/mode2/` | real Mode 2 runs, auto and HITL — before, after, diff, manifest |
