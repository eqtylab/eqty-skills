# Mode 1 — the procedure

Python repos already using **LangChain, LangGraph or DeepAgents**. Deterministic:
the framework decided the node boundaries, EQTY ships the handlers, and the edit
is the same edit every time.

This file is written for an agent executing that edit. It is not a copy of the
packages' own docs — those are written for a developer *choosing* to adopt the
handler, and cover options this procedure deliberately does not use. The reference
implementation is vendored verbatim in `references/upstream/` — read-only, pinned,
with provenance and hashes in `references/upstream/SOURCES.md`:

| Source | What it settles |
|---|---|
| `references/upstream/eqty-lineage-langchain.README.md` | asset identity, `@eqty_tool`, `StateExtractor`, `Path` handling |
| `references/upstream/eqty-lineage-deepagents.README.md` | virtual filesystem, plan, skills, write attribution, handler lifetime |
| `references/upstream/eqty-lineage-deepagents.EXAMPLES.md` | fifteen upstream examples: which eight are instrumentable and the exact edit each needs |
| `references/upstream/deepagents/research_agent.py` | the fullest worked example — scripted model, no API key |
| `references/upstream/langchain/research_agent.py` | the LangGraph equivalent |
| `references/upstream/test_interrupt_on.py` | the interrupt-guard contract, more precise than the prose |

Read the source before improvising. Everything below is recorded behaviour, not
inference.

## The edit, in one block

```python
from eqty_sdk import Context, Signer, init, set_active_signer
from eqty_lineage.deepagents import EqtyDeepAgentsHandler, eqty_tool
# LangChain / LangGraph: from eqty_lineage.langchain import EqtyCallbackHandler, eqty_tool

cfg = init(default_context=Context.new("Deep Research")).set_store_all_blobs(True)
set_active_signer(Signer.load_or_create(name="deep_research"))

result = agent.invoke(
    {"messages": [{"role": "user", "content": question}]},
    config={"callbacks": [EqtyDeepAgentsHandler()], "recursion_limit": 80},
)

cfg.get_default_context().export(Path("./manifests/run.json"))
```

`init()` belongs at process startup and the handler at the call site. Those are
two different lifetimes and conflating them is the most common way to get a
manifest that is well-formed and wrong; see **Handler lifetime** below.

`init()` writes a `.eqty_sdk` store under the **working directory**, so two runs
sharing a directory produce a store where the second appears to depend on the
first. Fixtures and tests that need isolation should `chdir` into a fresh
temporary directory first, as `references/upstream/deepagents/research_agent.py:212`
does.

## The edit, rule by rule

Eight rules. Each one is copied from a named source, not derived — if a repo
seems to need a ninth, that is a finding to report, not a rule to invent.

**1. `init()` once, at process startup — never per run.**
It is process-global. A second call does not raise and does not re-initialise:
it logs `Config already initialized` and leaves the first store in place, so a
per-run `init()` looks like isolation and gives none. Put it where the process
starts: `main()`, the module `if __name__ == "__main__"` block, the app factory,
the worker boot. `references/upstream/deepagents/research_agent.py:212` is the shape —
one `init_sdk()`, called once. Source: `references/upstream/eqty-lineage-deepagents.README.md`,
*Handler lifetime*.

**2. One handler per invocation, constructed at the call site.**
Not one at module scope that every run shares. File and plan versions are keyed
per run, so two runs in flight on one handler record the second run's write of
`/report.md` as a revision of the first run's file. `detect.py` flags each attach
point that sits inside an `async def`, a loop or a request handler; for those the
handler **must** be constructed inline:

```python
config={"callbacks": [EqtyDeepAgentsHandler()]}      # built here, per run
```

Sequential reuse across the turns of one checkpointed thread is the opposite
case — deliberate, and what chains turn 1's write to turn 3's edit. Source:
`references/upstream/eqty-lineage-deepagents.README.md`, *Handler lifetime*; `references/upstream/eqty-lineage-deepagents.EXAMPLES.md`,
*async-subagent-server*; and the handler's own runtime warning when it sees two
runs open at once.

**3. Merge into an existing `config=`. Never replace it.**
`recursion_limit`, `configurable`, thread ids and the caller's own callbacks all
live in that dict, and clobbering it breaks the app rather than the manifest —
which at least fails loudly, unlike the rest of this list. Both worked examples
carry a `recursion_limit` alongside the handler
(`references/upstream/deepagents/research_agent.py:244`,
`references/upstream/langchain/research_agent.py:162`). `detect.py` reports the
existing dict verbatim for every attach point, so there is no need to guess:

