# llama — HITL placement, gaps and review

Mode 2, HITL. Node selection: `../llama.hitl.nodes.md` (written before any code was
touched). Patch: `llama.hitl.patch` (`diff -ruN repo llama-hitl`, relative paths,
without the SDK directory, caches, the generated stub binaries or `run.log`).
Manifest: `../llama.hitl.manifest.json`, exactly as the SDK exported it.

**This is a proposal for review, not a commit.** No git remote is involved; the
change lives in `out/llama-hitl/` and the patch sits beside it.

The user's pick, verbatim (from the user): *"For llama, i just want the model call
and then the result raw token"*. It maps to one node: the model call is
`Llama.generate` (L2 boxes M–V/D7), and the raw tokens are its return value
(box W), recorded as the node's outputs.

---

## 1. Placement: where the node went, and why there

**Node 1, `Llama.generate` (`computation_type: model_call`): a `Computation` builder
inside `llama/generation.py:Llama.generate`, the original definition in its
original file.** This is rung 3 of the step-4 ladder. The rungs above it failed
for these reasons (read from `eqty_sdk/compute/compute.py` and
`eqty_sdk/asset/asset.py`, 2.4.2):

| Rung | Result |
|---|---|
| `@compute` on `Llama.generate` | Fails. The positional `self` is a `Llama`, and `serialize_for_hashing` sends it down the `hasattr(obj, "model")` branch, `json.dumps(model.state_dict())`, which raises `TypeError` on tensors **before the function runs**. Also, the return value `(out_tokens, None)` is a tuple of lists of ints, so `__create_asset__` would record one `Custom` asset per token id rather than one per sequence. And both callers pass `prompt_tokens=` by keyword, so the decorator would never see the prompts. |
| An allowed fix from §6.10 | None applies. Changing `self` is impossible. Changing the return shape would break callers, which unpack `(tokens, logprobs)`. Making the call sites pass `prompt_tokens` positionally would still leave the `self` failure. |
| `to_eqty_asset()` on `Llama` | Rejected. It would be a new asset-building method on the class (§6.10 rule 3), and `Llama` has no honest content to return: it holds in-memory weights, not the checkpoint bytes. |
| **Builder inside `Llama.generate`** | **Used.** It hashes what `generate` actually reads (`self.model.params`, `prompt_tokens`) at the point it reads them, and what it actually returns (`out_tokens`) at the point it has them. |

`@compute` is not used anywhere. The builder registers `generate`'s own source as
the node's `Code` input, taken with `inspect.getsource(Llama.generate)`. That is
the text `@compute` would have captured. The Code blob in the manifest is a
verbatim slice of `out/llama-hitl/llama/generation.py`, checked by substring
match, and it includes the builder itself (§6.0).

## 2. Every edit, with the §6.10 rule that allows it

