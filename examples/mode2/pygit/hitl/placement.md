# pygit — HITL placement (steps 4–6)

The node selection this implements is `../pygit.hitl.nodes.md`, written before
any code was touched. The user's pick came from the user, verbatim:
*"for pygit i just want this part: pack up the missing objects and POST them"*.

This is a **patch for human review**, not a commit. `pygit.hitl.patch` is
`diff -ruN repo pygit-hitl`.

## Versions and reproduction

| | |
|---|---|
| Python | 3.12.8 (`./venv/bin/python`) |
| `eqty_sdk` | 2.4.2 (installed wheel, `importlib.metadata`) |
| git (for the stand-in remote only) | 2.50.1 (Apple Git-155) |
| Signer | Ed25519, `Signer.load_or_create(name="pygit")`, created on first push |
| Storage | `_store=True` on every hash in the patch; `set_store_all_blobs` left at its default |
| Context | `Context.new("pygit push")`, the default context, created per push process |
| SDK directory | `example/myrepo/.git/eqty_sdk/` (inside `.git`, so not in the working tree and not tracked) |

From the workspace root:

```sh
cp -R repo pygit-hitl && (cd pygit-hitl && patch -p1 < ../out/pygit-hitl/pygit.hitl.patch)   # or use out/pygit-hitl as-is
cd out/pygit-hitl && ../../venv/bin/python run_example.py        # init → add → commit → push; moves the manifest to ../pygit.hitl.manifest.json
cd ../.. && ./venv/bin/python eqty-instrument/check_graph.py out/pygit.hitl.manifest.json --expect-computations 2 --expect-components 1
```

`run_example.py` deletes and recreates `out/pygit-hitl/example/` on every run. The commit
timestamp is real time, so every CID derived from the commit object changes between
runs. The run is not byte-reproducible, and nothing here claims it is.

## Every edit, with the rule that allows it

All edits are in `pygit.py`, the target's only source file. Rules are `references/eqtysdk.md` §6.10.

| # | Where | Edit | Allowed by |
|---|---|---|---|
| 1 | imports | Add `inspect` and a top-level, unconditional `import eqty_sdk`. No `try/except`. | §13.1 (the SDK is a hard dependency). Rule 1: importing adds no behaviour. |
| 2 | `create_pack()` | A `Computation` builder **inside the function**. Its inputs are `create_pack`'s own source (`Code`, via `inspect.getsource(create_pack)`) and each loose object file the pack is built from (`Binary.from_path(find_object(o))`). Its output is the returned pack bytes (`get_cid_for_bytes(data)` → `Binary.from_cid`, "Packfile"). The computation is finalised before `return data`. The packing lines themselves are untouched. | Rule 2: the files are the ones this step reads through `encode_pack_object`→`read_object`, and the output is the bytes returned. Rule 3: assets are registered inline, with no helper. Ladder rung 3 (builder inside the target function). `@compute` cannot go here: the positional `set` raises `TypeError` before the call and the `bytes` return raises after it (§6.3/§6.4), and a converted return would change what `push` concatenates. |
| 3 | `push()` | `data = build_lines_data(lines) + create_pack(missing)` split into `command = …; pack = …; data = command + pack`. | Rule 1: same calls, same order, same bytes sent. The pristine and patched `create_pack` produce identical bytes, checked below. |
| 4 | `push()` | A `Computation` builder around the POST. Inputs are `push`'s source (`Code`), the ref-update pkt-line `command` (`Document`) and `pack` (`Binary` "Packfile"), all registered before `http_request`. The output is the response bytes (`Document` "receive-pack report-status"). It is finalised right after the response arrives and before the asserts, so a rejected push is still recorded. Metadata holds `url`, `old` and `new` (the SHAs already in `command`). | Rule 2: those bytes were really sent and received. Rule 3: inline. `username`/`password` are never recorded. `@compute` on `push` would fail: the tuple return contains a `set`, so it raises `TypeError` after the push has happened. A library call `push(url, user, pw)` would also capture the password positionally. `http_request` is never decorated for the same reason. |
| 5 | `__main__`, `push` branch only | `init(default_context=Context.new("pygit push"), custom_dir=".git/eqty_sdk")`, then `set_active_signer(Signer.load_or_create(name="pygit"))`, then `push(...)` inside `try/finally`, exporting the default context to `.git/eqty_sdk/manifest.json`. | The mode2.md step 4 allowance: initialise the SDK and a signer in the target's own entry point. The SDK dir sits under `.git/`, which `get_status()` skips and `find_object()` never lists, so `status`/`diff`/`cat-file` are unchanged (checked below). The `finally` re-raises, so exit codes are unchanged. Other commands do not initialise the SDK because they run no instrumented code. That scopes setup; it does not switch any node off. |
| 6 | new `requirements.txt` | `eqty_sdk==2.4.2`. The repo had no dependency file. | mode2.md step 4 (declare the dependency). |
| 7 | new `run_example.py` | Scaffolding, with no nodes and no builder. | mode2.md step 5. |

