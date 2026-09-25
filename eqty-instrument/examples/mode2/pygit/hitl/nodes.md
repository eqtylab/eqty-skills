# pygit — HITL node selection (step 3)

Written before any code was touched. Mode 2, **HITL**.

## The user's pick (verbatim, from the user)

> "for pygit i just want this part: pack up the missing objects and POST them"

This selection came from the user at step 2. Everything below maps it onto the
L2 boxes of `cfg/pygit.cfg.html` and chains it; the placement in code is step 4's.

## The run this selection is against

As L1 stated it (carried verbatim to L2):

> Run: `pygit.py` `__main__` block, command sequence `init myrepo` → (cd myrepo) `add FILE...` → `commit -m MSG` → `push GIT_URL` (the create-repo-and-push-to-GitHub workflow from the README).

The user did not name a different run, so this is L1's. Only the **`push`** leg of
it carries nodes; `init`, `add` and `commit` run but record nothing.

**Uninstrumented paths** (L1's "Other entry points", plus the legs of the run the
pick does not cover):

- `pygit.py init`, `add`, `commit` — run in the example, no nodes (not picked).
- In `push`: credential resolution, `get_remote_master_hash()` (GET info/refs),
  `get_local_master_hash()`, `find_missing_objects()` and its walk
  (`find_commit_objects` / `find_tree_objects` / `read_tree` / `read_object`) — not picked.
- `pygit.py cat-file …`, `hash-object …`, `ls-files [-s]`, `status`, `diff` — not in the run, no nodes.
- Library use (`import pygit; pygit.push(url, user, pw)`): the same two nodes fire,
  but the caller must initialise the SDK and export; the CLI does that for itself.

## Mapping the pick onto L2 boxes

| User's words | L2 box(es) | Becomes |
|---|---|---|
| "pack up the missing objects" | **CP** `create_pack()`, **EP** `encode_pack_object()` (folded into CP) | node 1 |
| "and POST them" | **BL** `build_lines_data()` (folded, its bytes are an input), **HR2** `http_request()` POST, **OKd** response check (folded: the response is the output) | node 2 |

Not picked, left as a caller-side gap on purpose (mode2.md, *What auto and HITL
actually produce*): **FM** `find_missing_objects()` and everything above it. The
manifest will say *which* objects were packed, not *why those* were the missing ones.

## Nodes

### 1. Pack the missing objects — `create_pack()` · `computation_type: aggregate`

- **Archetype:** Aggregate (N git objects → 1 packfile). Caption: *"Packed the 4
  objects the remote lacked into one PACK v2 file."*
- **Inputs (really read):** each loose object file `.git/objects/xx/yyyy…` that
  `encode_pack_object()` → `read_object()` reads for this pack — one `Binary`
  asset per object, hashed from the file. Plus the `Code` of `create_pack` itself.
- **Output (really produced):** the pack bytes `create_pack()` returns — `Binary` "Packfile".
- **Why not `encode_pack_object()` as its own node:** per-object loop body with one
  caller (never list; tie-breaker "one caller → fold into caller").

### 2. POST the pack to git-receive-pack — in `push()` · `computation_type: emit`

- **Archetype:** Emit — the pack leaves the process; the remote holds it afterwards.
  Caption: *"POSTed the ref update and the packfile to /git-receive-pack; the server
  answered 'unpack ok / ok refs/heads/master'."*
- **Inputs (really sent):** the Packfile bytes (same bytes as node 1's output → the
  edge), the pkt-line ref-update command from `build_lines_data()`, and `push`'s `Code`.
- **Output (really received):** the receive-pack report-status response bytes.
- **Why BL is folded:** `build_lines_data()` is a pure function, no I/O (never list);
  its output is recorded as node 2's input where it is sent.
- **Why OKd is folded:** the asserts read exactly the recorded response; the gate's
  verdict is legible from that blob. No separate gate node.
- **Never recorded:** `username`, `password`.

## Chaining (HITL)

First pick = node 1, last pick = node 2. They chain directly: `push()` passes
`create_pack()`'s return value into the POST body, so the Packfile bytes are node 1's
output and node 2's input. **Nodes added to chain: none.**

Giving node 1 honest inputs is the hard part (mode2.md): it receives a `set` of SHA-1
ids, which records only identifiers. Instead of adding `find_missing_objects()` as a
node (the user did not pick it), node 1's inputs are the object files the packing
really reads. They will be roots.

## Prediction (written before the run)

The example adds **2 files**, so the push packs 4 objects (2 blobs, 1 tree, 1 commit).

- **Computations: 2** — "Pack missing objects", "POST pack to git-receive-pack".
- **Data nodes: 9** — 4 loose-object files, Code `create_pack`, Packfile, Code
  `push`, ref-update command, receive-pack response.
- **Connected components: 1.**
- **Roots: 7** — the 4 object files, the 2 `Code` assets, the ref-update command.
- **Leaves: 1** — the receive-pack response.
- **Shape:** a fan-in chain —
  `4 objects + Code(create_pack) → [pack] → Packfile → [POST] ← Code(push) + ref-update; [POST] → response`.