| # | File:line (patched) | Edit | Rule that allows it |
|---|---|---|---|
| 1 | `llama/generation.py:4` | `import inspect` | Needed for the builder's Code input. No behavior change (rule 1). |
| 2 | `llama/generation.py:14` | `from eqty_sdk import Code, Computation, Configuration, Token`: a top-level, unconditional import (§13.1) | Hard dependency. No `try/except ImportError` (rule 1: visible failure, never a silent no-op). |
| 3 | `llama/generation.py:161–166` | Right after the existing `params = self.model.params`, register `Configuration.from_object(params, name="model params")` and one `Token.from_object(t, name="prompt tokens [k]")` per prompt | Rule 2: `generate` really reads both here, and the bytes are hashed from the values in hand at that moment. Rule 3: inline, no helper. |
| 4 | `llama/generation.py:239–270` | Just before the unchanged `return`, register one `Token` per `out_tokens` sequence (`raw generated tokens [i]`) and finalize a `Computation` with the Code, params and prompt inputs, the raw-token outputs, and metadata `name`, `description`, `computation_type`, `temperature`, `top_p`, `max_gen_len`, `logprobs`, `echo`, `torch_initial_seed` | Rule 2: the outputs are what the function returns. The metadata values are its real arguments and `torch.initial_seed()` at call time. Rule 1: return value, parameters and side effects are unchanged. Rule 3: inline. |
| 5 | `example_chat_completion.py:6–10, 108–112` | In the target's own entry point (`__main__`): `init(Context.new(...), custom_dir=.eqty)`, `set_store_all_blobs(True)`, `Signer.load_or_create("llama")`, then after `fire.Fire(main)` export to `manifests/example_chat_completion.rank<RANK>.json` | §6.10 permits initialising the SDK and a signer in the target's own entry point so the shipped CLI records its own runs. `init` runs once per process at startup. Without it the builder would raise `Config not initialized`. |
| 6 | `example_text_completion.py:4–8, 73–77` | Same as #5, for the other CLI | Same. Without it, this CLI would break, because it also reaches `generate` (rule 1). |
| 7 | `requirements.txt:5` | `eqty_sdk==2.4.2` (`setup.py` reads this file into `install_requires`) | §13.1 dependency rule. |
| 8 | `.gitignore:161–163` | `.eqty/` (the SDK directory, which holds the signer's private key) and `manifests/` | Keeps the key out of tracked files (Mode 2 checklist). |
| 9 | `run_example.py` (new) | Scaffolding: stubs plus one call to `example_chat_completion.main`. **No builder and no `@compute`.** | Outside the target's own source. The only new file in the patch. |

No parameter list changed, no return value changed, no parameter or return value
was added, and no function was added.

## 3. What was stubbed, and what the manifest therefore does not claim

`run_example.py` stubs the boundary, not the logic. The following ran as shipped:
- `example_chat_completion.main`, with its six dialogs and default flags (`max_seq_len=512`, `max_gen_len=None → 511`, `temperature=0.6`, `top_p=0.9`);
- `Llama.build`: checkpoint glob, MP-size assert, `torch.load`, `params.json`, `initialize_model_parallel`, `Tokenizer`, `Transformer`, `load_state_dict`, `torch.manual_seed(1)`;
- `chat_completion`, `generate`, `Transformer.forward`, `sample_top_p` and `Tokenizer.decode`.

| Stubbed | With | So the manifest is **not** a claim about |
|---|---|---|
| Model weights (`llama-2-7b-chat/`) | A tiny Transformer (`dim 64`, `n_layers 2`, `n_heads 4`, vocab 2000) with seeded random weights (`torch.Generator().manual_seed(0)`), saved as `stub/ckpt/consolidated.00.pth` and `params.json` | Llama-2-7b-chat, or any real model. The recorded `model params` Configuration is the stub's configuration. The generated text is noise. |
| Tokenizer (`tokenizer.model`) | A SentencePiece BPE model trained in-process on the repo's Markdown docs (untouched by the patch), saved as `stub/tokenizer.model`. Same special ids as Llama (unk 0, bos 1, eos 2, pad −1) | The real Llama tokenizer. Every recorded token id is relative to the stub vocabulary. |
| CUDA | `device="cuda"` → CPU in `torch.full`/`torch.tensor`; `Tensor.cuda()` → identity; `torch.cuda.set_device` → no-op; `set_default_tensor_type(torch.cuda.HalfTensor)` ignored, so float32 instead of fp16 | GPU execution or fp16 numerics. |
| NCCL / torchrun | A gloo process group initialised by `run_example.py` (file store `stub/gloo-store`, world size 1, `RANK=LOCAL_RANK=0`). `Llama.build` therefore takes its "already initialised" branch (L2 D1 = yes) instead of `init_process_group("nccl")` | Multi-GPU / model-parallel runs. |
| `fire.Fire(main)` / the `__main__` block | `run_example.py` calls `example_chat_completion.main(ckpt_dir=..., tokenizer_path=...)` itself, with the same keyword arguments fire would pass, after doing its own SDK init and exporting once | The CLI's own `__main__` SDK init and export (edits #5/#6). **These have not been executed**, because they need CUDA; they only parse cleanly. |

**Behavior check.** The pristine `repo/` was copied to a throwaway directory and
run under the same `run_example.py` with only its SDK lines removed. Both runs had
byte-identical stub checkpoints and tokenizers. Their printed output, all six
dialogs and replies, was **identical** apart from the "Loaded in N seconds" timing
line. The throwaway copy was then deleted.

## 4. Reported gaps

1. **The model weights are not an input.** They are loaded in `Llama.build`, which
   the user did not pick. `generate` holds only an in-memory `Transformer`. The
   manifest does not say which weights produced the tokens. `model params` pins
   the architecture only.
2. **The tokenizer is not recorded.** The prompt and raw tokens are ids, and
   nothing in the manifest identifies the vocabulary that gives them meaning.
3. **There is no Ingest above the prompt tokens.** This is on purpose in HITL.
   Dialog text, `[INST]`/`<<SYS>>` formatting, the unsafe-tag flag and
   `Tokenizer.encode` (boxes J/D3/K/L) are not nodes, so the six prompt-token
   lists are roots.
4. **There is no Emit below the raw tokens.** This is on purpose in HITL.
   `Tokenizer.decode`, the `UNSAFE_ERROR` substitution and the print loop
   (D8/X/Y/Z) are not nodes. Dialog 6 (the unsafe prompt) has `raw generated
   tokens [5]` recorded, but the user was shown `UNSAFE_ERROR`. The manifest
   cannot show that substitution.
5. **Per-dialog pairing lives only in labels.** One batched `generate` call is one
   computation with 6 prompt inputs and 6 raw-token outputs. The edges do not say
   that `prompt tokens [k]` produced `raw generated tokens [k]`; only the `[k]` in
   the names does.
6. **The Code asset covers `generate`'s own text only.** It does not cover
   `Transformer.forward`, `sample_top_p`, `Tokenizer`, fairscale or torch (§6.0:
   source is not a dependency closure).
7. **Nondeterminism is recorded, not removed.** Sampler parameters and
   `torch.initial_seed()` are metadata. The RNG state at call time is not
   recorded, and the run is not claimed to be reproducible.
8. **Log-probs are not recorded.** With `logprobs=True`, `generate` also returns
   per-token log-probs. They are not recorded, because the user asked for raw
   tokens only. The `logprobs` flag itself is in the metadata.
9. **Library callers must initialise the SDK.** Any caller outside the two
   example CLIs that calls `Llama.generate` / `text_completion` /
   `chat_completion` without `eqty_sdk.init()` and an active signer now fails
   with the SDK's `Config not initialized` (a pyo3 `PanicException`, which is a
   `BaseException`). This is what the SDK being a hard dependency costs (§13.1),
   and a reviewer should decide whether it is acceptable.
