# Mode 2 — the procedure

Arbitrary Python. No LangChain, no LangGraph, no DeepAgents — therefore no
callback surface and no handler to attach. **Nothing here is a recognise-and-copy
edit.** Mode 1's framework had already decided where the node boundaries were;
here nothing has, and the whole difficulty is the one question Mode 1 never has
to ask: **which operations deserve to be nodes.**

Answering it wrongly is expensive in both directions. Too few nodes and the
manifest cannot answer *"why did it go this way"*; too many and it is 400 rows of
`normalize_column` that no reader will ever read.

**Python only.** Every archetype and example assumes Python semantics — `def`,
decorators, `inspect`-readable source, an AST you can walk.

This file is the procedure. Two things it deliberately does not repeat:

| Read | Before | What it settles |
|---|---|---|
| `references/mode2ideas.md` | step 3 | **which** operations become nodes: six always-archetypes, three when named, the never list, tie-breakers, the caption and count tests |
| `references/eqtysdk.md` | step 4 | the SDK surface. **§6.10** (the rules for editing code to instrument it), **§6.11** (recipes for the types `@compute` can't hash: datasets, models, tokenizers, configs), **§6.0** (*decorate in place* — the decorator goes on the original definition, never a wrapper), **§6** (`@compute` and its other failure modes), **§11** (the checklist for the manifest the user's run emits), **§13.1** (the SDK is public, and a hard dependency — never shim it) |

Everything catalogued in `references/eqtysdk.md` produces a manifest that exports, verifies
and reads as **green**, so none of it is caught by finishing the step and
checking that it worked.

**Mode 2 has no `detect.py`.** Mode 1 is a script plus a procedure; Mode 2 is a
procedure and agent judgment, gated by a human at step 2 and reviewed as a patch
at the end. There is no deterministic edit to automate. **And like Mode 1, it
never runs the target** (SKILL.md): what the code does is read from the source,
by the CFG agents and by `static_flow.py`, and the user's run is what tests it.

---

## STOP — dispatch the CFG before you read any further

If you are about to do Mode 2 work on a target, **the first action is to send
fresh agents at the repo with these two prompts and nothing else:**

> **L1** — *"Give me a high-level control flow diagram of what happens in this
> program."*
>
> **L2** — *"Go one level deeper — show the major functions and the branches
> between them, but don't go into individual statements."*

**Both levels get their own isolated dispatch.** L2 is not a follow-up you ask
yourself after reading this file, and it is not something you derive from the L1
boxes — it is its own agent, under the same blindfold, given the L1 diagram as
context and nothing more. L2 is the level step 3 selects from, so it is the one
that matters most, and it is the one most easily bent by knowing what a node is
for.

