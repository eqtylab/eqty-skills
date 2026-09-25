# pygit — Mode 2 (auto) placement, gaps and review

**This is a proposal for a person to review. Nothing was committed.** The target is not a git checkout and has no remote, so no branch was pushed and no pull request was opened. The change is `pygit.auto.patch`, written beside this file.

- Node selection (written before any code): `../pygit.auto.nodes.md`
- Manifest (one copy, exactly as exported): `../pygit.auto.manifest.json`
- CFG (step 1, isolated agents): `../../cfg/pygit.cfg.html`, `L1.md`, `L2.md`, `isolation.json`

## Reproduce

Versions:

- Python 3.12.8 (`./venv/bin/python`)
- `eqty_sdk==2.4.2`, which writes manifest `version` 3
- No network; the remote is `stub_remote.py` on 127.0.0.1

Run these from the workspace root (the directory holding `repo/`, `out/` and `eqty-instrument/`):

```sh
# the run: init -> add -> commit -> push, each a real `python pygit.py ...` process
./venv/bin/python out/pygit/run_example.py
#   -> writes out/pygit.auto.manifest.json (SDK store + signer: out/pygit/.eqty/, scratch repo: out/pygit/.run/)

# graph checks against the prediction
./venv/bin/python eqty-instrument/check_graph.py out/pygit.auto.manifest.json --expect-computations 8 --expect-components 3

# the patch (./pygit is a symlink to out/pygit, so the paths come out as repo/... and pygit/...)
diff -ruN -x .eqty -x .run -x __pycache__ -x pygit.auto.patch -x pygit.auto.placement.md repo pygit > out/pygit/pygit.auto.patch
```

`run_example.py` wipes `out/pygit/.eqty` and `out/pygit/.run` on every run. Each run therefore generates a new signer key and DID, a new context UUID and new commit ids (the timestamps change). The graph shape and counts do not change.

Two supporting checks were run from `scratch/`. They are not part of the patch.

- `./venv/bin/python scratch/verify.py out/pygit.auto.manifest.json` checks the manifest with the SDK's own `verify_statement` and `verify_vc`, and re-hashes every blob.
- `./venv/bin/python scratch/compare.py` runs pristine and instrumented pygit through the same 16 commands with the clock pinned (`scratch/fixedtime/sitecustomize.py`). It compares exit codes, stdout, the last line of stderr and the resulting file lists.

## What the patch contains

| File | Kind |
|---|---|
| `pygit.py` | the target's only source file, edited in place |
| `requirements.txt` | new. The repo had no dependency file; it pins `eqty_sdk==2.4.2` |
| `run_example.py` | scaffolding. No nodes; it drives pygit's own CLI |
| `stub_remote.py` | scaffolding. No nodes; a local smart-HTTP remote |

No new source module was added to the target. Excluded from the patch:

- `.eqty/`: the SDK store and signer private key
- `.run/`: the scratch repo the example creates
- `__pycache__/`
- this file and the patch itself

## Every edit to `pygit.py`, with the `references/eqtysdk.md` §6.10 rule that allows it

§6.10 has three rules:

- **R1** preserve behavior
- **R2** record only what really happened
- **R3** no naming helpers

The "row" labels are rows of the §6.10 failure-mode table.

