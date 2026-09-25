# eqty-instrument

The **write** side: take a repo that knows nothing about EQTY and produce a patch
that makes it emit a signed, content-addressed record of what ran. The read side —
verifying and reporting on a manifest that already exists — is `eqty-manifest/`,
and this skill hands off to it.

`SKILL.md` is the entry point and the thing an agent loads. This file is for
someone opening the directory.

## Two modes

**Mode 1** — the repo already uses LangChain, LangGraph or DeepAgents. The
framework fixed the node boundaries and EQTY ships handler packages that register
them, so the skill recognises and copies: initialise the SDK once, attach one
handler per invocation, decorate the repo's own tools, export. Same edit every
time. Procedure in `references/mode1.md`.

**Mode 2** — arbitrary Python, where no framework has decided which operations
deserve nodes. A procedure rather than a script: derive a control-flow graph
first, pick nodes auto or with the user, then instrument. Procedure in
`references/mode2.md`.

Detection is per module, not per repo — a real codebase is LangGraph in one place
and plain Python everywhere else, so a module matching nothing is reported as a
Mode 2 candidate, not a failure.

## Layout

| Path | What it is |
|---|---|
| `SKILL.md` | the skill itself — both modes, the correctness rules, the hand-off |
| `references/mode1.md` | Mode 1 rule by rule, plus the review checklist to print with the diff |
| `references/mode2.md` | Mode 2 — the CFG-first procedure, auto and HITL selection |
| `references/eqtysdk.md` | the SDK guide — required reading before a Mode 2 edit |
| `references/mode2ideas.md` | Mode 2's rules for which operations become nodes |
| `references/upstream/` | the `eqty-lineage` originals Mode 1 cites, vendored verbatim with `SOURCES.md` |
| `detect.py` | stdlib-only AST scan → JSON edit plan and human summary |
| `check_graph.py` | Mode 2's post-run graph checks on an emitted manifest |
| `isolated_cfg.py` | Mode 2 step 1: runs the CFG agents as fresh, isolated processes and checks nothing leaked |
| `examples/mode1/` | four real Mode 1 runs on stripped `eqty-lineage` examples — before, after, diff |
| `examples/mode2/` | pygit and llama, auto and HITL — before, after, diff, manifest |

The regression suite, its fixtures, the instrumented fixtures and the manifest they
emitted live outside the skill, in the repo's `tests/eqty-instrument/`. Inside the
skill folder they would be dead weight: they cannot run without this repo, so they
would ship only to fail. The suite is for developing the skill, not for using it: its run half needs this
repo's `.venv` and the companion `eqty-manifest` skill beside this one.

## Running it

```sh
python3 eqty-instrument/detect.py <path>          # human summary
python3 eqty-instrument/detect.py <path> --json   # the edit plan
```

Exit codes are the point: `0` a plan was produced, `1` Mode 1 applies but there is
no attachable call site, `2` no Mode 1 modules — everything is a Mode 2 candidate.
"Cannot be instrumented" has to be loud, because an empty plan otherwise looks
exactly like a repo that needed no changes.

```sh
.venv/bin/python tests/eqty-instrument/run_tests.py
```

Run it with the repo's `.venv` interpreter: the suite imports `eqty-manifest`'s
`parse_manifest` in-process, and its content check needs `eqty_sdk`. The
detection half is stdlib only. The half that runs an agent needs `eqty_sdk`,
`langchain` and `deepagents` from this repo's `.venv`; the suite finds an
interpreter that has them and shells out. If it cannot find one those checks
**fail** rather than skip — a suite that goes green without having run an
instrumented fixture has proved nothing.

Fixtures run standalone too, with no API key and no network:

```sh
.venv/bin/python tests/eqty-instrument/fixtures/deepagents_state/agent.py
```

## Three things that hold across the whole directory

**The output is a patch, not a commit.** Instrumentation lands in the working tree
on a branch; a human reviews and decides. Three of the Mode 1 correctness rules
fail *quietly*, so a wrong manifest still runs clean and still looks well-formed —
which is why the review checklist gets printed alongside the diff.

**Decorate in place.** Every computation carries a `Code` asset hashed from the
source of the function that ran, so the decorator goes on the repo's own
definition, in the file where it already lives. Applied to a new adapter
function it attests the adapter, leaves the real code unrecorded, and produces a
manifest describing a program nobody ships. The skill names this rule
*decorate in place* throughout; see `references/eqtysdk.md` §6.0.

**Assert structure, never counts.** Assets are content-addressed, so payloads that
happen to coincide collapse into one registration and raw totals move between
otherwise identical runs. Compare which assets, which edges, which computation
kinds — not how many.
