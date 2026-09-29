# pygit — prediction (auto)

**Nothing was run.** This says what the patched commands should record when
someone runs them, and how to check it.

## Run it

```sh
pip install -r requirements.txt                 # eqty_sdk==2.4.2
export PYGIT_EQTY_CONTEXT=$(python -c "import uuid; print(uuid.uuid4())")   # one context for all four commands
python pygit.py init myrepo && cd myrepo
python ../pygit.py add hello.txt notes.txt
python ../pygit.py commit -m "first commit"
GIT_USERNAME=... GIT_PASSWORD=... python ../pygit.py push https://github.com/<you>/<repo>.git
```

Each command exports the shared context when it exits, to `$PYGIT_EQTY_MANIFEST`
or by default `~/.pygit-eqty/manifests/<context id>.json`, so the file left after
`push` is the whole run. The SDK directory, with the signer's key, is
`$PYGIT_EQTY_DIR` (default `~/.pygit-eqty`), outside the repository.

## What it should record

With two files added, so the push packs four objects (two blobs, a tree, a
commit):

- **Computations: 8** — `init`; `add` ×2 (decorator and builder); `commit` ×2;
  `get_remote_master_hash`; `create_pack`; `push`.
- **Connected components:** **3** on a first push to an empty remote (`{init}`,
  `{get_remote_master_hash}`, and the chain from `add` to `push`); **2** when the
  remote already has commits, because the remote master id then flows into `push`.
- **Roots: 12** — the `Code` of all six functions; `repo`; `paths`; the two
  working files; `message`; the info/refs URL.
- **Leaves: 4** — `HEAD`, the commit id, the ref advertisement, the receive-pack
  response.
- **Multi-producer nodes: 2** — `.git/index` and the commit id.
- **Cycles: 0.**

## Assumptions the placement rests on

From `../flow.json` and `../merged.md`: the `.git/index` and master-ref hand-offs
are **likely** (found by the path both sides spell out); the three unknown
hand-offs are handled without depending on their types (`nodes.md`).

## Check the manifest, once there is one

```sh
uv run <eqty-manifest>/summary.py <manifest>
python3 <eqty-instrument>/check_graph.py <manifest> --expect-computations 8 --expect-components 3   # 2 if the remote had commits
```

Then work the `references/eqtysdk.md` §11 checklist, including the decoded blobs:
the info/refs URL is recorded as an input of `get_remote_master_hash`.
