# lora_banking77 — prediction (auto)

**Nothing was run.** The skill reads code and writes a patch; this file says what
the patched program should record when it runs where it is deployed, and how to
check that.

## Run it

```sh
pip install -r requirements.txt      # adds eqty-sdk==2.4.2
python train_lora.py --base <hf model id> --out runs/lora
```

The manifest is written to `$EQTY_MANIFEST`, by default
`~/.eqty/lora-banking77/manifests/train_lora.json`. The SDK directory, with the
signer's key, is `$EQTY_DIR` (default `~/.eqty/lora-banking77`), outside the repo.

## What it should record

From `../merged.md`, restricted to the seven selected functions (`main` is not a
node):

- **Computations: 8** — one per node, `tokenize` twice.
- **Connected components: 1.**
- **Roots: 13** — the two source splits and the base weights (by reference), the
  tokenizer, the LoRA config, the training arguments, and the seven functions'
  `Code` (`tokenize`'s is one node, used by both calls).
- **Leaves: 3** — `adapter_model.safetensors`, `adapter_config.json`, `metrics.json`.
- **Data nodes: 23.**
- **By reference: 3** — the base weights and the two source splits, each with
  `storage="by-reference"`, a reason and where to obtain it.

## Assumptions the placement rests on

Facts `../flow.json` marks **likely**, not certain:

- `Trainer.train()` changes the model it was built from (the catalogue). The
  patch hashes the adapter before and after training as two assets; if training
  did not change it, the two are equal bytes, one node, and the graph is still
  connected.
- `tokenizer` is a fast tokenizer, so `backend_tokenizer.to_str()` exists
  (`hf-tokenizer`). With a slow one, that call fails; its files from the cache
  are the fallback.

No hand-off was left **unknown**.

## Check the manifest, once there is one

```sh
uv run <eqty-manifest>/summary.py <manifest>
python3 <eqty-instrument>/check_graph.py <manifest> --expect-computations 8 --expect-components 1
```

Then work the `references/eqtysdk.md` §11 checklist: read the node names and the
decoded blobs, and check that no blob holds an absolute path (the training
arguments record `--out` as given; pass a relative path).