10. **Multi-rank runs are untested.** With `--nproc_per_node > 1`, every rank
    records and exports its own `manifests/*.rank<N>.json`, sharing one `.eqty/`
    directory and one signer name. Concurrent use of that directory was not
    tested.
11. **Some paths were not exercised.** `example_text_completion.py` and the CLI
    `__main__` blocks were not run (see §3).
12. **None of the usual decorator gaps occur.** There are no `Custom` nodes, no
    generated names, no path-text inputs, no identifier inputs, no cycles and no
    multi-producer nodes (`check_graph.py` reports none).

## 5. Prediction vs result

| | Predicted (`llama.hitl.nodes.md`) | Manifest |
|---|---|---|
| Computations | 1 (`Llama.generate`, `model_call`) | 1 (`Llama.generate`, `model_call`, 8 in → 6 out) |
| Data nodes | 14 (1 Code, 1 Configuration, 6 prompt Token, 6 raw Token) | 14, typed exactly so |
| Connected components | 1 | 1 |
| Roots | 8 | 8 (Code, Configuration, 6 prompt tokens) |
| Leaves | 6 | 6 (raw generated tokens) |
| Shape | a star: 8 → generate → 6; no cycles, no multi-producer | as predicted |

**The prediction matched.**

Computation metadata as exported: `{"name": "Llama.generate", "computation_type":
"model_call", "description": "...", "temperature": 0.6, "top_p": 0.9,
"max_gen_len": 511, "logprobs": false, "echo": false, "torch_initial_seed": 1}`.