| # | Where (instrumented file) | Edit | Allowed by |
|---|---|---|---|
| 1 | L8–13 | Added `atexit` and `uuid` to the stdlib imports. `eqty_sdk` is imported at top level with no `try/except`. `init` is imported as `eqty_init` so it does not shadow pygit's own `init()` | §13.1 (hard dependency, never shimmed); R1 (imports only) |
| 2 | L16–29 | Module-level SDK setup. It reads the SDK dir from `PYGIT_EQTY_DIR` (default `~/.pygit-eqty`, outside any working copy) and the context from `PYGIT_EQTY_CONTEXT` (a UUID, so several CLI processes record into one context; otherwise a new context per process). It stores all blobs, loads or creates the `pygit` signer, and registers `atexit` export to `PYGIT_EQTY_MANIFEST` (default `<dir>/manifests/<context-id>.json`) | mode2.md step 4, "initialising the SDK and a signer in the target's own entry point". It sits at module level so that *every* entry point records (CLI and `import pygit`) and none is switched off. R1: nothing is written into the working copy, and `atexit` leaves exit codes alone |
| 3 | `init` L60–73 | `@compute` with inline metadata (`computation_type: emit`), and `return Document.from_path(<repo>/.git/HEAD)` | Row 1 (None return: return a record of a file it really wrote). No caller uses the `None`: `__main__` ignores the result |
| 4 | `add` L275–313 | `@compute` (`ingest`) plus a `Computation` builder inside. It hashes the old `.git/index` if one exists, each working file's bytes as read, and each blob object and the new index as written. `sha1 = hash_object(read_file(path), 'blob')` is split into `data = read_file(path)` then `hash_object(data, 'blob')`, which is the same read in the same order. It now returns the `.git/index` asset. The builder is finalised only when it has an input | Row 1 (None return; `__main__` ignores it). "Data that moves through files": a builder inside the writer. R2: file bytes are hashed from `data` in hand; the index and objects are hashed from the files just written. The guard exists because the SDK raises on a computation with no inputs (found by the R1 comparison) |
| 5 | `commit` L337–392 | `@compute` (`transform`) plus a builder inside. It hashes `.git/index` just before `write_tree()` reads it, and the parent `refs/heads/master` just before `get_local_master_hash()` reads it. It hashes the tree object, the commit object and `refs/heads/master` just after they are written, plus the commit id. The return value is unchanged. The builder is finalised only when it has an input | "Data that moves through files": a builder inside the reader and writer. R1: the return value callers use is untouched. The guard came from an R1 failure: the SDK raised `RuntimeError: CID list must not be empty` on a first commit with no index. The pristine code makes an empty-tree commit there, and so does the patched code now |
| 6 | `get_remote_master_hash` L437–463 | A builder inside (`ingest`). Input: the info/refs URL text. Outputs: the response bytes as received, plus the remote master id when there is one. It is finalised at each return | Ladder rung 3. `@compute` would capture `password` (passed positionally by `push`) into the manifest, so it is ruled out. R2: the response is hashed from the bytes in hand |
| 7 | `create_pack` L546–566 | A builder inside (`aggregate`). Inputs: each packed object file in the store. Output: the pack bytes returned. It is skipped for an empty object set | Row 3 (a positional `set` raises `TypeError`); ladder rung 3. R2: the object files are the ones `encode_pack_object` reads one call down, hashed by path (a second read). The pack is hashed from `data` in hand. The guard: an empty set would give a no-input computation, and the SDK raises |
| 8 | `push` L569–608 | A builder inside (`emit`). Inputs: the remote master id if any, `refs/heads/master` hashed just before `get_local_master_hash()` reads it, and the pack bytes sent. Output: the receive-pack response, recorded before the asserts, so a rejected push is also recorded. `data = build_lines_data(lines) + create_pack(missing)` is split so the pack is in hand. That swaps the evaluation order of `build_lines_data`, which is pure, and `create_pack`. Nothing observable changes | Row 2 (returns `(str\|None, set)`; a `set` output raises). The return value is not changed, because library callers may use the set (R1). R2: bytes in hand |

R3 holds for all of these. No function was added to `pygit.py`. Every asset is registered inline where its bytes are read or written, and names such as `'blob ' + sha1` are built inline at that site.

## Behavior preserved (R1): evidence

`scratch/compare.py` ran the same 16 steps through `repo/pygit.py` and `out/pygit/pygit.py` with the clock pinned. The steps were:

- init
- commit with an empty index
- `cat-file` on a missing object (exit 1)
- `hash-object`
- add ×2
- `ls-files -s`, `status`
- commit
- re-add
- commit with a parent
- `cat-file pretty`, `diff`
- push to the stub
- nested init, and init on an existing dir (exit 1)
- library calls `create_pack(set())`, `add([])`, `read_index()`

Result: identical exit codes and stdout on 15 of the 16 steps, including matching commit ids. The file lists under both scratch trees are identical: no SDK file appears in any working copy. The one difference is the `ctime`/`inode` fields that `read_index()` prints in the library step. They come from the test harness writing the working files at different moments, not from pygit. The first comparison found the empty-index `commit` crash; edits 4, 5 and 7 now carry guards and the rerun is clean.

Remaining R1 caveats (not failures of the checked paths, but real differences a reviewer should weigh):

- `import pygit` now initialises the SDK for the whole process and sets the active signer. A host program that already uses `eqty_sdk` would see its second `init()` ignored (with a logged warning), and its active signer replaced by `pygit`'s.
- Each run writes into the SDK directory (default `~/.pygit-eqty`) and exports a manifest at exit. That directory must be writable. The default path was **not** exercised in this run, because nothing may be written outside the workspace; every run here set `PYGIT_EQTY_DIR`.
- `init`/`add` now return an asset instead of `None`. No caller in the repo uses the result.
- Extra reads: the index, the ref and the object files are hashed at their point of use, a second read of each.

