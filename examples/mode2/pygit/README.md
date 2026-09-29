# pygit — Mode 2, auto and HITL

[benhoyt/pygit](https://github.com/benhoyt/pygit) (MIT, [`LICENSE.txt`](LICENSE.txt)),
a small git that can init, add, commit and push to GitHub, instrumented with the
current Mode 2 flow. **Nothing in it was run by the skill**: it read the code,
wrote the patch, and said what the patched program should record.

Both modes select from the same CFG and data flow:

| Step | File | What it is |
|---|---|---|
| 1 | [`cfg/L1.md`](cfg/L1.md), [`cfg/L2.md`](cfg/L2.md) | The control-flow graph of one path through the program, `init` → `add` → `commit` → `push`, drawn by two isolated agents ([`cfg.isolation.json`](cfg.isolation.json)). The path was given with `--path` (then called `--run`, so the files' first line reads `Run:`): the same sequence L1 chose on its own in the earlier version of this example. |
| 1b | [`flow.json`](flow.json) | The data flow, read from the source by `static_flow.py` with pyright. pygit uses only the standard library, so no environment was needed. |
| 1c | [`merged.md`](merged.md) | The CFG joined with the data flow: the annotated L2 diagram, the cross-checks, what is not part of this run, and the prediction for every box. |
| 3 | [`auto/nodes.md`](auto/nodes.md), [`hitl/nodes.md`](hitl/nodes.md) | The node selections: auto takes every box `mode2ideas.md` keeps; HITL takes the user's pick, *"pack up the missing objects and POST them"*. |
| 4 | `auto/changes.diff`, `hitl/changes.diff` | The patches: `pygit_before.py` → `pygit_after.py`, plus `requirements.txt` declaring `eqty_sdk`. |
| 6 | [`auto/prediction.md`](auto/prediction.md), [`hitl/prediction.md`](hitl/prediction.md) | How to run each, what its manifest should contain, the assumptions, and the checks. |

**What to look at first:** in `merged.md`, the file hand-off the source reveals.
`write_index()` writes `.git/index` and `read_index()` reads it; the data flow
finds it because both spell the path out the same way, and counts it for the
nodes that call them, so `add` → `commit` is an edge before any code is touched.
The same goes for `.git/refs/heads/master` from `commit` to `push`.

**No manifest here:** the skill doesn't produce one. The earlier version of this
example (≤ 0.1.11) ran the patched commands against a local stand-in remote and
kept the manifests; that version is in the repository's history.