```python
# config= already present            → add one key
config={"recursion_limit": 80, "callbacks": [EqtyDeepAgentsHandler()]}
# callbacks already present          → append, keep theirs
config={**existing_config, "callbacks": [*existing_config.get("callbacks", []), EqtyDeepAgentsHandler()]}
```

**4. `@eqty_tool` on every tool the repo defines.**
Either side of LangChain's `@tool`, on the repo's own `def`, in the file where
that tool already lives — *decorate in place*, never on a re-export or on a
wrapper written to hold it, for the same reason `@compute` has (`references/eqtysdk.md` §6.0): it records source
with `inspect.getsource`, so whatever it decorates is what the manifest claims
ran. The decorator returns the function unchanged.
Without it the Tool asset is a name/description stub, because a callback only
ever receives a tool's *name*. It uses `inspect.getsource`, so it needs a real
file — not a REPL, not `exec`. `detect.py` lists every `@tool` function and
whether it already has it. Source: `references/upstream/eqty-lineage-langchain.README.md`,
*`@eqty_tool` — capture tool source code*.

**5. For DeepAgents, apply the same function to the compiled belt.**
`write_file`, `edit_file` and `task` are built by DeepAgents' middleware and are
not yours to decorate, so the manifest would record *that* a file was written but
not by what code. **This is the one exception to *decorate in place* — the only place a decorator
touches something the repo did not define — and it is applied to the live tool
object at runtime** — not to a
source wrapper written to stand in for it, which would content-address the
wrapper and leave the real tool a stub. Copy `references/upstream/deepagents/research_agent.py:163`,
including its guards — a compiled graph that does not have a `tools` node should
degrade to the stub, not raise:

```python
agent = create_deep_agent(...)
tools_node = getattr(agent.nodes.get("tools"), "bound", None)
for tool_obj in (getattr(tools_node, "tools_by_name", None) or {}).values():
    eqty_tool(tool_obj)
```

Source: `references/upstream/eqty-lineage-deepagents.README.md`, *`@eqty_tool` and the built-in tool
belt*.

**6. Filesystem references in state become `pathlib.Path`.**
A `Path` in graph state is registered as its own Dataset via
`Dataset.from_path`; the same value as a `str` is not registered at all. This is
the one rule that edits the repo's own logic rather than adding to it, so keep it
to state keys `detect.py` names and check nothing downstream was relying on the
string. Source: `references/upstream/eqty-lineage-langchain.README.md`, *`Path` values in graph
state*.

**7. Decorate nothing else, and treat async the same.**
No node, no subagent, no middleware, no `@eqty_sdk.compute` anywhere. The
handler already sees every node, model call, tool call, retrieval and subagent;
anything added on top is a second recording of the same event. `ainvoke`,
`astream`, `batch` and `abatch` take the identical edit — the handler implements
the sync callbacks and LangChain dispatches those from async runs. Source:
`references/upstream/eqty-lineage-deepagents.EXAMPLES.md`, *The pattern*.

**8. Export once, after the run.**
Instrumentation that never exports produces no artifact:

```python
cfg.get_default_context().export(Path("./manifests/<name>.json"))
```

Keep the handle `init()` returned (`cfg = init(...)`) or call
`eqty_sdk.get_config()` at export time.

The exporter embeds EQTY's vocabulary only, so a manifest as written verifies
**`0 / N`** from its own contexts — none of them. That needs no repair step: `eqty_sdk` is open
source and resolves the W3C contexts offline from the package, so the read side
verifies it as-is. Verify what came out — SKILL.md §6.

Use `Signer.load_or_create(name=...)`, not `Signer.new(name=..., _load_if_exists=True)`.
The latter works and warns `DeprecationWarning: _load_if_exists is deprecated`.

## Picking the handler

Per module, not per repo.

- imports `deepagents` → `EqtyDeepAgentsHandler`
- imports `langchain*` or `langgraph` → `EqtyCallbackHandler`
- neither → not Mode 1. Report it as a Mode 2 candidate; do not edit it.

`EqtyDeepAgentsHandler` subclasses `EqtyCallbackHandler`, so it records
everything the base handler records **plus** the parts of a deep agent that live
in graph state. Only `langchain-core` is required at runtime — the handler works
on plain LangChain runnables, and the DeepAgents package never imports
`deepagents`, reading everything from the callback stream and graph state
instead. Installing it therefore pins nothing.