## Stubbed, and what the manifest therefore does not claim

- **The remote.** `stub_remote.py` stands in for GitHub's smart-HTTP endpoints. It requires HTTP Basic auth and advertises an empty repo. It checks the received pack's signature, version, count and SHA-1 trailer, and replies `unpack ok` / `ok refs/heads/master`. It does **not** unpack or store objects, and it does not move a ref. The manifest's `remote ref advertisement` and `remote status report` are the stub's bytes. **The manifest makes no claim that any real remote received, stored or accepted this commit.**
- **Credentials** are dummy values (`demo` / `demo-password`). They are not in the manifest: checked by searching every blob, raw and inflated.
- **Nothing else.** `init`, `add`, `commit`, the missing-object walk and pack building run as real pygit code on real files. The two working files and the commit message are fixed text from `run_example.py`.

## Reported gaps

Every item `check_graph.py` reports is listed here.

1. **Path-text inputs.** `repo` (`init`) and `paths` (`add`) are `Custom` assets of path text, not file content. The file content is recorded separately by `add`'s builder.
2. **Identifier inputs and outputs.**
   - The commit id is a `Custom` node with a generated name (`Custom-tin4` in this run); `@compute` names plain return values that way.
   - The remote master id is recorded as text when the remote has one.
   - The `info/refs URL` is `Custom` text. If a user embeds credentials in a URL (`https://user:pass@…`), they would be recorded.
3. **`Custom` nodes: 5.** They are `repo`, `paths`, `message`, the commit id and the `info/refs URL`. `message` is the real commit message, just untyped.
4. **Keyword arguments not captured.** The decorator does not see `commit(..., author=)`, nor `push(..., username=, password=)`. The password is left out on purpose. `author` does appear, inside the commit object bytes.
5. **Two producers for one node, by design.**
   - `.git/index` is produced by `add` (decorator) and `add` (builder).
   - The commit id is produced by `commit` (decorator) and `commit` (builder).
   - Each pair is one function recorded twice: once with its `Code` and CLI inputs, once with its file content.
6. **No `Code` asset on builder-only nodes.** These are `get_remote_master_hash`, `create_pack` and `push`: ladder rung 3 (a builder inside the target function), tried after rung 1 failed.
   - For `get_remote_master_hash`, `@compute` would capture the password.
   - For `create_pack`, the `set` argument raises.
   - For `push`, the `set` in its return value raises.
   - `to_eqty_asset()` (rung 2) does not apply to `str` or `set` arguments.
7. **Missing edge: remote ingest → push.** The only value that flows is `remote_sha1`, and on a fresh remote it is `None`. So `get_remote_master_hash` is its own component in this run. It would link through the remote id on a push to a non-empty remote, but that was not exercised.
8. **Missing edge: `init` → anything.** No later pygit step reads `HEAD`, so `init` is its own component. This is honest: `add` does not depend on `init`'s output bytes.
9. **References inside objects are not edges.**
   - The index names blobs, the tree names blobs, and the commit names its tree by SHA-1 inside their bytes. The graph does not draw blob → index or tree → commit object.
   - The tree and the commit object are sibling outputs of `commit`'s builder.
   - All of them do fan into `create_pack`.
10. **Stored object bytes.** Objects are recorded as the stored, zlib-compressed files. A reader must inflate them to see the git object.
11. **Second reads.** Hand-off files (index, ref, object files) are hashed from the file at the moment the target reads or writes it, not from a buffer the target holds. That is the sanctioned §6.10 pattern, but it is a separate read.
12. **Paths that record less.** In these cases the SDK rejects a computation with no inputs, so the builder is skipped:
    - a first commit with no index (the decorator node still records);
    - `add([])` with no index (likewise);
    - `create_pack` of an empty set (nothing records, but `push` still records the empty pack it sends).
13. **Failure paths.** If an assert fails in `get_remote_master_hash` after the response arrives, no computation is recorded. `push` records the remote's report *before* its asserts.
14. **Cross-process runs.**
    - Without `PYGIT_EQTY_CONTEXT`, each CLI command is its own context and manifest file, and nothing links them.
    - With it, every process exports the cumulative context at exit, overwriting `PYGIT_EQTY_MANIFEST`. The example relies on this: the file was exported four times, and the one kept is the export `push` made.
