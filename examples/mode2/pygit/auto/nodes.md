# pygit — node selection (auto)

**Path:** `pygit.py` `__main__` block, command sequence `init myrepo`, then (in
`myrepo`) `add FILE...`, `commit -m MSG`, `push GIT_URL`. Given with `--path` (then
called `--run`, which is why the CFG's first line reads `Run:`): the sequence L1
chose from the code in the earlier version of this example, kept so the two
versions describe the same path. Mode: **auto**.

**Not part of this path** (`../merged.md`): `cat_file`, `diff`, `get_status`,
`ls_files` and `status`, the other commands. They get no nodes; they still pass
through the SDK set-up, and any instrumented function they call records.

## CFG boxes → lineage nodes

Every L2 box is accounted for. Data-flow facts are from `../merged.md`.

| L2 box(es) | Archetype (mode2ideas) | Decision | Why |
|---|---|---|---|
| A `__main__` dispatch | CLI parsing | **no node** | Never: CLI parsing. SDK set-up and export happen at module level, before dispatch. |
| B `init()` | **Emit** | **NODE 1** | Writes the repository skeleton and `HEAD` to disk. |
| C `add()` | **Ingest** (Tier 1) | **NODE 2** | The working-tree files enter here: the provenance floor. |
| R `read_index()` | accessor | fold | Reads `.git/index`; the index is hashed where `add` and `commit` use it. |
| D `hash_object()` | loop body | fold | Called per object (`merged.md`: loop body); the object files it writes are recorded by `add` and `commit` as their outputs. |
| E `write_index()` | Transform, one caller | fold into NODE 2 | Its `.git/index` is `add`'s output. The data flow finds `write_index` → `read_index` via `.git/index`, which is the edge `add` → `commit`. |
| F `commit()` | **Transform**, the named stage | **NODE 3** | *"Committed the index as a tree and a commit, moved master."* |
| G `write_tree()` | Transform, one caller | fold into NODE 3 | The tree object is `commit`'s output. |
| L `get_local_master_hash()` | accessor | fold | The master ref it reads is hashed by the readers, `commit` and `push`. The data flow finds `commit` → `push` via `.git/refs/heads/master`. |
| M, M1 `get_remote_master_hash()` | **Ingest** (Tier 1, network) | **NODE 4** | The remote's state enters the process. |
| N, N1 `find_missing_objects()` | selection, one caller | fold into NODE 6 | Its result is exactly the set of objects NODE 5 packs. |
| K, T `find_commit_objects()`, `find_tree_objects()` | loop bodies | **no node** | Recursive walkers, below the stage level. |
| CP `create_pack()` | **Aggregate** (Tier 1) | **NODE 5** | N objects → 1 pack: the one place the lineage fans in. |
| EP `encode_pack_object()` | loop body | **no node** | Per object, inside `create_pack`. |
| BD, X `build_lines_data()`, `extract_lines()` | pure | **no node** | No I/O. |
| P `http_request()` | wrapper | **no node** | Wraps `urllib`, and carries the credentials. |
| H, H1 `push()` | **Emit** (Tier 1) | **NODE 6** | The pack leaves the process; the remote's reply is the output. |

The merge's other cross-checks: `write_file`, `read_tree` and `find_object` have
no box, and are I/O primitives inside the nodes above (no node). The commit and
tree bytes that `commit` builds inline and hands to `hash_object` are recorded as
`commit`'s object files.

Count test: six bullets — *initialised the repo; added the files; committed them;
read the remote's master; packed the missing objects; pushed them.*

## The nodes

| # | Node (`computation_type`) | Mechanism | Inputs recorded | Outputs recorded |
|---|---|---|---|---|
| 1 | `init` (emit) | `@compute`; returns an asset of the `HEAD` it wrote (no caller uses the `None`) | `Code(init)`; `repo` (path text) | `HEAD` |
| 2 | `add` (ingest) | `@compute` for `Code` and the CLI input, plus a builder for the files | `Code(add)`; each file's bytes as read; the prior `.git/index` if any; `paths` (path text) | each blob object file; `.git/index` |
| 3 | `commit` (transform) | `@compute` plus a builder | `Code(commit)`; `.git/index` as read; the parent master ref if any; `message` | tree object; commit object; `refs/heads/master`; the commit id |
| 4 | `get_remote_master_hash` (ingest) | builder: `@compute` would capture `password` positionally | `Code(get_remote_master_hash)`; the info/refs URL | the ref advertisement; the remote master id, when the remote has one |
| 5 | `create_pack` (aggregate) | builder: its argument is a `set` | `Code(create_pack)`; each object file packed | the pack bytes |
| 6 | `push` (emit) | builder: it returns a `set`, which callers may use | `Code(push)`; the master ref as read; the remote master id when not `None`; the pack bytes | the receive-pack response |

Every builder takes its function's source (`references/eqtysdk.md` §6.11). In
`add` and `commit`, `inspect.getsource` follows the decorator's `functools.wraps`
to the original function, so the builder's `Code` is the same text `@compute`
records: one node.

**Unknowns** (`merged.md`), and how the patch avoids depending on them:

- `remote_sha1`'s type (pyright: a `str` or `None`, partly `Any`): recorded as text
  when it is not `None`.
- `objects`' type (`set[Unknown]`): not recorded as a value at all; `create_pack`
  records the object files it packs.
- `http_request`'s return: recorded as the response bytes.

## Gaps (reported, not fixed)

1. **Path and identifier text:** `repo`, `paths` and the info/refs URL commit to
   their text, not to files (§6.7).
2. **`author` is a keyword argument,** so `commit`'s decorator doesn't record it (§6.1).
3. **Two computations each for `add` and `commit`,** sharing their outputs: the
   index and the commit id each have two producers.
4. **`init` is its own component:** nothing later reads `HEAD`.
5. **On a first push to an empty remote,** `get_remote_master_hash` returns `None`,
   so it hands nothing to `push` and is its own component too.
6. **Credentials are never recorded;** the push URL is.