## What the base handler records

Without any decoration at all:

| Callback | Registered as |
|---|---|
| graph node run | input/output Dataset assets + a computation |
| chat model call | Prompt + Model in, Reasoning out |
| tool call | Tool + input Dataset in, output Dataset out |
| retrieval | Tool + query Prompt in, **one `Document` per retrieved document** out |
| subagent | its own `agent` computation, linked to the tool that delegated to it |

Every computation carries a `framework` tag read from the run's own metadata
rather than assumed. Failures are recorded, not dropped: a node, tool or model
call that raises produces a `graph_node_error` / `tool_error` /
`chat_model_error` computation with the error type and message as its output, and
a failed tool's error also feeds the enclosing node's output state, because that
is what the model sees.

A `GraphInterrupt` is **not** a failure. Human-in-the-loop pauses
(`interrupt_on=...`) are handled — the guard that tells a pause from a crash is
`_is_graph_control_flow`, vendored as `references/upstream/interrupt_guard.py`,
and `references/upstream/test_interrupt_on.py` asserts
the DeepAgents handler inherits it.

### And, for a deep agent, what lives in state

| DeepAgents concept | Asset | Where it lands |
|---|---|---|
| the deep agent | `Agent` | input to the root `graph` computation |
| a subagent | `Agent` | input to its own `agent` computation **and** to the `task` call that spawned it |
| a virtual file | `Document` | output of the call that wrote it; input to every call that reads it |
| a file the run only reads | `Document` | input to the `read_file` call — the tool's *rendering*, not the file's bytes |
| a todo-list revision | `Dataset` | output of the `write_todos` that wrote it, chained to the revision it replaced |
| a loaded skill | `Skill` | input to the model turn whose state held the catalogue |
| a system prompt | `SystemPrompt` | input to that `chat_model` computation |

No new primitives: every one of these is an asset type the SDK already ships.

## Asset identity — why the same name can be two assets

Each of these is content-addressed to **what identifies it**, not to its name,
so two things that behave differently never share one asset.