No parameter list changed, and no return value changed. No function was added to
`pygit.py`: no adapter, no `eqty_*` twin, no naming helper. No `@compute` anywhere.
Both nodes are builders inside the repo's own functions.

## Reported gaps

1. **Library callers must initialise the SDK. This changes behaviour for anyone who doesn't.**
   `import pygit; pygit.push(...)` or `pygit.create_pack(...)` without `eqty_sdk.init()`
   plus an active signer now raises `pyo3_runtime.PanicException: Config not initialized`
   from inside `create_pack`, before any request is sent. That exception is a
   `BaseException`, so `except Exception` does not catch it. This follows from the hard-dependency rule,
   which allows no shim and no silent no-op. It is **not** "every caller behaves as
   before", so the matching review-checklist item is left unticked.
2. **Object files are hashed by a second read.** `create_pack` hashes each
   `.git/objects/xx/…` file with `Binary.from_path` just before `encode_pack_object`
   reads it again through `read_object`. They are the same files, but not the same bytes
   in hand, so this is not an atomic snapshot (§7.3). Each blob does decompress to a git object whose SHA-1
   equals its name (checked below).
3. **Why these objects were missing is not recorded.** `find_missing_objects()`, the
   `info/refs` GET and `get_local_master_hash()` were not picked. HITL leaves this caller-side
   gap open on purpose. The loose-object files are roots with nothing above them.
   The remote's prior state appears only as the `old` metadata string, which is an identifier.
4. **The object ids are identifiers.** `create_pack` receives a `set` of SHA-1 strings.
   They are carried only in asset names (`Loose object <sha>`) and in `object_count`, not as a
   data node. The claim rests on the hashed files.
5. **The response gate is not a node.** The asserts that read the report-status are
   unrecorded as a verdict. The recorded response blob (`unpack ok / ok refs/heads/master`)
   is what a reader checks. If an assert fails, the POST node is still recorded and still
   exported (`finally`), and nothing in the manifest says the push failed.
6. **`Code` covers only the two functions' own text.** The `create_pack` and `push`
   sources are recorded. Their callees (`encode_pack_object`, `read_object`,
   `find_object`, `build_lines_data`, `http_request`, `extract_lines`) are not (§6.0).
   `push`'s `Code` also covers the unpicked legs it contains.
7. **Published metadata:** the push `url` is in the computation metadata and is exported.
8. **One label registered twice:** "Packfile" is registered in `create_pack` (output) and
   in `push` (input). Same bytes, same CID, same name: two metadata statements, one node.
9. `check_graph.py` reported nothing: no generated names, no `Custom`, no path text, no
   multi-producer nodes, no cycles. No missing edges beyond items 3 and 5.

## Stubbed, and what the manifest therefore does not claim

- **The remote.** GitHub is replaced by a local HTTP server on 127.0.0.1 inside
  `run_example.py`. It passes each request to the **real `git http-backend`** over a bare repo in
  `example/remote.git` (`http.receivepack=true`). So the pack and ref update were accepted by
  real `git receive-pack`, and `git fsck --strict` passed on the remote, but not by GitHub.
- **Content-Type.** pygit POSTs with urllib's default
  `application/x-www-form-urlencoded`. The stand-in hands `git http-backend` the
  `application/x-git-receive-pack-request` it insists on. The manifest makes no claim that
  any particular server accepts pygit's request headers.
- **Authentication.** The stand-in never sends a 401 challenge, so urllib's basic-auth
  handler never sends the dummy credentials (`GIT_USERNAME`/`GIT_PASSWORD` = `example-*`).
  Nothing about authentication is claimed. The credential strings appear nowhere in the
  manifest (checked).
- The stand-in is scaffolding and carries no nodes. The report-status blob is what that
  local git said.

## Prediction vs result

| | Predicted (nodes.md) | Manifest |
|---|---|---|
| Computations | 2 | **2** — "Pack missing objects" (aggregate, 5 in → 1 out), "POST pack to git-receive-pack" (emit, 3 in → 1 out) |
| Data nodes | 9 | **9** |
| Components | 1 | **1** |
| Roots | 7 (4 objects, 2 Code, ref-update) | **7**, same set |
| Leaves | 1 (report-status) | **1**, same |
| Shape | fan-in chain via Packfile | as predicted |

The prediction matched on every count.

## `check_graph.py` output