15. **Uninstrumented entry points.** `cat-file`, `hash-object` (including `-w`, which writes an object), `ls-files`, `status` and `diff` carry no nodes.
16. **Nondeterminism.** The commit timestamp and the index's stat fields (ctime, mtime, dev, inode, uid, gid) make the commit id and the index CID machine- and moment-specific. They are recorded inside the bytes, not as metadata, and the run is not reproducible byte-for-byte.
17. **Disclosure.** With `store_all_blobs = true` the manifest carries:
    - the working-file contents and the commit message;
    - the author name and email (inside the compressed commit object);
    - the local stub URL;
    - the full source of `init`/`add`/`commit`.

## Prediction vs result

| | Predicted (`pygit.auto.nodes.md`) | Manifest |
|---|---|---|
| Computations | 8 | **8** |
| Connected components | 3: `{init}`, `{get_remote_master_hash}`, main chain | **3**, the same three |
| Roots | 9 | **9**: Code ×3, `repo`, `paths`, `hello.txt`, `notes.txt`, `message`, `info/refs URL` |
| Leaves | 4 | **4**: `HEAD`, commit id, remote ref advertisement, remote status report |
| Multi-producer nodes | 2 (index, commit id) | **2** |
| Cycles | 0 | **0** |

**The prediction matched on every count.** `check_graph.py` lists computations in the manifest's statement order, not in run order; the prediction's run order was not checked.

## `check_graph.py` output (final run)

```
== out/pygit.auto.manifest.json
8 computations, 20 data nodes

-- every node as a reader sees it
  COMPUTE          add                                b''
  COMPUTE          add: working-tree files -> blob objects + .git/index b''
  COMPUTE          commit                             b''
  COMPUTE          commit: .git/index -> tree + commit objects, master ref b''
  COMPUTE          create_pack: objects -> packfile   b''
  COMPUTE          get_remote_master_hash: GET info/refs b''
  COMPUTE          init                               b''
  COMPUTE          push: pack -> remote git-receive-pack b''
  Binary           .git/index                         b'DIRC\x00\x00\x00\x02\x00\x00\x00\x02j\xb6\xc8B\x00\x00\x00\x00j\xb6\xc8B\x00\x00\x00\x00\x01\x00\x00\x12\x07O\xd7\xfa\x00\x00\x81\xa4\x00\x00\x01\xf5'
  Binary           blob 5862ca8e9d24ea9b4480a7377a9275e9a97942f4 b'x\x9cK\xca\xc9OR01f(\xa8L\xcf,\xb1R\xc8*-.QH\xcd\xcb/M\xcfP\x00\n(\x94\xe4+$\xe7\xe7\xe6\x02Y\x89y)'
  Binary           blob af5626b4a114abcb82d63db7c8082c3c4756e51b b'x\x9cK\xca\xc9OR04a\xf0H\xcd\xc9\xc9\xd7Q(\xcf/\xcaIQ\xe4\x02\x00N\xf5\x06\xb8'
  Binary           commit cb351c12a5a2721e98708f56a01c544208dddb09 b'x\x9c\x95\xcdM\n\x021\x0c@a\xd7=E\x0e\xa0\xd2\x18\xdbiAD\x17z\x8fL\x7f\x9c\x82\xa5C\x8d\xa8\xb7\x17\x9c\x13\xb8|\x8b\x8f\x17Z\xad'
  Binary           packfile                           b'PACK\x00\x00\x00\x02\x00\x00\x00\x04\xaa\x04x\x9c340031Q\xc8H\xcd\xc9\xc9\xd7+\xa9(aX\x1f\xa6\xb6e\xa1\xc8\xea\xd3M\xd7'
  Binary           tree 353f12a35cb966cc31960bf61f311f9854915f33 b'x\x9c+)JMU07a040031Q\xc8H\xcd\xc9\xc9\xd7+\xa9(aX\x1f\xa6\xb6e\xa1\xc8\xea\xd3M\xd7l\xb7\x9f\xe0\xd0\xb1'
  Code             add                                b"@compute(metadata={\n        'name': 'add',\n "
  Code             commit                             b"@compute(metadata={\n        'name': 'commit'"
  Code             init                               b"@compute(metadata={\n        'name': 'init',\n"
  Custom           Custom-tin4                        b'cb351c12a5a2721e98708f56a01c544208dddb09'
  Custom           info/refs URL                      b'http://127.0.0.1:52348/demo/myrepo.git/info/'
  Custom           message                            b'First commit, made with pygit'
  Custom           paths                              b'["hello.txt", "notes.txt"]'
  Custom           repo                               b'myrepo'
  Document         HEAD                               b'ref: refs/heads/master'
  Document         hello.txt                          b'Hello, world!\n'
  Document         notes.txt                          b'pygit: just enough git to commit and push.\n'
  Document         refs/heads/master                  b'cb351c12a5a2721e98708f56a01c544208dddb09\n'
  Document         remote ref advertisement           b'001f# service=git-receive-pack\n0000004b00000'
  Document         remote status report               b'000eunpack ok\n0019ok refs/heads/master\n0000'

-- reported (list each in the patch as a gap; never a failure)
  [note]   1 generated data node names (Custom-a1b2)  e.g. Custom-tin4
  [none]   0 unnamed data nodes
  [note]   5 Custom / untyped data nodes  e.g. Custom-tin4; repo; paths
  [none]   0 nodes carrying two or more labels
  [none]   0 nodes whose content starts with '/' (path text, not a file)
  [note]   2 data nodes with two or more producers  e.g. Custom-tin4; .git/index
  [none]   0 cycles

-- checks
  [ok  ]   0 computations with no computation_type
  [ok  ]   3 connected components, as predicted
  [ok  ]   8 computations, as predicted

-- roots (9), data nodes nothing produced
  Custom           paths
  Custom           repo
  Custom           message
  Code             add
  Document         hello.txt
  Code             commit
  Custom           info/refs URL
  Code             init
  Document         notes.txt

-- leaves (4), data nodes nothing consumed
  Document         HEAD
  Custom           Custom-tin4
  Document         remote ref advertisement
  Document         remote status report

-- computations, in the order they were registered
  commit: .git/index -> tree + commit objects, master ref transform    1 in -> 4 out
  create_pack: objects -> packfile aggregate    4 in -> 1 out
  init                     emit         2 in -> 1 out
  add                      ingest       2 in -> 1 out
  commit                   transform    2 in -> 1 out
  add: working-tree files -> blob objects + .git/index ingest       2 in -> 3 out
  push: pack -> remote git-receive-pack emit         2 in -> 1 out
  get_remote_master_hash: GET info/refs ingest       1 in -> 1 out

all checks passed
```