- **Models** carry their sampling parameters, so `temperature=1.9` is a different
  asset from `temperature=0.0`. Bound tool schemas are excluded (the belt is the
  agent's shape, and each tool is its own asset already), as is anything whose
  name reads as credential material — matched on whole words, so
  `max_completion_tokens` survives and `auth_token` does not.
- **Tools** are the source when `@eqty_tool` is applied, a name/description stub
  when it is not. Attach configuration and the payload carries the source
  alongside it, so one tool aimed at staging and at production is two assets.
- **Retrievers** fold in whatever you attach, because a class name alone cannot
  tell two corpora apart.

Anything attached with `with_config(run_name=..., metadata=..., tags=...)` is
folded into all three. The frameworks' own run-scoped keys are excluded
(`langgraph_*`, `ls_*`, `lc_*`, `checkpoint_ns`; `seq:step:N`, `graph:step:N`,
`map:key:*`, `langsmith:*`) — those encode a *position*, so folding them in would
give the same tool a different asset depending on where in the graph it ran, and
mint a new asset on every call.

**Practical consequence for instrumentation:** if the repo already builds two
retrievers or two model clients that differ only in configuration, attach that
configuration with `with_config` rather than leaving it implicit. It costs one
line and it is the difference between the manifest recording *which* corpus was
consulted and recording only that some retriever ran.

## Tool sources — the one thing callbacks cannot give you

A callback only ever carries a tool's **name**. Source cannot be recovered later,
so it has to be captured at definition time:

```python
from langchain_core.tools import tool
from eqty_lineage.langchain import eqty_tool

@tool
@eqty_tool
def search(query: str) -> str:
    """Search the knowledge base."""
```

The decorator returns the function unchanged and works on **either side** of
LangChain's `@tool`. It records the source with `inspect.getsource`, so it only
works for functions defined in real files — not a REPL, not `exec`'d code — and
the recorded source includes the decorator lines. A tool without it still gets a
Tool asset, just one registered from a name/description stub.

DeepAgents' built-in belt (`write_file`, `edit_file`, `task`, `execute`, …) is
built by its own middleware and is not yours to decorate. Their source is
readable, so apply the same function to the belt the compiled agent assembled:

```python
agent = create_deep_agent(...)
tools_node = getattr(agent.nodes.get("tools"), "bound", None)
for tool_obj in (getattr(tools_node, "tools_by_name", None) or {}).values():
    eqty_tool(tool_obj)
```

Upgrade DeepAgents and those assets change, which is the point. The handler does
not do this for you — reaching into a compiled graph is a caller's liberty, not
something a callback handler should assume — and a tool whose source cannot be
read falls back to its stub rather than failing.

`references/upstream/deepagents/research_agent.py:163` is the worked version, and it
guards both lookups (`agent.nodes.get("tools")`, then `tools_by_name`) rather
than indexing blindly. Copy the guards.

## `Path` values in state

`pathlib.Path` values in graph state are registered as their own Dataset asset
via `Dataset.from_path` (CIDing full directory contents) and linked into the
computation that carried them. **A string is not.** Keeping filesystem references
in state as `Path` is the opt-in, and it is a one-word edit with a large effect.

Versions are keyed on `(path, content CID)`, never on the path alone:

- **new content at a known path** → a new Dataset, recorded as an *output* of the
  computation that wrote it, with the version it replaced linked as an *input*.
  Successive edits form a chain rather than unrelated assets.
- **content already registered** → carried through as an *input*, never
  re-emitted as an output, which would put a cycle in the graph.

Keying on the path alone would resolve every later sighting to the first one's
asset, so anything running after a rewrite would be attested against content it
never saw.

## The virtual filesystem (DeepAgents)

A deep agent carries its filesystem in graph state, so without an extractor every
node's state asset embeds every file, once per node, and no file is ever an
entity in its own right. `VirtualFileExtractor` claims the `files` key and
replaces it with path → content CID, so the state says *which* files the node had
without containing them. It is installed by default; you do not add it.

**A file asset's CID is its content's CID** — the payload is the file's content
and nothing else, so it matches what any other tool computes for those bytes. The
path is metadata (`file_path`), attached unconditionally. Two consequences worth
knowing: identical content at two paths is *one* asset, and a file's identity is
comparable across runs and against a CID computed outside the package.

Writes are reconstructed from the **tool's arguments**, not from state, because
`StateBackend` applies writes through LangGraph's channel API — the new content
appears in neither the tool's result nor the node's output state. Left at that, a
file the agent wrote would be an input to everything that read it and an output
of nothing, which in a provenance graph says the run found it already there.

- `write_file` carries the content, so the version is registered directly.
- `edit_file` reports only success, so the result is derived from the version the
  edit was made against, with the backend's own occurrence rules re-checked
  first. If any check fails, **nothing is registered** — the file reappears at
  its next sighting as an input, which understates provenance rather than
  misstating it.
- `delete` drops the path's current version, so a later write chains to nothing.
- a failed call registers nothing, and failure is read from the result's
  `status`, not its wording — backends variously say "Error", "Failed to write
  file …", or relay the remote's message verbatim.

Paths are normalised exactly as DeepAgents normalises them (`validate_path`, step
for step): a leading slash is forced and `.` and interior `//` collapse, so a
model writing `report.md` creates `/report.md`. *Near* agreement would be worse
than none — a leading `//` is a different file under POSIX and stays different,
and a path `validate_path` refuses (`..`, leading `~`, a drive letter) records
nothing rather than being rewritten into a plausible path the call never touched.

**Files the run only reads.** Under a filesystem, store or sandbox backend, a
pre-existing file the agent never writes reaches neither the extractor nor the
write reconstruction. A successful `read_file` on a path with no known version
therefore registers what the tool *returned*, as an input, labelled as a
rendering rather than as the file: the result is line-numbered, chunked and
truncated, and that loss cannot be undone, so recording it under the file's own
identity would assert a content hash the file never had. A rendering never
becomes the version a later `edit_file` reconstructs against and never satisfies
a later read.

## The plan, and skills

`TodoListMiddleware` is **not** part of the default deep agent stack. It comes
from `langchain` and must be passed explicitly:

```python
from langchain.agents.middleware import TodoListMiddleware
create_deep_agent(..., middleware=[TodoListMiddleware()])
```

With it, each `write_todos` produces a Dataset holding that revision, chained to
the one it replaced. Without it the state key does not exist and nothing is
recorded — **no phantom plan appears**. A system prompt that tells the model to
"create a todo list" is not evidence the middleware is installed; several
upstream examples do exactly that and have no `write_todos` tool. If the repo's
prompt asks for a plan and the middleware is absent, say so in the patch notes:
adding it is a one-line change that records the plan with no other effect.

`SkillsMiddleware` puts its catalogue in `skills_metadata`, so skills are
captured under **any** backend. Each is registered as a `Skill` asset and
*carried* as an input — a skill is something the run was given, never something
it produced. The `SKILL.md` file itself is an ordinary virtual file, registered
when it is read.

Extractors claim a state key at the top of a state and inside the `update` of a
LangGraph `Command`, and deliberately nowhere else: a call's arguments and the
state it produces are serialised by the same code, so claiming the name anywhere
would register `write_todos(todos=[...])`'s new plan as an *input* to the call
that wrote it — reversing the one edge the plan's lineage exists to record.

## Backend completeness classes

Straight from `references/upstream/eqty-lineage-deepagents.EXAMPLES.md`. The backend changes how complete the record is, not
whether it works; nothing errors and nothing is misrecorded.

**A — default `StateBackend`.** The filesystem is in graph state. Full picture:
every file version chained write → edit → read, subagents via `task`, the skills
catalogue, each tool call its own computation.

**B — `FilesystemBackend`.** Files live on disk and never appear in graph state —
state keys are just `['messages']` and the extractor sees nothing. Recording
still works, because write attribution rides on tool arguments, which are
backend-agnostic:

```
write_file   out=[state, /report.md v1]
edit_file    in=[..., /report.md v1]  out=[state, /report.md v2]     ← chain intact
read_file    in=[..., /report.md v2]                                 ← links the version read
```

The manifest records the **virtual** path the agent used (`/report.md`), not the
real one (`<root_dir>/report.md`). That is what the agent saw, and a reader may
expect otherwise — worth a line in the patch notes.

**C — `CompositeBackend` / sandbox.** As B, plus `execute` recorded as an
ordinary tool computation: command in, output out. Only a sandbox or local-shell
backend implements `execute`; every other backend errors the call, and an error
changes nothing.

Its limit is real and belongs in the review notes: a shell can write files
without naming a path, so those writes get no version. A successful `execute`
drops the reconstruction caches, so a later `edit_file` registers nothing rather
than a version the file never held, and a later `read_file` registers the
rendering it actually got. A gap where a shell ran is the correct outcome. Under
the state backend the extractor re-registers the filesystem from the next state
it sees, so nothing is lost there.

## Handler lifetime

**One handler per conversation. Never one shared between conversations running at
once.**

Sequential reuse is deliberate and useful: across the turns of one checkpointed
thread it is what chains a file written in turn 1 to an edit in turn 3, rather
than leaving two unrelated entities. Build a fresh handler per turn instead and
files written earlier are registered on first sighting as inputs, unchained to
their real origin. Neither is broken; one handler per *conversation* is the
documented contract, and this is what it buys.

Concurrent sharing is a bug. File and plan versions are keyed per run — the plan
by nothing at all — so two runs in flight collide and the second run's write of
`/report.md` is recorded as a revision of the first run's file. The handler warns
when it sees a second run open while another is still running, but the fix is to
build it per run:

```python
async def execute_run(user_message: str) -> None:
    with graph_context(ctx):                  # eqty_sdk.context; a ContextVar, safe under asyncio
        await agent.ainvoke(
            {"messages": [HumanMessage(user_message)]},
            config={"callbacks": [EqtyDeepAgentsHandler()]},   # one per run
        )
```

The agent itself holds no lineage state and can stay shared.

**A retry loop is a concurrency question too.** Each attempt is a separate run, so
the handler goes inside the loop, not outside it.

## Options you may want, and one you probably don't

`verbose=True` attaches the raw LangChain callback context to every asset —
which callback produced it, `run_id` / `parent_run_id`, tags, the LangGraph
metadata dict. Values are sanitised for the graph explorer: non-JSON types are
stringified, dicts and lists JSON-encoded, `None` kept as `"null"` so a
present-but-empty key stays distinguishable from an absent one, and keys that
collide with SDK constructor kwargs prefixed `LC-`. Useful while developing the
instrumentation; it is what both worked examples use.

`StateExtractor` teaches the handler about a custom state key — it claims part of
a state, registers whatever assets represent it, and returns what stands in its
place. `key_path` is the sequence of dict keys that led to the value, so an
extractor claims a *key* rather than guessing from a value's shape; `sink.carry`
for an input, `sink.create` for an output, **never both** (re-emitting a carried
asset as an output puts a cycle in the graph); extractors run newest-first, so
yours beats the built-ins; one that raises is skipped rather than taking down the
run being observed.

Mode 1 does **not** write extractors by default. The built-ins cover files, plan,
skills and `Path` values, which is the whole of a framework-shaped app. Reach for
one only when a repo carries a domain entity in state that is genuinely its own
asset, and say in the patch notes why.

## Known limits

- **No in-process call site, no callbacks.** An agent running in a `langgraph
  dev` subprocess, on LangSmith's platform from an `agent.json`, or in a
  container has nothing to attach to. Deployment-model limitation; covering it
  needs a hook the runtime exposes. A module-level graph with *no* invoke is the
  recoverable case: import it and drive it from a script that has a call site.
- **Compaction is recorded, but not *as* lossy.** `SummarizationMiddleware` is in
  the default deep agent stack and overrides `before_model`, which LangGraph runs
  as a node, so it is recorded like any other — you can see *that* a compaction
  happened, on which turn, and what survived. What is missing is the *edge*: the
  SDK's vocabulary is `prov:used` / `prov:wasGeneratedBy`, so the step reads as
  an ordinary derivation, implying the output carries the input when most of it
  was dropped. No asset type fixes this and PROV, OpenLineage and in-toto do not
  model lossy derivation either. Raised with the SDK team; nothing here can fix
  it. It only fires on long runs.
- **Counts are not a contract.** Assets are content-addressed, so two state
  payloads that happen to coincide collapse into one registration and raw
  statement totals move by one between otherwise identical runs. Compare
  manifests **structurally** — which assets, which edges — never byte-for-byte or
  by count.

## Review checklist — print this with the patch

Three of these fail **quietly**. The run succeeds, the manifest is signed and
well-formed, and it describes something that did not happen. None of them is
caught by "it ran", so they have to be caught by reading the diff.

**Quiet failures — check these first**

- [ ] **Handler lifetime.** Is a handler constructed *at* each call site that can
      run concurrently — inside the `async def`, the request handler, the retry
      loop? A handler shared across concurrent runs records run B's write as a
      revision of run A's file. Sequential reuse across the turns of one
      checkpointed thread is correct and should be left alone.
- [ ] **`init()` scope.** Is it called exactly once at startup, and not per run?
      A second call is a silent no-op, so a per-run `init()` gives every run the
      first run's store while looking like it does the opposite.
- [ ] **Shell writes.** If `execute` is in the belt (sandbox or local-shell
      backend), the patch notes must say so: a shell can write files without
      naming a path, a successful `execute` drops the reconstruction caches, and
      those writes get no version. Prefer the file tools over shell redirection
      for anything you want attested. The gap is correct behaviour, but a reader
      who does not know it is there will read it as an absence of writes.

**Loud failures — but check anyway**

- [ ] **`config=` merged, not replaced.** Every pre-existing key still present:
      `recursion_limit`, `configurable`, thread ids, the caller's own callbacks.
- [ ] **Every attach point covered.** Compare the diff against `detect.py`'s
      list. An attach point with an `unresolved` binding needs a human to confirm
      it is a graph and not a model or a retriever.
- [ ] **No model or retriever `.invoke` was instrumented.** Callbacks there
      record one model call and nothing else.

**Completeness — what the manifest will and won't contain**

- [ ] **Placement — *decorate in place*.** Every `@eqty_tool` is on the repo's own tool definition, in the file it already lives in — no adapter, no wrapper written to hold it. The built-in belt is the one exception, applied to the live objects at runtime.
- [ ] **Tools.** Every tool the repo defines carries `@eqty_tool`; for DeepAgents
      the compiled-belt loop runs after `create_deep_agent(...)`.
- [ ] **`Path` state.** Filesystem references `detect.py` flagged are `Path`
      objects, and nothing downstream depended on them being `str`.
- [ ] **Backend class stated.** A / B / C, in the patch notes. Under B and C the
      manifest records the **virtual** path the agent saw, not the real one.
- [ ] **Plan.** If the system prompt asks the model to keep a todo list, is
      `TodoListMiddleware` actually installed? It is not in the default stack, so
      without it there is no `write_todos` and no plan asset — and the prompt
      makes it look like there should be one.
- [ ] **Nothing else decorated.** No node, subagent, middleware or `@compute`
      added anywhere.