```
== out/pygit.hitl.manifest.json
2 computations, 9 data nodes

-- every node as a reader sees it
  COMPUTE          POST pack to git-receive-pack      b''
  COMPUTE          Pack missing objects               b''
  Binary           Loose object 508ceb25d9126b53d07b8c34da45ae546e53cef4 b'x\x9c\x9d\x8d]\nB!\x10F{v\x15\xb3\x81bF\xcd\x1f\xb8D=\xb4\x10\xf5\x8e%$^\xcc\xa0v\x1f\xe4\x0ez<\x07\xce\xf7\xa5Vk\x19'
  Binary           Loose object ae00a8bb6d207781ea189a4fb85b0324df42f111 b'x\x9c+)JMU07f040031Q\xc8H\xcd\xc9\xc9\xd7+\xa9(ax\xf3%&6\xf5\xd0D\xb7\xa5\xbb\xf7\x9d\xf6\x148\xf7'
  Binary           Loose object bc8a5d61de7204d76cc4ba80bccbbb4fd2be0cdd b'x\x9cK\xca\xc9OR05fPV\xc8\xcb/I-\xe6\xe2*(-\xceHMQH\xaaT(\xa8L\xcf,Q(\xc9WHT\xc8\xc9ON'
  Binary           Loose object ecf45c5d65c29146a5bbbecb4910cee0f6125faf b'x\x9cK\xca\xc9OR04f\xc8H\xcd\xc9\xc9\xd7Q(\xa8L\xcf,\xe1\x02\x00I\xaa\x06\xbb'
  Binary           Packfile                           b'PACK\x00\x00\x00\x02\x00\x00\x00\x04\x92\x0cx\x9c\x9d\xccA\n\x021\x0c\x85\xe1}O\x91\x0b(\xc9\xb4\xb6\x1d\x90A\x17\x1e$uR-X:'
  Code             create_pack                        b'def create_pack(objects):\n    """Create pack'
  Code             push                               b'def push(git_url, username=None, password=No'
  Document         receive-pack ref-update command    b'00770000000000000000000000000000000000000000'
  Document         receive-pack report-status         b'000eunpack ok\n0019ok refs/heads/master\n0000'

-- reported (list each in the patch as a gap; never a failure)
  [none]   0 generated data node names (Custom-a1b2)
  [none]   0 unnamed data nodes
  [none]   0 Custom / untyped data nodes
  [none]   0 nodes carrying two or more labels
  [none]   0 nodes whose content starts with '/' (path text, not a file)
  [none]   0 data nodes with two or more producers
  [none]   0 cycles

-- checks
  [ok  ]   0 computations with no computation_type
  [ok  ]   1 connected components, as predicted
  [ok  ]   2 computations, as predicted

-- roots (7), data nodes nothing produced
  Code             push
  Binary           Loose object 508ceb25d9126b53d07b8c34da45ae546e53cef4
  Document         receive-pack ref-update command
  Binary           Loose object ecf45c5d65c29146a5bbbecb4910cee0f6125faf
  Binary           Loose object ae00a8bb6d207781ea189a4fb85b0324df42f111
  Binary           Loose object bc8a5d61de7204d76cc4ba80bccbbb4fd2be0cdd
  Code             create_pack

-- leaves (1), data nodes nothing consumed
  Document         receive-pack report-status

-- computations, in the order they were registered
  Pack missing objects     aggregate    5 in -> 1 out
  POST pack to git-receive-pack emit         3 in -> 1 out

all checks passed
```

## SDK verification and content checks (run offline, `./venv/bin/python`)

- `verify_statement(json, contexts=manifest["contexts"])` on all 22 non-credential
  statements: **22/22 True**. `verify_vc(credential, statement_id=credentialSubject.id,
  contexts=…)` on all 24 credentials: **24/24 True**. Every non-credential statement
  is the subject of a credential.
- Blobs: all 20 blobs re-hash to their CIDs with `get_cid_for_bytes` or `get_cid_for_json`
  (the scratch SDK dir used for hashing was deleted afterwards). All 9 data CIDs referenced by computations have a blob.
- Loose-object blobs: each one zlib-decompresses to a git object (`commit 194`, `tree 73`,
  `blob 53`, `blob 13`) whose SHA-1 equals the SHA in its name.
- `Code` blobs: `create_pack` (25 lines) and `push` (44 lines) are verbatim substrings of
  `pygit-hitl/pygit.py`. They are the repo's functions (with the instrumentation lines),
  not adapters.
- Behaviour: the **pristine** `repo/pygit.py`'s `create_pack` on the same four objects
  returns bytes identical to the recorded Packfile. `pygit.py status` in the pushed repo
  prints nothing and exits 0 under both the pristine and patched file (`.git/eqty_sdk` is invisible to it).
- Manifest sha256 `d9341ff62812ae0a4fc33bca2733b1a541b37c09a7ca2f8ffc3f9ff62889b484`.
  It is one file, moved (not copied) from `.git/eqty_sdk/manifest.json`, and unedited.