Each agent gets the mechanical parameters only — the node budget, the output
path, and the file requirements in
[Step 1](#step-1--build-the-cfg-with-the-sdk-entirely-out-of-mind), plus, for L2,
the L1 diagram it is going one level deeper than. **Neither agent may see this
file, `references/mode2ideas.md`, the SDK, or the word "EQTY."**

**Dispatch them with `isolated_cfg.py`, never as in-session subagents:**

```sh
python3 <skill-dir>/isolated_cfg.py <target-repo> --out <scratch-dir> [--agent claude|codex] [--path "<the path the user named>"]
```

A subagent spawned from your session inherits that session's context:
- the repo path and its git status;
- recent commit messages;
- CLAUDE.md and memory;
- the skills list, which carries this skill's own description of what the diagram is for.

That is the contamination this stop sign exists to prevent, arriving through the
harness instead of through a file. The script starts each level as a separate,
fresh process — `claude -p` by default, `codex exec` with `--agent codex` (the
default when only `codex` is on PATH):
- **Where it runs:** a neutral temp directory outside any git repo, holding only a copy of the target.
- **What it gets under Claude:** no skills, no MCP servers, no settings or plugin hooks, no CLAUDE.md, and only the Read/Write/Glob/Grep tools.
- **What it gets under Codex:** a fresh, empty `CODEX_HOME` holding only a link to the user's login, so no user config, skills, MCP servers, plugins, hooks, memories or `AGENTS.md`; web search and the extra tool families off; the `workspace-write` sandbox, which enforces no writes outside the copy and no network.
- **What it checks afterwards:** under Claude, every run's init record and every tool call; under Codex, the home it ran with, every event type, every file write and every shell command. It writes `isolation.json` and exits non-zero if anything leaked.

**If it exits non-zero, or neither the `claude` nor the `codex` CLI is available, stop and report.** Do
not fall back to an in-session subagent and do not draw the CFG yourself.

**You do not choose which path through the program gets diagrammed.** A *path*
here is a way through the code (an entry point and, for a multi-command tool,
the command sequence), not something that executes: nothing runs in step 1. L1
chooses it from the code and states it; the script carries that statement to L2
so both levels describe the same path. Choosing it yourself is contamination: you have read this
file, so your pick would encode what a node is for — which is exactly what the
blindfold exists to keep out of the CFG.

Everything below this line is contamination for both of them. It is written for
*you*, the agent running the workflow, and every section of it — the archetypes,
the seed heuristic, `store=True`, the boundary rule — is an answer to *"what is
worth recording,"* which is **step 3's question, not step 1's.**

An agent that has read this file cannot draw an innocent CFG, and at L2 — where
the boxes are fine enough to bend without anyone noticing — it matters most. It
will merge two steps because they would share a node, round a loop boundary to
where a decorator would sit, and quietly drop the steps it knows would not
survive `references/mode2ideas.md`. The boxes come back already filtered, step 3 has nothing
left to choose from, and what you get is the agent's instincts wearing the
program's name. **You cannot tell this happened by looking at the CFG** — that is
what makes it worth a stop sign rather than a footnote.

So: dispatch first, keep reading second. The CFG lands while you read the rest of
this file, and step 2 shows it to the user.

**If a CFG already exists for your target, this does not apply** — read on and
pick up at step 2.

---

## Two different things are called "a graph". Keep them apart.

| | **CFG** — control-flow graph | **Lineage graph** — EQTY integrity graph |
|---|---|---|
| **What it is** | A diagram of what the program does, derived by *reading the source* | The DAG reconstructed from a manifest the *running program emitted* |
| **Produced by** | An agent reading code. No SDK, no execution | Instrumented code, actually run, exporting `manifest.json` |
| **Contains** | Boxes and arrows. Nothing cryptographic | Computations, hashed assets, signed statements, CIDs |
| **Role** | **Input.** The candidate node set, and what the user picks from | **Output.** The deliverable |
| **Named** | `<target>.cfg.html` | `<target>.<mode>.manifest.json` |

Every unqualified "graph" in `references/mode2ideas.md` means the **lineage graph**. The CFG
is never the deliverable — it is scaffolding that tells the agent (and the user)
where nodes could go.

Rule of thumb: **if it has hashes in it, it's a lineage graph.**

---

## Output layout

One directory per target. The names carry the step and the mode, so a target run
both ways reads side by side:

```
<outdir>/<target>/
├── <target>.cfg.html               step 1 — L1 and L2, one file, SDK-agnostic
├── <target>.flow.json              step 1b — the data flow, read from the source
├── <target>.merged.md              step 1c — the CFG joined with the data flow, shown at step 2
├── <target>.auto.nodes.md          step 3 — the selection, auto
├── <target>.hitl.nodes.md          step 3 — the selection, HITL
└── <target>.<mode>.prediction.md   step 6 — what the user's run should emit, and how to check it
```

No manifest is in this directory: the skill never runs the target (SKILL.md),
so the lineage graph comes from the user's run, at the path step 6 names.

`<mode>` is **`auto`** or **`hitl`** and nothing else. Instrumented source goes to
a **tracked** path — `<instrumented>/<target>/` for auto,
`<instrumented>/<target>-hitl/` for HITL — never into a gitignored vendored
checkout, where a patch is lost. Each holds the patched source, a
`<target>.<mode>.patch` against the pristine target, and `run_example.py`.

**The patch adds no new source file to the target.** Instrumentation is edits to
files that were already there. A `lineage.py`, an `eqty_nodes.py` or any other
module created to hold decorated functions is the step-4 failure in its most
visible form — every node in such a file attests that file, which the target does
not have. The only files the patch introduces are the ones outside the target's
own source: `run_example.py`, any stub it needs, and the dependency file if the
repo had none.

**The manifest the user's run writes ships exactly as the SDK exported it — one copy, unedited.**
There is no repaired or context-embedded second copy: the read side resolves
the W3C contexts from `eqty_sdk` itself (`integrity.py`'s `verify_vc` /
`verify_statement`), so a manifest does not have to carry them to be checkable.
A post-export step that rewrites the emitter's output is a skill patching its
own emitter, and a second copy on disk is a second thing to keep in sync.

---

## The workflow

**The order matters: the CFG is built before the SDK is anywhere in scope, node
selection is its own step, and only then is any code touched.**

### Step 1 — Build the CFG, with the SDK entirely out of mind

Point fresh agents at the target repo and have them derive a control-flow graph
by reading the source — one per level, two per target, each a separate isolated
process started by `isolated_cfg.py` (see the stop sign above). The script holds
the two prompts and the mechanical parameters below verbatim; L2's `Path:` line is
carried over from L1 by the script, not by you. Keep its `isolation.json` beside
the CFG as the evidence that both agents were blind. **The CFG is an output
artifact**, written to `<outdir>/<target>/<target>.cfg.html`: a self-contained
HTML file, no external assets.

**The prompts are fixed. There are two of them, one per level, and each goes to
its own isolated agent.** Verbatim:

| Level | Prompt | Budget |
|---|---|---|
| **L1** | *"Give me a high-level control flow diagram of what happens in this program."* | ~9 boxes |
| **L2** | *"Go one level deeper — show the major functions and the branches between them, but don't go into individual statements."* | ~30 boxes |

Each is followed by the same paragraph saying what the diagram is for, in
neutral words that name neither the project nor the tool (`PURPOSE` in
`isolated_cfg.py`): *"someone will use this diagram to answer, for any output
the program produces, 'where did this come from?' — which files were loaded,
which inputs and settings shaped it, and which steps transformed it."* With it,
independent agents draw diagrams that agree more (L1 Jaccard 0.92 against 0.81
without it, over 8 runs each).

The L2 agent is given the L1 diagram as context — *"one level deeper"* has to be
deeper than something — and nothing else. It is a second dispatch, not a second
turn you take yourself once you know what the nodes are for.

L2 carries its own definition of a box, and the prompt states it: **a major
function, or a branch between major functions.** Individual statements are not
boxes. If a box would be one line of code, it belongs inside its caller. That
clause is what stops L2 sliding into L3 — the budget alone does not do it, since
an agent will happily spend 30 boxes on the first two functions.

**The entry point is L1's choice, not yours — and for a multi-command tool the
*sequence* is too.** The same source gives different graphs depending on which
command it starts from, which is why the choice is recorded rather than assumed:

- **L1 states the path it diagrammed** as the first line of its output, `Path:` —
  which file or function starts it, and for a multi-command tool the command
  sequence. Require that line; it is what makes the graph interpretable at all.
- **The script carries that line verbatim into the L2 dispatch**, so L2 goes
  deeper on the same path rather than picking its own. This is the only entry-point information
  that ever flows from you to an agent, and it originates with L1, not with you.
- **Step 3 records it** at the top of the node-selection doc, together with the
  other runnable paths through the repo, named as uninstrumented. A reader must
  be able to see what the manifest does *not* cover.
- **If the user has said which path they care about, pass theirs through
  instead** (`--path`), and say in the selection doc that it came from the user. That is the
  user's decision to make; it is not yours.

**The deliverable is a diagram — boxes and arrows, drawn.** Not an annotated
list, not a document with a paragraph per step. Mermaid `flowchart TD` inside
`<pre class="mermaid">`, with the mermaid bundle **inlined** so the file renders
from disk with no network. Box labels are short: a function name and a
half-sentence, the length of *"_cast() — turn the return value into bytes"*.
In the Mermaid source, every node label and every edge label goes in double
quotes (`A["…"]`, `A -->|"…"| B`), with no backticks: an unquoted `(` in an edge
label, or a backtick right after a quote, stops the diagram from rendering.
Anything that needs a paragraph belongs in a reference table underneath the
diagram, not in the box.

This is worth stating because it is the failure mode. Asked for a control flow
diagram, an agent will happily return a beautifully formatted **essay about**
control flow, one box per section heading, and it is useless for step 3 — you
cannot see the shape of the program in it, which is the entire point.

Give each one a **node budget, not an adjective**. "High-level" means nothing to
an agent; "about 30 nodes" produces a usable graph. Emit **L1 and L2 in the same
file**: L1 is the one a person reads first, L2 is the one step 3 selects from.
Two dispatches, one output file.

**Show the prompts, not just their answers.** The file opens with the two prompts,
side by side, one card per level. Each card is headed with its level and budget
(`L1 · ~9 boxes`, `L2 · ~30 boxes`) and quotes that level's prompt **verbatim**,
exactly as the agent received it. Under the quote, note the one extra thing each
agent was given: the L2 card says it also received the L1 diagram and L1's `Path:`
line. Under the cards, two tabs switch between **L1 · high level** and
**L2 · one level deeper**. Each tab shows:

- the path that level diagrammed, from its `Path:` line;
- the diagram itself;
- the reference table, collapsed under a `<details>` summary.

A reader can then see what was asked beside what came back, and the prompt text
can be checked for a leaked word. Render every diagram before hiding the inactive
tab: mermaid lays out a hidden diagram at zero size.

**This step knows nothing about EQTY.** No SDK, no instrumentation, no manifest,
no execution — and, just as important, **no anticipating them**. This is
mechanical, not a matter of an agent being well behaved: each CFG agent is
dispatched with its prompt and the parameters, and **is never given this file or
`references/mode2ideas.md` to read.** Nor does it inherit them through the harness:
an in-session subagent would see your session's skills list, git status and
CLAUDE.md, which is why the dispatch is a separate process. See the stop sign
above.

Neither may bias its boxes toward what looks hashable, merge steps because they'd
share a node, or drop steps because they wouldn't survive `references/mode2ideas.md`. **This
is why L2 is dispatched rather than derived.** Deriving L2 from the L1 boxes
yourself, after reading this file, is the same contamination wearing a different
hat: you would be expanding exactly the branches you already know become nodes.

The CFG describes the program. What is worth recording comes next.

### Step 1b — Read the data flow from the source

While the CFG agents work, run the data-flow pass. It is a script, not an agent,
so it carries no judgment into the CFG, and it runs none of the target's code:

```sh
python3 <skill-dir>/static_flow.py <target-repo> --entry <entry file from L1's Path: line> \
    --out <outdir>/<target>/<target>.flow.json [--venv <the project's virtualenv>]
```

It follows every function the entry reaches: which one's result feeds which,
what is changed in place, what goes through a file, which parameters are paths,
what a bare `@compute` would raise on, and the `references/eqtysdk.md` §6.11
recipe each hand-off needs. Every fact is marked **certain**, **likely** (it rests
on the script's catalogue of library calls) or **unknown**.

**Pyright is the default** for types: it type-checks a copy of the source with
the project's installed libraries, and is run with no Python interpreter on its
path, so it executes nothing. Point `--venv` at the project's environment if it
is not `<repo>/.venv` or `venv`; with none, library types stay unknown.

- **If pyright isn't installed,** the script stops and says so. Ask the user to
  install it (`npm install -g pyright`; the npm build, since the pip wrapper is
  itself Python). Don't install it yourself.
- **If they decline,** rerun with `--types ast`. The script's own catalogue then
  types what it knows, and everything else stays unknown. Say which one was used:
  the report's first line names it.

**Never show the data-flow report to the CFG agents,** and don't start them
after reading it: it is an answer to "what flows where", and it would bend their
boxes. They run from the stop sign above, blind; this runs beside them.

### Step 1c — Join the CFG and the data flow

Once both exist:

```sh
python3 <skill-dir>/merge_flow.py <cfg-dir> <outdir>/<target>/<target>.flow.json \
    --out <outdir>/<target>/<target>.merged.md
```

It joins each L2 box to the function its "where it lives" column points into,
and writes:

- **the annotated L2 diagram:** the CFG's boxes and arrows, with each function's
  box coloured by whether a bare `@compute` works there, naming the recipes it
  needs, and dashed arrows for the hand-offs the CFG didn't draw. The one that
  matters most is a value **changed in place**: the CFG draws the model going
  from where it was built to where it is saved, while the version saved is the
  one training changed. The arrow starts at the step that changed it;
- **cross-checks:** a function the data flow reaches with no box (the CFG missed
  it), a box naming no reached function (dead code, dispatch the source can't
  follow, or nothing real), and a value made inline in a caller, an ingest with
  no node unless it is recorded inline;
- **the auto prediction:** functions, computations counting call sites, and
  components.

Add the annotated diagram to `<target>.cfg.html` as a third tab after L1 and L2,
**L2 · with the data flow**, so the user sees it at step 2.

### Step 2 — Ask the user: auto or HITL

**This is the one interactive gate. Everything after it runs to completion.**

**Don't ask when the answer is already known:**

- **The request already names it** ("instrument this in auto mode", "let me
  pick the nodes"): use that choice, and say so in one line when you show the
  CFG.
- **No one can answer:** a headless run (`codex exec`, `claude -p`, a CI job)
  or a request to work without questions. Take **auto**, and record in
  `<target>.<mode>.prediction.md` that it was taken because no one could be
  asked.

Otherwise, show the CFG — both levels, and L2 with the data flow — and ask, with
the one-line summary at the top of `<target>.merged.md` so the user can judge
whether picking by hand is worth it, in these terms:

> *8 functions, 9 computations, 2 components; 1 hand-off unknown, 2 cross-checks
> to review.*
>
> **Auto** — I take every node the CFG proposes and instrument all of them. You
> review the patch at the end. Nothing to decide now.
>
> **HITL** (human in the loop) — you pick which boxes on this diagram matter, plus
> any step you care about that the diagram didn't surface. I chain your picks
> into a connected graph and instrument those. Fewer nodes, and they're the ones
> you asked for.

Do not ask anything else before this point and do not ask for node-level input
until the user has chosen HITL. **If the user picks auto, the skill must not ask
"should I hash this parameter?"** — a novice cannot answer that about a library
they do not know, and being asked is the failure the auto path exists to prevent.

If the user does not choose, default to **auto**.

### Step 3 — Select the lineage-graph nodes

**Still no code touched.** The output of this step is an explicit, written-down
list: which CFG boxes become lineage-graph nodes, what each one's inputs and
outputs are, and why. Write it to `<outdir>/<target>/<target>.<mode>.nodes.md`
so the selection can be reviewed and argued with *before* a patch exists.

**Open it with the path the CFG describes, as L1 stated it**, and with the other
paths through the repo listed as uninstrumented. The selection is only
meaningful against one path through the program, and a reader who cannot see
which one — or which ones were left out — cannot tell a scoping decision from an
omission.

This is where EQTY enters. Apply `references/mode2ideas.md` — the archetypes, the never
list, the tie-breakers, the caption and count tests — and then the mode decides
what is filtered:

**Auto** — take **every node the CFG proposes**. No human in the loop, no
narrowing by preference. The point of auto is that the guidance carries the
judgment.

Seed heuristic for what matters: **anything written to disk is important;
intermediate Python objects probably are not.**

**HITL** — the user picks nodes off the CFG, plus any other step they care about
that the CFG didn't surface. Then do the part that makes HITL hard: **chain the
user's first node to their last.** A hand-picked set fragments — you cannot
record a tree object without the steps that built it — so fill in the
intermediate nodes needed to keep the lineage graph connected end to end, and
**list which ones you added and why.** `merge_flow.py ... --pick <box,box,...>`
does the chaining on the data flow: it adds the functions the picks need to
connect (`added train: carries model between evaluate and save_adapter`) and
predicts the result. The user picked the nodes, not the call sites; placement is
still yours (step 4).

**HITL may also ask about unknowns, auto never does.** After the picks, ask only
about the `unknown` facts the merge lists that change the selection's edges or
component count, in terms of the user's program ("`load()` returns what
`json.load` gives it: a dict of rows, or a list?"), each with the default auto
would take. Auto takes those defaults silently and lists each one in the
prediction file as an assumption.

**For an unknown, prefer a recipe that doesn't depend on the answer.** A dict and
a list hash the same way through `from_object` in a builder; only `@compute`'s
list expansion tells them apart (`references/eqtysdk.md` §6.4). Where no such
recipe exists, take the likelier reading and state it.

**Both modes chain.** Chaining is not HITL's special burden — see the rules
below. Whichever mode you are in, **write down the expected node count, root
count and graph shape**, starting from the merge's prediction. The selection is a prediction, and the manifest from the
user's run is what tests it; a prediction that misses is the signal that step 3
was wrong, and it is invisible if you never wrote it down.

### Step 4 — Instrument the repo with the EQTY SDK

**Read `references/eqtysdk.md` before you write a line of this** — especially **§6.10**,
**§6.0**, **§6** and **§13.1**. Work from the node list, not from the CFG: patch
**the target's own files, in place**, so that running the code emits a manifest.

**Import `eqty_sdk` at module top level and let it fail if it is missing.** No
`try/except ImportError`, no asset helper that hands back a plain value when the
SDK is absent — a shimmed run that emits nothing is indistinguishable from one
that worked (§13.1). **And never define your own compute decorator**: apply
`eqty_sdk.compute` directly, with its metadata written inline at each decorator.
No wrapper *around* the decorator, which is one more place instrumentation can be
silently switched off, and no helper that builds names or metadata. And **add `eqty_sdk==<version>` to the target repo's dependency
file** as part of the patch: instrumenting it added a runtime dependency, and a
repo whose `requirements.txt` does not say so does not install into a working
state for the next person. The output is a **reviewable patch**, not
a manifest; the user reads and tunes the emitted code, and that review is the
safety net that makes non-deterministic generation acceptable.

The node list said *what* deserves a node. This step works out **where in the
code that node actually lives**: which of the target's **own** functions carries
the decorator, and what metadata the decorator attaches so the graph reads without
opening every object. Names, descriptions and `computation_type` go in the
decorator's `metadata`, which costs no change to the function.

***Decorate in place*: `@compute` goes on the original function definition, in
the file where that function already lives. Never on a wrapper.** Not on a new function that calls
it, not on an `eqty_*` twin appended to the bottom of the file, not in a new
module beside it. The reason is the point of the whole exercise: the SDK is not
only recording a lineage graph, it is recording **provenance of the repo's own
code**. `@compute` hashes the decorated function's source as that node's `Code`
asset, so a reader can check the code the manifest names against the code in the
repository. Decorate an adapter and the manifest faithfully attests a function
the instrumenting agent invented — one that exists in no repository anyone will
check it against — while the function that does the work stays uninstrumented
and every normal caller emits nothing. The graph has the same shape either way
and verifies green either way.

**Read `references/eqtysdk.md` §6.0 and §6.10 before placing a single decorator.** When an
original genuinely cannot take a decorator, work down this ladder: an allowed fix
from §6.10; then `to_eqty_asset()`; then the `Computation` builder **inside the
target function**, recording what that function actually reads and writes, **and
its own source as a `Code` input** (`inspect.getsource`, `references/eqtysdk.md`
§6.11): a builder records only what it is given, so without that line the node
attests the data and not the code; then a reported gap. **The bottom rung is a
reported gap, never an adapter.**

***Edit to instrument, but preserve behavior and record only real data flow.***
The instrumented file is the program that ships, so the manifest honestly attests
"original + EQTY code". Edits are allowed within `references/eqtysdk.md` §6.10's three rules:

1. **Preserve behavior.** Every caller, including the target's own command line,
   gets the same results and side effects as before.
2. **Record only what really happened.** A recorded input was actually read or
   received by that step; a recorded output was actually produced by it, hashed
   from the bytes in hand at that moment.
3. **No naming helpers.** Add no function whose job is to build, name or type
   assets. Register assets inline, where the data is read or written.

That allows, for example:

- a function that returned `None` returning a record of an output it really
  produced, such as an asset of the file it just wrote;
- a `Computation` builder inside a function, hashing a file at the point the
  function reads or writes it. This is how a hand-off through the filesystem, like
  `add` writing `.git/index` and `commit` reading it, gets an **observed** edge;
- initialising the SDK and a signer in the target's own entry point, so the
  shipped program records its own runs. The signer is the VComp notary when
  `EQTY_NOTARY_URL` is set and a local key otherwise (`references/eqtysdk.md` §2).
  Keep the SDK directory, and with it the signer's private key, out of the user's
  tracked files.

It forbids:

- changing a parameter list, or a parameter's type to an asset, so the decorator
  records it;
- adding a parameter or return value the function does not use, to create an
  edge. That edge is asserted, not observed;
- changing a return value callers use, unless every caller behaves identically;
- naming helpers, and any other new asset-building functions;
- switching instrumentation off for some entry points.

Name every edit in the patch, with the rule that allows it. What still cannot be
recorded goes in the patch as a **reported gap**: path-text or identifier inputs,
`Custom` nodes, generated names, and missing edges.

The rules below are what this step gets wrong. Work them as a checklist.

### Step 5 — Write a small runnable example, and don't run it

Instrumented code that never runs emits nothing, and the skill never runs it
(SKILL.md). So give the user something small to run: a **self-contained example**
that exercises the instrumented path and produces a manifest — scripted, no API
keys, deterministic where possible, and matched to the path L1 diagrammed (the
entry point and command sequence stated in the CFG). Where the target's own
entry point already does that once instrumented, name it instead and write no
example. Write the file; never execute it, import it, or run the target to test
it.

**Nothing the skill writes is ever instrumented.** `run_example.py`, scripted
backends, stubbed remotes, harnesses, fixtures — every file that exists because
this skill created it is scaffolding, and scaffolding is not the program under
attestation. It initialises the SDK, calls the instrumented target with the
argument types the target's own callers use, and exports. It carries no
`@compute` of its own and no `Computation` builder of its own; builders belong
inside the target's functions (step 4). A node on a stub is worse than a missing node,
because it reads as evidence about the target and is evidence about a file
written ten minutes ago to make the target runnable.

The test is mechanical, and it is the same one step 4 uses: **if the file is not
in the pristine target, it gets no nodes.** The example calls the target's
functions with the same argument types its own callers use. Passing assets into a
function whose original code expects plain values would itself require the
signature change step 4 rules out.

Where a real run needs weights, a GPU, credentials or a network peer, **stub the
boundary, not the logic**: a scripted backend or a local stand-in, so the real
control flow, the real node set and the real chaining all execute. Say plainly in
the patch what was stubbed — **a manifest over a stubbed component is not a claim
about that component.** If a real run turns out to be required, that is a
hardware or credentials matter for the user, not something for the skill to try.

### Step 6 — Hand over the prediction and the checks

Write `<target>.<mode>.prediction.md`, and put the same in the patch. It holds:

- **the command** that runs the example (or the entry point), and the manifest
  path it writes;
- **the prediction** from step 3: computation count, component count, roots and
  shape, from `static_flow.py`'s report restricted to the selected nodes;
- **the signer:** the VComp notary where the deployment sets `EQTY_NOTARY_URL`,
  so `operatedBy` is the notary's DID and the manifest carries its hardware
  credentials; a local key where it doesn't;
- **the assumptions:** every fact the static pass marked `likely` or `unknown`
  that the placement rests on, each with what the patch assumed, so the user
  knows what their run is testing;
- **the checks for that manifest:** `eqty-manifest`'s `summary.py`, then
  `<skill-dir>/check_graph.py <manifest> --expect-computations N
  --expect-components N`, then the `references/eqtysdk.md` §11 checklist.

Say plainly that nothing was run and the patch is unverified until it runs where
it is deployed. **Don't ask the user to run it and don't wait for a manifest:**
that run often needs a cloud deployment that doesn't exist yet. The step ends
when the prediction file and the patch are written. If a manifest is handed to
you later, working the checks is fine: they read it and execute none of the
target's code.

**A green verifier is not the finish line, for them or for you.** Work the
checklist in `references/eqtysdk.md` §11. A manifest can verify perfectly while
the node labelled "checkpoint" contains a temporary file path, or while a `Code`
blob holds an adapter instead of the target's function. Read the node names and
the `Code` blobs.

**What the emitter embeds.** `eqty_sdk`'s exporter omits the W3C JSON-LD
contexts its credentials reference, so a fresh manifest verifies **`0 / N`** from
its own contexts alone. That is a property of the exporter, not a defect to
repair here. Hand it to the read side as-is: `verify_vc` and `verify_statement`
in the SDK's `integrity.py` carry those contexts compiled in and resolve them
offline, so the signatures verify with no post-export step and no second file.
**Export once, and export nothing but the manifest.**

**When a manifest exists, its connectivity is compared with the prediction.**
Every component beyond the one predicted is either a node-placement mistake to fix
or a missing edge to report. Never fix it by changing the target's signatures or
return values.

---

## Rules that fail quietly

Each of these produces a well-formed, signed, fully verifying, **wrong** manifest.
None is caught by the run succeeding. Every one was found by running this
procedure, not by reading the SDK.

- **A node whose `Code` asset is an adapter attests nothing about the repo.**
  `@compute` hashes the decorated function's own source, so decorating a new
  function that calls the repo's real one produces a manifest committing to code
  the instrumenting agent wrote and the user does not ship — while the real
  function stays uninstrumented, so every caller outside the example emits
  nothing, silently. Both graphs have the same shape, the same names and the same
  edges, and both verify green; the only way to see it is to open the `Code` blob
  and read it. *Decorate in place* (`references/eqtysdk.md` §6.0).
- **A green manifest can commit to nothing at all.** Pass a file as
  `str(path)` and `@compute` hashes **the path text, not the file**. The manifest
  commits to a filename, the CID changes whenever the path does, and the node
  types as `Custom`. It verifies green throughout, and nothing in the read side
  can catch it — only reading the node labels does. Where the code already passes
  paths, do not change the parameter to take contents. Record the file's bytes
  with a builder inside the function, at the point it reads the file; the
  path-text node that `@compute` still records is reported as a gap.
- **`@compute` over plain values gives `Custom` nodes with generated names.**
  Positional arguments that are not already assets become `Custom` nodes named after
  the parameter, and plain return values become `Custom-<4 chars>`. That is valid
  SDK output and an honest record of what the function handles. Do not change
  parameter lists to get types and names (`references/eqtysdk.md` §6.10). Describe the
  computation in the decorator's `metadata`, and give typed, named records only to
  data you register inline where it is really read or written.
- **Equal bytes are one node, whatever each call site calls it.** Two
  registrations of the same content share a CID, and each can carry its own label.
  This is a readability issue, not an integrity failure. **Never add a naming
  helper to fix it**; the skill does not use them.
- **Identifiers and selections read ambiguously.** A function that passes id
  strings, or returns objects it selected rather than created, records exactly
  that: the ids, or those objects as outputs. Equal strings give equal CIDs, so an
  id that recurs in several roles can link nodes that are not otherwise related.
  Where the function reads the artifact an id names (a git object file, say),
  hash those bytes with a builder at the point of reading. Otherwise report it.
  Never change what the function passes or returns to swap ids for artifacts.
- **Chaining comes from data that really flows.** An edge exists only where one
  recorded step's output equals a later recorded step's input. Where the code
  passes the value, place decorators so the call produces the edge. Where the
  hand-off goes through a file (one step writes `.git/index`, a later one reads
  it), record the file's bytes inside the writer as an output and inside the
  reader as an input; equal bytes make the edge. Never add a parameter or return
  value to create one. What cannot be observed is a reported missing edge, in both
  auto and HITL.
- **Two SDK shapes silently fragment a graph, and both look fine in review.**
  (1) A returned `list` becomes N assets but a list *argument* is hashed as one
  JSON blob, so a list-out/list-in pair never matches. (2) Only **positional**
  args become data nodes — load-bearing in both directions: it is how credentials
  and un-serialisable internals stay out of a manifest, and it is how a chain
  gets dropped by accident.

---

## Constraints

- **`store=True` everywhere for now — except heavy artifacts, declared.**
  `store` controls whether the preimage is captured as a base64 blob in the
  manifest. `store=False` with no other copy of the content means nothing can
  ever be verified — the hashes are dead. It costs roughly **2.5x** file size.
  The one exception is SKILL.md's *commit to heavy artifacts by CID*: model
  weights, large datasets, anything the manifest would otherwise become a copy
  of. Those take `_store=False` **with** `storage="by-reference"` and a
  `storage_reason` in the same registration, and are listed as a gap. Judge by
  what the target loads in real use, not by the stub the example runs on. An
  absent CID without that declaration is a defect, and the read side reports it
  as missing.
- **Compute nodes have no type label in the SDK.** Asset types apply only to
  hashable entities, so a computation cannot carry one. Put the CFG's own label
  on it as metadata — `computation_type`: `ingest`, `transform`, `model_call`,
  `aggregate`, `emit`, `decide`. This is the one remaining "unknown" in a
  finished graph and it is owed by the SDK, not by the patch.
- **Node granularity is unresolved.** For an instrumented function, which
  arguments become data nodes? Easily hashable args (a number, an op string) yes;
  a whole context/state object, unclear. In a CFG the boxes are compute and the
  data is implicit in the arrows; in a lineage graph the data nodes are real and
  have to be chosen. The agent decides, and annotates enough that the graph reads
  without opening every object.
- **Never instrument the stdlib or third-party libraries.** Don't follow imports
  outside first-party code. **The target itself is never "third-party" for this
  purpose**, however it was vendored: a pinned snapshot of an upstream repo is
  still the target, and its files are exactly where the decorators belong. This
  rule is about not following imports *outward* from the target, not about
  keeping your hands off the target's source. Reading it the other way is what
  produces a side module of adapters (step 4).
- **When a Tier-1 archetype falls outside that boundary, surface it — don't
  silently drop it.** The valuable node is sometimes in the caller (above the
  boundary) or inside a library, in another process (below it). Strict rules then
  give a graph with a hole where the most important node should be. Don't weaken
  the boundary rule; report the gap. Instrumenting by **process identity** rather
  than call site sidesteps the below-the-boundary case.
- **The skill's output is a patch**, not a manifest. Non-determinism in
  generation is acceptable *because the user reviews and tunes the emitted code*.
  That review is the safety net; keep the patch readable.

- **Nondeterminism is recorded, not removed.** A seed and the sampler
  parameters ride as metadata on the model-call node. That *records* what the
  run used; it does not make the run reproducible, and the patch should not
  claim otherwise.

---

## Review checklist — print this with the patch

- [ ] The CFG was drawn by agents that never saw this file, `references/mode2ideas.md` or the SDK — and L2 was **dispatched**, not derived from L1.
- [ ] Both CFG agents were started by `isolated_cfg.py`, not as in-session subagents. Its `isolation.json` sits beside the CFG, names the agent (`claude` or `codex`), and every check in it passes: no skills, no MCP servers, tools limited, every tool call inside the agent's own copy.
- [ ] `<target>.flow.json` came from `static_flow.py` on the entry L1 named, and its first line says whether pyright was used; if not, the user declined to install it. The CFG agents never saw it.
- [ ] `<target>.merged.md` exists, the user saw its annotated L2 diagram at step 2, and every cross-check in it is answered in the node list.
- [ ] At step 2 the user was asked auto vs HITL, with the merge's summary line, and asked nothing about nodes before it — or the choice came from the request, or auto was taken because no one could be asked, and the prediction file says which.
- [ ] A node list exists, in writing, and predates the patch.
- [ ] In HITL: the nodes added to chain the user's picks are listed, with why.
- [ ] Predicted node count, root count and graph shape are written down in `<target>.<mode>.prediction.md`, with every `likely` or `unknown` fact the placement assumed.
- [ ] **Nothing of the target was run, imported or executed**, the user was not asked to run it, and the handoff says the patch is unverified until it runs where it is deployed.
- [ ] `eqty_sdk` is imported at top level, unconditionally — no `try/except ImportError`, no plain-value asset fallback.
- [ ] No locally-defined compute decorator. `eqty_sdk.compute` is applied directly, with metadata written inline.
- [ ] **Every `@compute` sits on a function that exists in the pristine target** — same name, same file. No adapter, no `eqty_*` twin, no new module of decorated functions.
- [ ] The patch adds **no new source file to the target**. Instrumentation is edits to files that were already there.
- [ ] No node uses `Code.from_path` to point at "where the real logic lives" — that helper is an adapter confessing.
- [ ] **Every `Computation` builder takes its own function's source** as a `Code` input, `inspect.getsource(<that function>)`, right after `Computation.new(...)` — never another function's, never a file's.
- [ ] **Nothing the skill wrote is instrumented.** `run_example.py`, scripted backends and stubs carry no nodes; they call the target with the argument types its own callers use.
- [ ] Any node that could not be placed on an original definition is **reported as a gap**, naming which rung of the step 4 ladder was tried.
- [ ] **Every edit to the target is named in the patch with the `references/eqtysdk.md` §6.10 rule that allows it.** Behavior is unchanged for every caller, including the target's own command line, which still records its runs.
- [ ] **No naming helpers**, and no other new asset-building functions. Assets are registered inline where their data is read or written.
- [ ] No parameter list changed, and no parameter or return value added that the function does not use. Every edge comes from data that really flowed; a filesystem hand-off is recorded by builders inside the writing and the reading functions.
- [ ] The target repo's dependency file declares `eqty_sdk==<version>`.
- [ ] `Custom` nodes, generated names, path-text inputs, identifier inputs and missing edges that remain are **listed as reported gaps**.
- [ ] The SDK directory the entry point initialises, with its signer key, is outside the user's tracked files.
- [ ] The signer is chosen at start-up: the VComp notary when `EQTY_NOTARY_URL` is set, with no fallback if it can't be reached; otherwise a local key under a different name.
- [ ] Every node's `computation_type` metadata is set.
- [ ] Every `_store=False` registration carries `storage="by-reference"` and a `storage_reason`, is a heavy artifact by what the target loads in real use, and is listed as a gap. Nothing else is `_store=False`.
- [ ] Stubs and the example use relative paths, so no blob of the user's manifest will hold an absolute path or a user name (`references/eqtysdk.md` §11).
- [ ] The handoff gives the `check_graph.py <manifest> --expect-computations N --expect-components N` command with the predicted numbers, and the §11 checklist to work on the manifest.
- [ ] The example exports exactly one manifest, as emitted — no repaired or context-embedded second copy.
- [ ] Anything stubbed for the example is named in the patch, with what the manifest therefore does not claim.

## How to check the result, once the user has run it

- **Walk someone through the lineage graph step by step** — *this is the commit
  message I typed, this is the patch, and here it all is, captured.* If that
  walkthrough works, the graph is good.
- **Open a `Code` blob and read it.** Pick any computation, decode its `Code`
  asset, and confirm you are looking at a function from the target's own source
  and not at glue written to hold a decorator. This is the only check that
  catches an adapter, and nothing else in the read side will.
- **Paste the graph into a fresh LLM session** and ask what it can infer. That
  tests whether the metadata is doing its job.
- **Round-trip it against `eqty-manifest`**: build a manifest with one skill,
  read it with the other.

## What auto and HITL actually produce

A hand-picked slice is **not the auto graph minus nodes** — it is a different
graph with different roots. HITL keeps the steps the user cares about and drops
the ones that would say *why* those were the right values, so its provenance
floor is usually an ingest the auto run does not have. Its chaining pass may add
almost nothing: on a tight pick the hard part is not connecting the picks to each
other, it is **giving the first pick honest inputs**.

Gaps the auto run would fill in from the caller side — a missing Ingest above the
first node, a missing Emit below the last — are left open in HITL on purpose, so
they are visible in the graph as a root with nothing above it and an output with
nothing below it.