## 6. `check_graph.py` output

```
$ ./venv/bin/python eqty-instrument/check_graph.py out/llama.hitl.manifest.json --expect-computations 1 --expect-components 1
== out/llama.hitl.manifest.json
1 computations, 14 data nodes

-- every node as a reader sees it
  COMPUTE          Llama.generate                     b''
  Code             generate                           b'    @torch.inference_mode()\n    def generate'
  Configuration    model params                       b'{"dim": 64, "n_layers": 2, "n_heads": 4, "n_'
  Token            prompt tokens [0]                  b'[1, 77, 1852, 1960, 43, 206, 172, 16, 44, 38'
  Token            prompt tokens [1]                  b'[1, 77, 1852, 1960, 146, 1818, 880, 20, 32, '
  Token            prompt tokens [2]                  b'[1, 77, 1852, 1960, 1066, 1978, 1691, 1579, '
  Token            prompt tokens [3]                  b'[1, 77, 1852, 1960, 1066, 1978, 1691, 1579, '
  Token            prompt tokens [4]                  b'[1, 77, 1852, 1960, 1066, 1978, 1691, 1579, '
  Token            prompt tokens [5]                  b'[1, 77, 1852, 1960, 1814, 1209, 77, 1933, 18'
  Token            raw generated tokens [0]           b'[1432, 1168, 1076, 1578, 1385, 1265, 934, 81'
  Token            raw generated tokens [1]           b'[1437, 1557, 857, 1794, 1102, 953, 815, 1137'
  Token            raw generated tokens [2]           b'[344, 860, 944, 1646, 1637, 169, 429, 1801, '
  Token            raw generated tokens [3]           b'[999, 728, 1419, 1335, 1172, 1653, 1330, 145'
  Token            raw generated tokens [4]           b'[794, 0, 991, 1076, 660, 367, 46, 1605, 1051'
  Token            raw generated tokens [5]           b'[1026, 1099, 1305, 119, 1382, 1234, 1718, 13'

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
  [ok  ]   1 computations, as predicted

-- roots (8), data nodes nothing produced
  Token            prompt tokens [3]
  Token            prompt tokens [0]
  Configuration    model params
  Token            prompt tokens [4]
  Code             generate
  Token            prompt tokens [1]
  Token            prompt tokens [5]
  Token            prompt tokens [2]

-- leaves (6), data nodes nothing consumed
  Token            raw generated tokens [4]
  Token            raw generated tokens [2]
  Token            raw generated tokens [5]
  Token            raw generated tokens [1]
  Token            raw generated tokens [3]
  Token            raw generated tokens [0]

-- computations, in the order they were registered
  Llama.generate           model_call   8 in -> 6 out

all checks passed
```

Exit code 0. It reported no gap items, and every gap in §4 comes from reading the
code and the manifest, not from this script.

## 7. `references/eqtysdk.md` §11 checklist, worked

- [x] **SDK version and configuration are recorded.** `eqty_sdk==2.4.2`.
  `init(default_context=Context.new("llama hitl run_example"),
  custom_dir=out/llama-hitl/.eqty)` with `set_store_all_blobs(True)` (persisted in
  `.eqty/config.toml`). The builder and all assets pass `_store=True`.
  `EQTY_SKIP_PROOF` and `EQTY_TIMESTAMP` are unset. Signer: `Signer.load_or_create("llama")`,
  Ed25519, `did:key:z6MkuUbnDGUNb3goDfaAU53Y1Y3GzGYqQEBer8bWc7xPFv4f`.
- [x] **The covered entry point was run**, but only partly. `example_chat_completion.main` ran
  through `run_example.py` (stubbed, see §3). `example_text_completion` and the
  `__main__` blocks were not run, and the manifest does not claim them.
- [x] **Inputs, outputs and source were inspected against the actual work.** The
  Code blob is `generate`'s text, including the builder. The prompt Token blobs
  start `[1, 77, 1852, 1960, …]` (BOS followed by `[INST]`), and the raw
  outputs contain no prompt ids and no EOS, as box W trims them. Keyword arguments
  are carried as metadata. The implicit dependencies not captured are the weights,
  the tokenizer, `Transformer.forward`/`sample_top_p` source and the RNG state
  (gaps 1, 2, 6, 7).