## `references/eqtysdk.md` §11, worked

- [x] SDK version and storage/context configuration recorded (table above).
- [x] The entry point the instrumentation claims to cover was run: `pygit.py push` via the CLI, after `init`/`add`/`commit` via the CLI.
- [x] Inputs, outputs and source inspected against the work. Keyword args (`username=`,
      `password=`) are not captured, by design. Implicit dependencies not captured are
      listed as gaps 3, 4 and 6.
- [x] Statement IDs and credentials verified (22/22, 24/24). Each credential is bound to its
      subject. Issuer: the single local `did:key` signer `pygit`. Authorising that signer is the reader's policy.
- [x] Every blob checked with its codec. No referenced content is unavailable.
- [x] Missing steps, repeated content and connectivity assessed: 1 component as predicted,
      and the Packfile repeat is gap 8. The missing steps are gaps 3 and 5.
- [x] Disclosure reviewed: no credentials. File contents, commit author/email, source and
      push URL are all exported. Scope proven: only the two recorded steps of this one local push.

## `references/mode2.md` review checklist

- [x] The CFG was drawn by agents that never saw mode2.md, mode2ideas.md or the SDK, and L2 was dispatched, not derived (`cfg/isolation.json`).
- [x] Both CFG agents were started by `isolated_cfg.py`. `isolation.json` sits beside the CFG in `cfg/`, names the agent `claude`, and every check passes. (Its own `known_residue`: the account email line injected at login.)
- [x] The user was asked auto vs HITL at step 2 and asked nothing about nodes before it. Per the task, step 2 was done upstream; the user chose HITL. I did not ask the question myself.
- [x] A node list exists in writing and predates the patch (`../pygit.hitl.nodes.md`).
- [x] In HITL, the nodes added to chain the user's picks are listed with why: none were needed, and the file says why, including why `find_missing_objects` was not added.
- [x] Predicted node count, root count and shape were written before the run and compared after it (table above: all match).
- [x] `eqty_sdk` is imported at top level, unconditionally.
- [x] No locally defined compute decorator. None is applied at all; both nodes are builders.
- [x] Every `@compute` sits on a function that exists in the pristine target. This holds vacuously, since there are none. Both builders sit inside `create_pack` and `push`, in `pygit.py`.
- [x] The patch adds no new source file to the target. The only new files are `requirements.txt` (the repo had no dependency file) and `run_example.py` (scaffolding).
- [x] No node uses `Code.from_path`. `Code.from_object(inspect.getsource(f))` is called inside `f` itself, for `create_pack` and `push`.
- [x] Nothing the skill wrote is instrumented. `run_example.py` and its stand-in remote have no nodes; they call pygit's CLI with ordinary argv.
- [x] Nodes that could not take `@compute` on the original definition are reported with the ladder rung tried: rung 3, a builder inside the target function (edits 2 and 4). No node fell to a gap.
- [ ] **Every edit is named with its §6.10 rule, and behaviour is unchanged for every caller, including the command line, which still records its runs.** The first half and the CLI half are true. Unticked because behaviour is **not** unchanged for library callers who do not initialise the SDK: they now get `PanicException: Config not initialized` (gap 1).
- [x] No naming helpers, and no other asset-building functions. Everything is registered inline.
- [x] No parameter list changed, and no parameter or return value added. The single edge (Packfile) is the value `push` really passes from `create_pack` into the POST body.
- [x] The dependency file declares `eqty_sdk==2.4.2`.
- [x] `Custom` nodes, generated names, path-text inputs, identifier inputs and missing edges are listed as gaps. There are no `Custom`, generated-name or path-text nodes. Identifiers are gap 4; missing edges are gaps 3 and 5.
- [x] The SDK directory and signer key are outside the user's tracked files (`.git/eqty_sdk/`).
- [x] Connectivity matches the prediction: 1 component.
- [x] Every node's `computation_type` is set (`aggregate`, `emit`).
- [x] §11 worked, and `check_graph.py … --expect-computations 2 --expect-components 1` passes. It reported no items, so there was nothing to reconcile with the gap list.
- [x] Exactly one manifest, exactly as emitted: `../pygit.hitl.manifest.json`.
- [x] Everything stubbed for the example is named above, together with what the manifest therefore does not claim.

## Housekeeping disclosed

Two stray writes happened during the run, and both were removed. Importing the pristine
module for the pack comparison created `repo/__pycache__/`; it was deleted, and `repo/`'s
files are unmodified (Sep 10 mtimes, patch re-applies cleanly). A throwaway shell
redirect wrote `/tmp/x`, outside the workspace; it was deleted.

The manifest path is `out/pygit.hitl.manifest.json`. Reading, verifying and reporting on
it is the separate `eqty-manifest` skill's job, if wanted. No remote exists here, so
nothing was pushed and no pull request was opened.
