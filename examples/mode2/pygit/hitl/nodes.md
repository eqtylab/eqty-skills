# pygit — node selection (HITL)

**Path:** the same as auto, `init myrepo` → `add` → `commit` → `push GIT_URL`
(`../cfg/L2.md`). Mode: **HITL**.

**The user's pick, verbatim:** *"for pygit i just want this part: pack up the
missing objects and POST them"*

## The pick on the L2 boxes

| User's words | L2 box(es) | Becomes |
|---|---|---|
| "pack up the missing objects" | **CP** `create_pack()`, with **EP** `encode_pack_object()` folded in | NODE 1 |
| "and POST them" | **H** `push()`: the POST, with **BD** `build_lines_data()` and the **H1** response check folded in | NODE 2 |

**Chaining:** `merge_flow.py --pick CP,H` connects them directly: `push` passes
`create_pack`'s return into the POST body, so the pack bytes are NODE 1's output
and NODE 2's input. **Nodes added to chain: none.** Prediction: 2 functions,
2 computations, 1 component.

**Not picked, left as a caller-side gap on purpose:** `find_missing_objects()`
(N) and everything before it, `init`, `add` and `commit`. The manifest says
*which* objects were packed, not *why those* were the missing ones.

## The nodes

| # | Node (`computation_type`) | Lives in | Inputs recorded | Outputs recorded |
|---|---|---|---|---|
| 1 | Pack missing objects (aggregate) | `pygit.py:create_pack`, builder | `Code(create_pack)`; each loose object file packed | the pack bytes |
| 2 | POST pack to git-receive-pack (emit) | `pygit.py:push`, builder around the POST | `Code(push)`; the pack bytes; the ref-update command | the receive-pack response |

Both are builders: `create_pack` takes a `set`, and `push` returns one (§6.10).
Each takes its function's source as a `Code` input.

## Gaps (reported, not fixed)

1. **Why these objects:** `find_missing_objects` is not a node, so the objects
   packed are roots with no producer.
2. **The SDK is set up only in the `push` branch** of `__main__`, so `init`, `add`
   and `commit` record nothing, as the pick asks. A library caller of `push` or
   `create_pack` must set up the SDK itself.
3. **The push URL, old and new ids are in NODE 2's metadata,** so they are
   exported. Credentials never are.
4. **A failed push still exports,** from the `finally`; nothing in the manifest
   says it failed.