Its exit code was 0. Its "1 in -> 4 out" for the commit builder is right for this run: a first commit has no parent ref to read.

## `references/eqtysdk.md` §11, worked

- [x] **SDK version and configuration recorded.**
  - `eqty_sdk` 2.4.2.
  - `store_all_blobs = true`; the `cid_ignore` defaults (hidden files, gitignore and symlinks all false) are persisted in `out/pygit/.eqty/config.toml`.
  - One context per run, set through `PYGIT_EQTY_CONTEXT`; the example uses a new UUID each run.
  - The signer is local Ed25519, named `pygit`.
- [x] **The claimed entry points were run.** The CLI sequence `init → add → commit → push` ran as four separate processes. Every other subcommand, plus three library calls, ran in the R1 comparison. Their manifest was not kept: they are not the claimed run.
- [x] **Inputs, outputs and source inspected against the work.**
  - Every data node was read in the `check_graph` listing above.
  - The three `Code` blobs are the decorated functions' text, found verbatim in `out/pygit/pygit.py`: `init` (14 lines), `add` (39 lines), `commit` (58 lines). They are not adapters.
  - Omitted keyword arguments (`author`, `username`, `password`) and the dependencies the decorator does not capture (helpers such as `hash_object`, `write_index`, `write_tree`) are listed as gaps 4 and 6.
- [x] **Statement IDs and credentials verified.**
  - `verify_statement` passed 112/112, using the contexts compiled into the SDK. None were supplied from the manifest.
  - `verify_vc` with `statement_id` = the credential subject passed 56/56.
  - Every non-credential statement has a credential.
  - There is a single issuer, the run's own `did:key`.
  - **Not done: issuer authorisation.** No policy exists for which DID may sign pygit runs. The key is created fresh by the example.
- [x] **Blobs checked with the right codec.**
  - 20/20 raw (`bafkr4…`) blobs re-hash to their CID through `get_cid_for_bytes`.
  - 28/28 JSON metadata (`baga6…`) blobs re-hash through `get_cid_for_json`.
  - No referenced content CID lacks a blob.