- [x] **Statements and credentials verify.** `verify_statement` returned `True`
  for **60/60** statements (1 Computation, 14 Data, 15 Metadata, 30 Credential
  registrations). `verify_vc` returned `True` for **30/30** credentials, each
  called with `statement_id` = its `credentialSubject.id`, and each subject is a
  statement present in the manifest. This used the SDK's compiled-in contexts,
  offline. All credentials have a single issuer, which is also `operatedBy` and
  `registeredBy` on the computation. That DID was created locally for this run;
  **it is not authorized by anyone**, and no issuer policy was applied.
- [x] **All blobs are present and hash to their CIDs.** **29/29** blobs rehash to
  their CIDs: 14 raw BLAKE3 via `get_cid_for_bytes`, and 15 metadata JSON blobs
  via `get_cid_for_json` (JCS). Every one of the 14 data registrations has its
  preimage in the manifest, and none is unavailable.
- [x] **Missing steps, repeated content and connectivity were assessed.** There is
  one component, as predicted, with no repeated content, no cycles and no
  multi-producer nodes. The missing steps are gaps 1–5.
- [x] **Disclosure was reviewed.** The manifest carries `generate`'s full source
  and docstring, the stub model configuration, and every prompt and output token
  list. Anyone holding the tokenizer can decode those token lists back into text,
  so **the prompts are effectively disclosed**. In this run the tokenizer is a
  stub and the prompts are the repo's own example dialogs. It contains no secrets
  or keys, and the signer's private key stays in `.eqty/signers/`, which is
  excluded from the patch.

## 8. `references/mode2.md` review checklist

- [x] **The CFG was drawn by agents that never saw this file, `references/mode2ideas.md` or the SDK, and L2 was dispatched, not derived from L1.** Per `cfg/isolation.json`, two separate levels were run. I did not draw or redraw it.
- [x] **Both CFG agents were started by `isolated_cfg.py`, not as in-session subagents.** `isolation.json` sits beside the CFG in `cfg/`, names the agent (`claude`), and every check passes. It records one known residue: the account's userEmail line is injected at login.
- [ ] **The user was asked auto vs HITL at step 2, and asked nothing about nodes before it.** *Not ticked:* step 2 happened outside this session. I know the user chose HITL and I have their words, but I did not observe what they were asked beforehand, so I cannot attest to it.
- [x] **A node list exists, in writing, and predates the patch.** `out/llama.hitl.nodes.md` was written before `cp -R repo`.
- [x] **In HITL, the nodes added to chain the user's picks are listed, with why.** No computations were added. The data added to give the pick honest inputs (prompt tokens, model params), and what was deliberately left open, are listed in the node doc under "Chaining".
- [x] **Predicted node count, root count and graph shape were written down before the run, and compared against the manifest after it.** See §5.
- [x] **`eqty_sdk` is imported at top level, unconditionally.** There is no `try/except ImportError` and no plain-value asset fallback.
- [x] **There is no locally-defined compute decorator.** `eqty_sdk.compute` is not used at all: it cannot take `Llama.generate` (§1), so the SDK's own `Computation` builder is used directly, with its metadata inline.
- [x] **Every `@compute` sits on a function that exists in the pristine target.** This holds trivially, since there is no `@compute`. The only recording code is inside `Llama.generate`, in `llama/generation.py`, where it lives in the pristine target. There is no adapter, no `eqty_*` twin and no new module.
- [x] **The patch adds no new source file to the target.** The only new file is `run_example.py`, which is scaffolding and outside the target's own source.
- [x] **No node uses `Code.from_path` to point at "where the real logic lives".** The Code input is `Code.from_object(inspect.getsource(Llama.generate))`, taken inside `Llama.generate` itself.
- [x] **Nothing the skill wrote is instrumented.** `run_example.py` has no builder and no decorator, and it calls `main` with the same argument types fire passes.
- [x] **Any node that could not be placed on an original definition is reported as a gap, naming which rung was tried.** The node is on the original definition, at rung 3 (the builder inside the target function). §1 records why rungs 1 and 2 failed.
- [ ] **Every edit to the target is named in the patch with the §6.10 rule that allows it, and behavior is unchanged for every caller, including the target's own command line, which still records its runs.** *Not ticked, in part:*
  - Every edit is named with its rule in §2.
  - Behavior is verified unchanged only for the chat example's `main` under stubs, where the output was identical to pristine.
  - The CLI `__main__` recording path was not executed (it needs CUDA).
  - Library callers that do not initialise the SDK now fail (gap 9).
