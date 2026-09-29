# pygit — prediction (HITL)

**Nothing was run.** This says what the patched `push` should record.

## Run it

```sh
pip install -r requirements.txt                 # eqty_sdk==2.4.2
python pygit.py init myrepo && cd myrepo
python ../pygit.py add hello.txt notes.txt
python ../pygit.py commit -m "first commit"
GIT_USERNAME=... GIT_PASSWORD=... python ../pygit.py push https://github.com/<you>/<repo>.git
```

Only `push` records. It writes `.git/eqty_sdk/manifest.json`; the SDK directory,
with the signer's key, is `.git/eqty_sdk/`, inside `.git` and so outside the
working tree.

## What it should record

With two files added, so four objects are packed:

- **Computations: 2** — "Pack missing objects", "POST pack to git-receive-pack".
- **Data nodes: 9** — the four object files, `Code(create_pack)`, the pack,
  `Code(push)`, the ref-update command, the response.
- **Connected components: 1.**
- **Roots: 7** — the four object files, the two `Code` assets, the ref-update command.
- **Leaves: 1** — the receive-pack response.

## Check the manifest, once there is one

```sh
uv run <eqty-manifest>/summary.py .git/eqty_sdk/manifest.json
python3 <eqty-instrument>/check_graph.py .git/eqty_sdk/manifest.json --expect-computations 2 --expect-components 1
```