- [x] **Missing steps, repeated content and connectivity assessed** against the intended workflow: 3 components, all explained (gaps 7 and 8); 2 multi-producer nodes, deliberate (gap 5); no cycles.
- [x] **Disclosure reviewed.** See gap 17. No credentials appear, raw or inflated. Only the scope above was checked.

## `references/mode2.md` review checklist

- [x] **The CFG agents never saw this file, `mode2ideas.md` or the SDK, and L2 was dispatched rather than derived.** Both levels come from `isolated_cfg.py`, one dispatch each, per `isolation.json`. L2's prompt carries only L1's diagram and its `Run:` line.
- [x] **Both agents were started by `isolated_cfg.py`, and every check in `isolation.json` passes.** It names `agent: claude`, and every check passes for L1 and L2: no skills, no MCP servers, tools limited to Glob/Grep/Read/Write, every tool call inside the agent's copy. Its declared residue is the account `userEmail` line injected at login.
- [x] **The user was asked auto vs HITL at step 2, and nothing about nodes before it.** Step 2 happened before this session, and the user chose auto. This session asked nothing.
- [x] **A written node list predates the patch.** `out/pygit.auto.nodes.md` was written before `pygit.py` was copied or edited.
- [x] **HITL: added nodes listed.** Not applicable (auto).
- [x] **Prediction written before the run and compared after.** Node count, root count, leaves, components and shape are in the nodes doc, and the comparison is above. The prediction matched.
- [x] **`eqty_sdk` imported at top level, unconditionally.** No `try/except ImportError` and no fallback.
- [x] **`eqty_sdk.compute` applied directly.** No local compute decorator; metadata is inline at each of the three decorators.
- [x] **Every `@compute` is on a function in the pristine target.** `init`, `add` and `commit` keep their own names in `pygit.py`.
- [x] **No new source file in the target.** The new files are `requirements.txt`, which the repo lacked, and the scaffolding: `run_example.py` and `stub_remote.py`.
- [x] **No `Code.from_path` anywhere.**
- [x] **Nothing the skill wrote is instrumented.** `run_example.py` and `stub_remote.py` hold no `@compute` and no builder. The example calls pygit's CLI with the same string arguments a user types.
- [x] **Nodes that could not be decorated are reported as gaps, with the rung tried.** Gap 6: `get_remote_master_hash`, `create_pack` and `push` are on rung 3 (builder inside the target function).
- [x] **Every edit named with its §6.10 rule, behavior unchanged, and the command line still records.** The edit table above does this; the command line still records its runs. The rules are named in this document, which ships beside the patch, not as comments inside the diff. Behavior was checked by the R1 comparison, and the remaining caveats are listed.
- [x] **No naming helpers and no other new asset-building functions.** No function was added.
- [x] **No parameter list changed, and no unused parameter or return value added.**
  - The only return-value changes are the two sanctioned None → asset records (`init`, `add`); `__main__` ignores both results.
  - Every edge comes from bytes read or written.
  - The file hand-offs (index, ref, objects) are recorded by builders inside the writing and reading functions.
- [x] **The dependency file declares `eqty_sdk==2.4.2`.** It is a new `requirements.txt`.
- [x] **Remaining `Custom` nodes, generated names, path-text and identifier inputs and missing edges are listed as gaps** (1, 2, 3, 7, 8, 9).
- [x] **The SDK directory and signer key are outside the user's tracked files.** The default is `~/.pygit-eqty`. In this run they are at `out/pygit/.eqty`, inside the instrumented folder: that folder is not a git repo, and `.eqty` is excluded from the patch. Nothing lands in the demo working copy. That was checked in the comparison: identical file lists.
- [x] **Connectivity matches the prediction:** 3 of 3.
- [x] **Every node's `computation_type` is set:** 0 missing.
- [x] **§11 worked, `check_graph.py` passes with `--expect-computations 8 --expect-components 3`, and every reported item is in the gap list:**
  - generated name → gap 2
  - `Custom` ×5 → gap 3
  - multi-producer ×2 → gap 5
- [x] **Exactly one manifest, exactly as emitted:** `out/pygit.auto.manifest.json`. No repaired or context-embedded copy exists. It is the file the SDK's last export (at `push`'s exit) wrote; nothing edited it afterwards.
- [x] **Everything stubbed is named, with what the manifest does not claim:** see "Stubbed".

## Handoff

The manifest is at `out/pygit.auto.manifest.json`. The read-side `eqty-manifest` skill is run separately on it; this session did not use it and wrote no report.