- [x] **There are no naming helpers, and no other new asset-building functions.** All assets are registered inline.
- [x] **No parameter list changed, and no parameter or return value was added that the function does not use.** Every edge comes from data `generate` really read or returned.
- [x] **The target repo's dependency file declares `eqty_sdk==<version>`.** `requirements.txt` declares `eqty_sdk==2.4.2`.
- [x] **Remaining `Custom` nodes, generated names, path-text inputs, identifier inputs and missing edges are listed as reported gaps.** There are none of the first four (gap 12). The missing edges are gaps 1–5.
- [x] **The SDK directory, with its signer key, is outside the user's tracked files.** For the run it is `out/llama-hitl/.eqty/`, which is excluded from the patch and covered by the patched `.gitignore`. The CLI uses `./.eqty/`, which is also gitignored.
- [x] **Connectivity matches the prediction.** 1 component, predicted 1.
- [x] **Every node's `computation_type` metadata is set.** It is `model_call`.
- [x] **§11 was worked, and `check_graph.py --expect-computations 1 --expect-components 1` passes.** It reported no items, and every gap is in §4.
- [x] **There is exactly one manifest, exactly as emitted.** There is no repaired or context-embedded second copy. An earlier manifest, from before the stub tokenizer's corpus was changed, was deleted before the final run. The final run exported once.
- [x] **Anything stubbed for the example is named, with what the manifest therefore does not claim.** See §3.

## 9. Reproducing

Versions: Python 3.12.8 on macOS 26.5.2 (no CUDA). eqty_sdk 2.4.2, torch 2.14.0,
fairscale 0.4.13, fire 0.7.1, sentencepiece 0.2.2, blake3 1.0.9. Everything was
run offline.

From the working directory (the one holding `repo/`, `venv/`, `eqty-instrument/`
and `out/`):

```sh
# working copy: ./llama-hitl is a symlink to out/llama-hitl, so the diff reads `repo` vs `llama-hitl`
# and the SDK directory created by the run stays inside out/llama-hitl
mkdir -p out && cp -R repo out/llama-hitl && ln -s out/llama-hitl llama-hitl
(cd llama-hitl && patch -p1 < llama.hitl.patch)     # only if starting from a pristine copy

# run (writes stub/, .eqty/ and ../llama.hitl.manifest.json)
(cd out/llama-hitl && rm -rf .eqty stub && ../../venv/bin/python run_example.py > run.log 2>&1)

# read back
./venv/bin/python eqty-instrument/check_graph.py out/llama.hitl.manifest.json --expect-computations 1 --expect-components 1

# patch
diff -ruN -x .eqty -x __pycache__ -x stub -x run.log -x llama.hitl.patch repo llama-hitl > out/llama-hitl/llama.hitl.patch
```

The signature, statement and blob checks in §7 were run as inline scripts, and
their results are quoted above:
- `eqty_sdk.verify_statement` on each statement;
- `eqty_sdk.verify_vc` on each credential, with its subject id;
- `get_cid_for_bytes` / `get_cid_for_json`, using a scratch SDK directory that was deleted afterwards.

A rerun creates a new signer, so the DIDs, timestamps and signatures will differ.
The token content is deterministic on the same machine: seeded weights, a
single-threaded tokenizer trainer, and `torch.manual_seed(1)` in `Llama.build`.

*Process note:* one check-output capture was briefly written to `/tmp` during the
session, outside the working directory, and deleted immediately. Nothing else was
written outside the working directory.
