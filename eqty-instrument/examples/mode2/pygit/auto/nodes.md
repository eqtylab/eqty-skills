# pygit — step 3 node selection (auto)

Written before any code was touched. Mode: **auto** (chosen by the user at step 2).

## The run

As L1 stated it, carried to L2 verbatim (it came from L1, not from the user or from me):

> Run: `pygit.py` `__main__` block, command sequence `init myrepo` → (cd myrepo) `add FILE...` → `commit -m MSG` → `push GIT_URL` (the create-repo-and-push-to-GitHub workflow from the README).

Other runnable paths through the repo. These are **not instrumented** as runs: they get no nodes of their own. They do still pass through the SDK setup, and an instrumented function they call still records.

- `pygit.py cat-file {commit|tree|blob|size|type|pretty} HASH` → `cat_file()`
- `pygit.py hash-object PATH [-t TYPE] [-w]` → `hash_object()` on its own
- `pygit.py ls-files [-s]` → `ls_files()`
- `pygit.py status` → `status()` / `get_status()`
- `pygit.py diff` → `diff()`
- `import pygit` as a library. The same functions carry the same instrumentation, so a library call to `add`/`commit`/`push` records exactly as the CLI does, but no library run is exercised here.

## Filtering pass (`references/mode2ideas.md`), box by box over L2

| L2 box | Verdict | Why |
|---|---|---|
| M1 `__main__` parse, D1 dispatch | never | CLI parsing. SDK init and export go here, but as setup, not as a node |
| IN `init()` | **NODE** — emit | writes the repo skeleton and `HEAD` to disk. Caption: *"initialised empty repository myrepo"* |
| AD/AE `add()` | **NODE** — ingest | the working-tree files enter here. This is the provenance floor: the bytes the user typed |
| RI `read_index()` | fold (no node) | reads internal state (the index), not the outside world. 5 callers, but it is an accessor over a file. The index hand-off it performs is recorded by the stages that read it (add, commit), which hash `.git/index` at the point they use it |
| HO/HOd/HOW `hash_object()` | fold (no node) | per-object primitive, called inside add's per-path loop (a loop body below the stage level) and once each by write_tree/commit. Tie-breaker: parent and child both qualify → keep the parent. It is not Tier-1 Emit: the objects stay in the local store until push. The object files it writes are recorded by the parents (add, commit) as their outputs |
| WI `write_index()` | fold into add | one caller; the file it writes is add's output |
| CM/CT/CH `commit()` | **NODE** — transform | the named stage *"committed the index as a tree + commit, moved master"* |
| WT `write_tree()` | fold into commit | Tier 2 transform, one caller → fold into the caller; the tree object is commit's output |
| GL/GL2 `get_local_master_hash()`, CPd | never (accessor) | the ref-file hand-off it reads is hashed in the reading stage (commit for the parent, push for the local master) |
| PU/HR2/OKd `push()` | **NODE** — emit | the artifact leaves the process: the pack is POSTed to the remote. Caption: *"pushed 4 objects to the remote, which replied unpack ok"* |
| GR/GRd `get_remote_master_hash()` | **NODE** — ingest | the remote's state enters the process (network ingest, Tier 1). It is push's child and Tier 1, so both are kept and the nesting is deliberate |
| HR `http_request()` | never | a wrapper around urllib (instrument the inner call, which is stdlib, so fold into the callers). It also carries the credentials |
| EL `extract_lines()`, BL `build_lines_data()` | never | pure functions, no I/O |
| FM/FMd `find_missing_objects()` | fold into push | Tier 2 selection with one caller. Its result is visible as exactly the set of objects create_pack's node takes as inputs |
| FC/FCd/FT/FTd/RT/RO walkers and `read_object()` | never | recursive helpers and per-object reads, below the stage level |
| CP `create_pack()` | **NODE** — aggregate | N objects → 1 pack, the one place the lineage genuinely fans in. Tier 1, kept alongside its parent push |
| EP `encode_pack_object()` | never | per-object loop body inside create_pack |

**Six stages → nodes:** init, add, commit, get_remote_master_hash, create_pack, push. That passes the count test: it is the bullet list you would give a manager.

## Per-node placement plan (inputs, outputs, mechanism)

The SDK constraints that shape placement are in `references/eqtysdk.md` §6.1–§6.4 and §6.10:

- `@compute` sees only positional args.
- `bytes` and `set` are rejected as inputs and outputs.
- A `None` return raises.

pygit hands data between stages through `.git/` files, so file hand-offs are recorded with a `Computation` builder **inside** the target function that reads or writes the file, per §6.10.

| # | Stage (function in `pygit.py`) | Mechanism | Inputs recorded | Outputs recorded | computation_type |
|---|---|---|---|---|---|
| 1 | `init(repo)` | `@compute`. §6.10 None-return fix: return an asset of the `HEAD` file it just wrote (no caller uses the `None`) | Code(init), `repo` (path text → Custom, gap) | `HEAD` file bytes (Document) | emit |
| 2a | `add(paths)` — builder inside | builder, hashing at the point of read/write | each working-tree file's bytes as read (Document per file), prior `.git/index` if one exists | each blob object file written, `.git/index` as written | ingest |
| 2b | `add(paths)` — decorator | `@compute`. None-return fix: return the asset of `.git/index` it just wrote | Code(add), `paths` (list of path text → one Custom, gap) | `.git/index` (same CID as 2a) | ingest |
| 3a | `commit(message, author=None)` — builder inside | builder | `.git/index` as read (edge from add), parent `refs/heads/master` if one exists | tree object file, commit object file, `refs/heads/master` as written, commit id text | transform |
| 3b | `commit(...)` — decorator | `@compute`; return value unchanged (callers use the sha) | Code(commit), `message` (Custom; `author` is a kwarg so it is not captured) | commit id text (Custom, generated name — gap; same CID as in 3a) | transform |
| 4 | `get_remote_master_hash(git_url, username, password)` | builder only: `@compute` would capture `password` positionally | the info/refs URL text (identifier, gap) | the response bytes received (Document); the remote master id text when the remote has one | ingest |
| 5 | `create_pack(objects)` | builder only: its argument is a `set` (§6.10 row 3) | each object file packed (hashed from the object store, the files `encode_pack_object` reads) | the pack bytes (Binary) | aggregate |
| 6 | `push(git_url, ...)` | builder only: it returns `(str\|None, set)`, and a `set` output raises (§6.10 row 2). Library callers may use the set, so the return value is not changed | `refs/heads/master` as read (edge from commit), the pack bytes it sends (edge from create_pack), the remote master id when not None (edge from 4) | the receive-pack response bytes (Document) | emit |

The functions that have a builder but no decorator (4, 5, 6) carry no `Code` asset. That is a reported gap (ladder rung: builder inside the target function). Functions 2 and 3 carry both a decorator (`Code` + CLI inputs) and a builder (file content), so each appears as **two computations sharing an output**. That nesting is deliberate, and each shared output is reported as a multi-producer node.

## Chaining (both modes chain)

Edges that should form from data that really flows:

- add → commit: `.git/index` bytes, written by add and read by commit.
- add → create_pack: each blob object file.
- commit → create_pack: tree and commit object files.
- commit → push: `refs/heads/master` bytes.
- create_pack → push: pack bytes.
- 2a↔2b and 3a↔3b: shared outputs (index, commit id).

**Predicted missing edge:** get_remote_master_hash → push exists only through the remote master id. The README workflow pushes to a fresh, empty remote, so the id is `None` and nothing flows. The ingest node is therefore its own component in this run. init's `HEAD` is never read by any later pygit step, so init is its own component too.

## Prediction (written before the run)

The example adds two distinct files.

- **Computations: 8.** In registration order: init, add (builder), add (decorator), commit (builder), commit (decorator), get_remote_master_hash, create_pack, push.
- **Connected components: 3.** They are `{init}`, `{get_remote_master_hash}` and one main chain `{add ×2, commit ×2, create_pack, push}`.
- **Roots: 9.** The Code assets of init, add and commit; `repo`; `paths`; the two working files; `message`; the info/refs URL.
- **Leaves: 4.** `HEAD`, the commit id, the info/refs response, and the receive-pack response.
- **Multi-producer nodes: 2.** These are `.git/index` and the commit id.
- **Cycles: 0.**
- **Shape:** two singleton islands plus a DAG in which two working files fan into add. Add's index flows into commit. Blobs, tree and commit object fan into create_pack (4 → 1). The pack and the master ref fan into push, which ends at the remote's report.
