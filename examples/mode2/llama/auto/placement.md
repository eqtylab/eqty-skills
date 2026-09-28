# llama — Mode 2 (auto) placement, gaps and review

This is a **patch for a person to review**, not a commit. `./repo/` is not a git
repository and has no remote, so there is no branch and no pull request: the
diff is written out beside the source as `llama.auto.patch`.

| | |
|---|---|
| Target | `./repo/` (Meta `llama` 2 inference code), pristine; patched copy is `out/llama/` (`./llama` is a symlink to it, so the patch paths read `repo/…` → `llama/…`) |
| Mode | 2, **auto** (user's choice at step 2) |
| Run | `torchrun --nproc_per_node 1 example_chat_completion.py --ckpt_dir llama-2-7b-chat/ --tokenizer_path tokenizer.model` (L1's `Run:` line) |
| Node selection | `out/llama.auto.nodes.md`, written 12:05:45, before the first code edit at 12:06:46 |
| Manifest | `out/llama.auto.manifest.json`, exactly as exported (12:09:22), one copy |
| SDK directory | `out/llama/.eqty/` (blob store, `graphs.db`, signer `llama` with its private key). `.gitignore` excludes it |

## Manifest re-created after two edits (2026-09-25)

The isolated agent's run was followed by two edits, made by the orchestrator rather than
the agent, and then **only the example was re-run** to re-create the manifest. The node
selection and every other edit are the agent's.

- **Checkpoint declared by reference.** `Llama.build` registers the shard with
  `storage="by-reference"`, a `storage_reason` and an `obtain_from` beside `_store=False`
  (SKILL.md, *Commit to heavy artifacts by CID*). `eqty-manifest` now lists it as
  *by reference*, not as a missing pre-image, and `summary.py` exits 0.
- **Stub tokenizer trained with relative paths.** `example_stub.py make-fixture` trains
  from inside the fixture directory. The earlier manifest's tokenizer blob embedded the
  agent's absolute temp path through SentencePiece's `trainer_spec`, which the
  disclosure check below had missed: it searched the manifest's JSON, not the decoded
  blobs. The decoded blobs of this manifest hold no absolute paths.

The graph is unchanged: 4 computations, 1 component, and the same checkpoint CID (the
fixture is seeded). One fewer Metadata/Data registration than before: the
unsafe-request flags are one CID registered by two builders, and the earlier run's two
registrations fell in different seconds, so they did not collapse into one statement.

## Reproduce

```sh
cd out/llama
rm -rf .eqty example_fixture            # optional: start from nothing, as the recorded run did
EQTY_MANIFEST=../llama.auto.manifest.json ../../venv/bin/python run_example.py
../../venv/bin/python ../../eqty-instrument/check_graph.py ../llama.auto.manifest.json --expect-computations 4 --expect-components 1
```

`run_example.py` builds the fixture on first use (`python example_stub.py make-fixture example_fixture`),
sets the environment `torchrun --nproc_per_node 1` would set, installs the stubs, and runs
`example_chat_completion.py` as `__main__` with `--ckpt_dir example_fixture/llama-tiny-chat/ --tokenizer_path example_fixture/tokenizer.model`.
On a real GPU host with the real weights the instrumented program runs unchanged under
torchrun and writes `.eqty/manifests/chat_completion.rank<RANK>.json` (override with `EQTY_MANIFEST`; SDK directory with `EQTY_DIR`).

Patch: `diff -ruN -x .eqty -x __pycache__ -x example_fixture -x llama.auto.patch -x llama.auto.placement.md repo llama`
(run from the workspace root). It applies cleanly to a fresh copy of `repo/` with
`patch -p1` and reproduces the tree (checked).

**Versions:** Python 3.12.8 · `eqty_sdk` 2.4.2 · torch 2.14.0 · fairscale 0.4.13 ·
sentencepiece 0.2.2 · fire 0.7.1 · macOS (Darwin 25.5.0), CPU only. Signer: Ed25519
`did:key:z6Mkm8yDz8gyBV3vWiLbLa3XuHNCMnnf8QV61SQUQNDFPoJN` (a fresh `.eqty/` for the re-created manifest). `store_all_blobs = true`
(persisted in `.eqty/config.toml`); CID ignore rules at their defaults.

## Where each node lives, and why it is a builder

Every node is a `Computation` builder **inside the repo's own function**, in
`llama/generation.py`, the file where it already lives: rung 3 of the step-4 ladder.
No `@compute` is applied, and no function was added. Rungs 1 and 2 were tried
and do not work here:

- **Rung 1 (`@compute` + a §6.10 fix).** All three functions are methods of `Llama`
  or return one. `@compute` records positional arguments, so `self` (a `Llama`) is
  hashed. §7.1 serialises any object with a `.model` attribute as JSON of
  `.model.state_dict()`, which is tensors. That raises `TypeError` before
  `generate` / `chat_completion` run. `build` has the same problem with its return
  value, and there it would raise *after* the weights had loaded. The §6.10
  table's fix is "a supported equivalent only if every caller behaves identically".
  No such equivalent exists for a `Llama` instance. Also, both callers pass
  `generate`'s real inputs (`prompt_tokens=…`) and `build`'s (`ckpt_dir=…`) by
  keyword, so `@compute` would record none of them.
- **Rung 2 (`to_eqty_asset()` on `Llama`).** This would stop the `TypeError`, but it
  adds a function whose only job is to build an asset: a naming helper by §6.10
  rule 3. It would also still record nothing but `self` for `generate`. Not used.
- **Rung 3 (builder inside the function)** records what each function really reads
  and writes. Its `Code` input is `Code.from_object(inspect.getsource(<that very
  function>))`, the same text `@compute` would hash. It is not `Code.from_path`,
  and it does not point anywhere else.

| # | Node (`computation_type`) | Function | Inputs | Outputs |
|---|---|---|---|---|
| 1 | Load checkpoint and tokenizer (`ingest`) | `Llama.build` | Code `Llama.build`; `Model` checkpoint shard (file bytes, **CID only**); `Configuration` `params.json` (file bytes); `Binary` tokenizer model (file bytes) | `Configuration` `ModelArgs` as built |
| 2 | Screen and format dialogs (`transform`) | `Llama.chat_completion`, before `generate` | Code `Llama.chat_completion`; tokenizer (in-memory `serialized_model_proto()`); `Prompt` dialogs | `Dataset` prompt tokens; `Dataset` unsafe-request flags |
| 3 | Generate (`model_call`) | `Llama.generate` | Code `Llama.generate`; prompt tokens (as received); `ModelArgs` (`self.model.params`, which it reads) | `Dataset` generated tokens (+ log-probs when `logprobs=True`) |
| 4 | Decide unsafe and decode replies (`decide`) | `Llama.chat_completion`, after `generate` | Code `Llama.chat_completion`; tokenizer; generated tokens; unsafe flags (+ log-probs when requested) | `Document` chat predictions |

Metadata on NODE 1: `checkpoint_shard`, `model_parallel_size`, `seed`. On NODE 3:
`temperature`, `top_p`, `max_gen_len`, `logprobs`, `echo`, and
`seed = torch.initial_seed()`. These record the nondeterminism. They do not make
the run reproducible, and nothing here claims they do.

## Every edit, with the §6.10 rule that allows it

| # | File | Edit | Allowed by |
|---|---|---|---|
| E1 | `llama/generation.py` | `import inspect`; `from eqty_sdk import …` at module top level, unconditionally | §13.1: the SDK is a hard dependency, with no `try/except ImportError`. Rule 1 holds for the repo's own entry points, which initialise it (E5) |
| E2 | `llama/generation.py` `Llama.build` | builder after the `Loaded in …` print, so the printed time is unchanged: inputs are the shard at `ckpt_path`, `ckpt_dir/params.json` and `tokenizer_path`, the files `build` reads; output is the `model_args` it built. Return value untouched | §6.10 *data that moves through files*: a builder inside the function that reads them, hashing the bytes. Rule 2 (really read / really produced). Rule 3 (inline registration). Weights `_store=False`: SKILL.md "commit to heavy artifacts by CID, never by embedding them" |
| E3 | `llama/generation.py` `Llama.generate` | builder opened on entry, recording `prompt_tokens` and `params` as received and read; outputs `out_tokens` (and `out_logprobs` if requested) added just before the unchanged `return` | Rule 2, and rule 1: signature, return value and side effects are unchanged |
| E4 | `llama/generation.py` `Llama.chat_completion` | two builders, around the call to `generate`. The two `return [...]` expressions became `predictions = [...]`, then one `return predictions`. It is the same list, built the same way, for every caller. The `Code` and tokenizer assets are registered once per call and shared by both builders | Rule 1: identical return value. Rule 2: dialogs hashed as received, prompt tokens and flags as produced, generated tokens as returned, predictions as produced. Rule 3 |
| E5 | `example_chat_completion.py`, `example_text_completion.py` | `import os`, `Path`, and the SDK names. In the `__main__` block: `init(default_context=Context.new(...), custom_dir=.eqty).set_store_all_blobs(True)`, then `set_active_signer(Signer.load_or_create(name="llama"))`, then `fire.Fire(main)` as before, then `export(...)`. `main()` itself is untouched | mode2.md step 4: "initialising the SDK and a signer in the target's own entry point, so the shipped program records its own runs". `init` runs once, at process start-up. Both entry points record, so neither has instrumentation switched off |
| E6 | `requirements.txt` | `eqty_sdk==2.4.2` (`setup.py` reads this file) | §13.1 |
| E7 | `.gitignore` | `.eqty/` | mode2.md: keep the SDK directory and signer key out of tracked files |

**Stdout check.** With the same stubs and fixture, the pristine `repo/` and the
instrumented tree print byte-identical stdout, apart from the wall-clock
`Loaded in …` line. I checked this for both `example_chat_completion.py` and
`example_text_completion.py`. The text-completion run also exports a valid
manifest: 2 computations, 1 component.

New files, all scaffolding outside the `llama` package: `run_example.py`,
`example_stub.py`, and the generated `example_fixture/`, which is excluded from the
patch because it is binary and regenerated on demand. None of them carries a node.

## Stubbed — and what the manifest therefore does not claim

| Boundary | Real | Stand-in (`example_stub.py`) |
|---|---|---|
| GPU | CUDA, fp16 (`torch.cuda.HalfTensor`) | CPU, float32. `torch.cuda.set_device` no-op, `Tensor.cuda()` identity, `device="cuda"` → CPU in `torch.full`/`torch.tensor` |
| Collective backend | NCCL | gloo (same `init_process_group` call, backend swapped). The CFG's D1 "not initialised" branch still runs |
| Launcher | `torchrun --nproc_per_node 1` | env vars it sets (`RANK`, `LOCAL_RANK`, `WORLD_SIZE`, `MASTER_*`) in one process |
| Weights | `llama-2-7b-chat/consolidated.00.pth` + `params.json` | 2-layer, dim-64 Transformer built with the repo's own `llama.model`, randomly initialised (seed 0), `params.json` in Meta's format |
| Tokenizer | Meta's `tokenizer.model` | unigram SentencePiece, vocab 1000, trained on the repo's Markdown docs |

**The manifest is therefore not a claim about Llama 2.** It says nothing about the
real weights, tokenizer, fp16/GPU numerics or NCCL. The generated text is
gibberish from random weights. What it does attest is the target's real control
flow, node set and chaining, run through the repo's own code, and the exact
fixture bytes it ran on. The weights CID equals `get_cid_for_path` of the fixture
checkpoint (checked).

## Reported gaps

1. **No observed weights edge into `Generate`.** `generate` runs the weights through
   `self.model` but never holds their bytes. The checkpoint is linked to the model
   call only through NODE 1 and the `ModelArgs` configuration. The file is also
   loaded with `load_state_dict(strict=False)`, so the manifest commits to the
   file's bytes, not to exactly what was loaded.
2. **Weights are by reference.** A reader cannot re-hash the checkpoint without
   obtaining the file. This is the one data registration with no blob, declared as
   such (`storage="by-reference"`, with a reason and where to obtain it) under
   mode2.md's heavy-artifact exception to "`store=True` everywhere". The declaration
   is the signer's claim; it does not make the bytes checkable.
3. **Library callers must initialise the SDK.** The instrumented `Llama` methods
   need `init()` and an active signer. A third-party program that imports `llama`
   without doing that now fails (the SDK raises `Config not initialized` / `No active
   signer`). The repo's two entry points are unaffected. This is a behaviour change
   for external callers; it is left visible rather than shimmed, per §13.1.
4. **A run that stops records nothing.** If the role/last-message assertions (the
   gate in NODE 2) fail, or anything else raises, NODE 2 is never finalised and the
   `__main__` export is skipped. A stopped run leaves no manifest.
5. **`text_completion` is partly covered.** Its prompt encoding and decoding are not
   nodes. On that path the prompt tokens are a root with no producer, and the
   generated tokens are a leaf.
6. **Implicit inputs not recorded.** `generate` also reads the tokenizer's `pad_id` /
   `eos_id`; these are not a recorded input of NODE 3. RNG state beyond the initial
   seed is not recorded.
7. **Tokenizer identity rests on equal bytes.** Build hashes the file, and NODEs 2/4
   hash `sp_model.serialized_model_proto()`. For the fixture they are identical, so
   they form one node. For Meta's real `tokenizer.model` this is untested. If the
   bytes differ, the tokenizer appears as two nodes, still in one component.
8. **Hash-after-read.** NODE 1 hashes the three files after `torch.load` / `open`
   (§7.3). A file changed in between would be misattributed. With the real
   checkpoint this is also one more full read of about 13 GB per rank.
9. **Not nodes, by design.** `Transformer.forward`, `sample_top_p` and the per-token
   loop are loop bodies. `Tokenizer.encode`/`decode` are pure helpers. `main`'s
   printing is printing. stdout itself is not recorded; the predictions it prints
   are.
10. **Model parallel > 1 untested.** Each rank would record its own shard and
    export `chat_completion.rank<N>.json`, but all ranks share one `.eqty/`
    directory. Only MP = 1 was run.
11. **`Code` blobs include the instrumentation.** They are the functions as they now
    stand in `llama/generation.py`, `Computation` lines included (§6.0). They never
    hash to the upstream text.
12. `check_graph.py` reported none of: generated names, unnamed nodes, `Custom`
    nodes, multi-labelled nodes, path-text content, multiple producers or cycles.
    No path-text or identifier inputs exist. The manifest contains no absolute
    paths.

## Prediction vs result

| | Predicted (nodes.md) | Manifest |
|---|---|---|
| Computations | 4 | **4** |
| Connected components | 1 | **1** |
| Roots | 7 (8 if tokenizer split) | **7**: 3 Code, checkpoint, `params.json`, tokenizer, dialogs |
| Leaves | 1 | **1**: chat predictions |
| Data nodes | 12 (13 if split) | **12** |
| Shape | ingest → config → generate; format → {prompt tokens → generate, flags → decide}; generate → tokens → decide → predictions | as predicted |

The prediction matched.

## `check_graph.py` output

```
== out/llama.auto.manifest.json
4 computations, 12 data nodes

-- every node as a reader sees it
  COMPUTE          Decide unsafe and decode replies   b''
  COMPUTE          Generate                           b''
  COMPUTE          Load checkpoint and tokenizer      b''
  COMPUTE          Screen and format dialogs          b''
  Binary           SentencePiece tokenizer model      b'\n\x0e\n\x05<unk>\x15\x00\x00\x00\x00\x18\x02\n\x0c\n\x03<s>\x15\x00\x00\x00\x00\x18\x03\n\r\n\x04</s>\x15\x00\x00\x00\x00\x18'
  Code             Llama.build                        b'    @staticmethod\n    def build(\n        ckp'
  Code             Llama.chat_completion              b'    def chat_completion(\n        self,\n     '
  Code             Llama.generate                     b'    @torch.inference_mode()\n    def generate'
  Configuration    Model configuration (ModelArgs)    b'{"dim": 64, "n_layers": 2, "n_heads": 4, "n_'
  Configuration    params.json                        b'{"dim": 64, "multiple_of": 32, "n_heads": 4,'
  Dataset          Generated tokens                   b'[[230, 181, 683, 400, 965, 783, 423, 493, 55'
  Dataset          Prompt tokens                      b'[[1, 22, 175, 996, 102, 116, 446, 3, 87, 219'
  Dataset          Unsafe-request flags               b'[false, false, false, false, false, true]'
  Document         Chat predictions                   b'[{"generation": {"role": "assistant", "conte'
  Model            Checkpoint shard consolidated.00.pth b''
  Prompt           Dialogs                            b'[[{"role": "user", "content": "what is the r'

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
  [ok  ]   4 computations, as predicted

-- roots (7), data nodes nothing produced
  Binary           SentencePiece tokenizer model
  Prompt           Dialogs
  Code             Llama.generate
  Model            Checkpoint shard consolidated.00.pth
  Configuration    params.json
  Code             Llama.chat_completion
  Code             Llama.build

-- leaves (1), data nodes nothing consumed
  Document         Chat predictions

-- computations, in the order they were registered
  Generate                 model_call   3 in -> 1 out
  Screen and format dialogs transform    3 in -> 2 out
  Load checkpoint and tokenizer ingest       4 in -> 1 out
  Decide unsafe and decode replies decide       4 in -> 1 out

all checks passed
```

The check tool lists computations by statement key, not by execution order. The
real order is ingest → screen → generate → decide.

## SDK verification (offline, `eqty_sdk` 2.4.2)

- Statements: 12 `DataRegistration`, 16 `MetadataRegistration`, 4
  `ComputationRegistration`, 42 `CredentialRegistration` (re-created manifest).
- `verify_statement`: **74 / 74** true.
- `verify_vc`: **42 / 42** true. Each is bound (`statement_id=`) to its
  `credentialSubject.id`, and every one of the 32 non-credential statements is
  covered by a credential. There is one issuer, the signer above. Whether that
  issuer is *authorised* is the reader's policy, not checked here.
- Blobs: all **27** re-hash to their CID. 11 are raw bytes (`get_cid_for_bytes`)
  and 16 are JSON-codec metadata/context blobs (`get_cid_for_json`). One data
  registration has no blob: the checkpoint, declared by reference (gap 2).
- `Code` blobs: all three are verbatim substrings of `out/llama/llama/generation.py`
  (`Llama.build`, `Llama.generate`, `Llama.chat_completion`). None is an adapter.

## `references/eqtysdk.md` §11, worked

- [x] SDK version and storage/context configuration recorded: 2.4.2,
      `store_all_blobs = true`, default CID ignore rules, one default context
      `llama chat_completion`, weights `_store=False` and declared `storage="by-reference"`.
- [x] Ran the entry point the instrumentation claims to cover (chat). The text
      entry point was also run in a scratch check.
- [x] Inspected inputs, outputs and source. Keyword arguments do not matter here
      because builders enumerate inputs explicitly. Implicit dependencies left out
      are listed (gaps 1, 6).
- [x] Statement IDs and credentials verified. Credentials are bound to their
      subjects. Issuer authorisation is left to the reader.
- [x] Blobs checked with the right codec. The unavailable checkpoint content is
      reported, not claimed.
- [x] Missing steps, repeated content and connectivity assessed against the
      intended workflow. There is one component, no cycles and no multiple
      producers.
- [x] Disclosure reviewed. The manifest embeds the dialogs, prompts, generations,
      tokenizer, `params.json`, source text and metadata, and no weights. The
      **decoded** blobs were searched for absolute paths and user names: none (the
      first manifest failed this, through the tokenizer; see the top section). Only
      the scope above is claimed.

## `references/mode2.md` review checklist

- [x] The CFG was drawn by agents that never saw mode2.md, mode2ideas.md or the SDK, and L2 was **dispatched**. This is per `cfg/isolation.json`, from a run done before this session; I did not redraw it.
- [x] Both CFG agents were started by `isolated_cfg.py`. `isolation.json` sits beside the CFG in `./cfg/`, names `claude`, and every check passes. It records one known residue: the account email line injected at login.
- [x] The user was asked auto vs HITL at step 2 and chose auto, as stated in the task; nothing about nodes was asked.
- [x] A node list exists in writing and predates the patch (12:05:45 vs 12:06:46).
- [ ] In HITL: chaining nodes listed. **N/A: auto run.**
- [x] Predicted node count, root count and shape were written before the run, and compared above.
- [x] `eqty_sdk` is imported at top level, unconditionally.
- [x] No locally defined compute decorator. `eqty_sdk.compute` is **not applied at all**, because rung 1 fails for every node (see above); the builder metadata is written inline.
- [x] Every node sits in a function that exists in the pristine target, with the same name and file (`llama/generation.py`: `Llama.build`, `Llama.generate`, `Llama.chat_completion`). There are no `@compute` decorators, so none can be misplaced.
- [x] The patch adds no new source file to the target. `run_example.py` and `example_stub.py` are scaffolding outside the `llama` package.
- [x] No `Code.from_path`. Each `Code` is the running function's own `inspect.getsource`.
- [x] Nothing the skill wrote is instrumented. The runner calls the target's CLI with the target's own argument types (`--ckpt_dir` / `--tokenizer_path` strings).
- [x] Every node's rung is reported: all four are rung 3 (builder inside the target function), after rung 1 and rung 2 were rejected.
- [ ] **Behaviour is unchanged for every caller: only partly true.** Every edit is named with its §6.10 rule. Both of the target's command lines behave as before (identical stdout) and record their runs. A *third-party* program that imports `llama` without initialising the SDK now fails (gap 3), so the box stays unticked.
- [x] No naming helpers and no other new asset-building functions. `to_eqty_asset` was rejected for exactly this reason.
- [x] No parameter list changed, and no parameter or return value added. `chat_completion`'s return was restructured but returns the same value. Every edge comes from data that really flowed.
- [x] `requirements.txt` declares `eqty_sdk==2.4.2`.
- [x] Remaining gaps are listed. There are no `Custom` nodes, generated names, path-text or identifier inputs. Missing edges and implicit inputs are gaps 1, 5 and 6.
- [x] The SDK directory with its signer key (`.eqty/`) is excluded by `.gitignore`. The repo is not under git here, so "tracked" means "excluded by `.gitignore`".
- [x] Connectivity matches the prediction: 1 component.
- [x] Every node's `computation_type` is set.
- [x] The one `_store=False` registration (the checkpoint) carries `storage="by-reference"` and a `storage_reason`, is weights in real use, and is gap 2.
- [x] No decoded blob holds an absolute path or a user name.
- [x] §11 worked, and `check_graph.py … --expect-computations 4 --expect-components 1` passes with nothing reported.
- [x] Exactly one manifest, exactly as emitted. Trial-run manifests from development went to `scratch/` and were deleted.
- [x] Everything stubbed for the example is named above, together with what the manifest therefore does not claim.

## Housekeeping notes

- One of my scratch comparison runs imported the pristine `repo/llama` package, and
  Python wrote `repo/llama/__pycache__/`. I deleted that directory. `repo/`'s file
  contents are pristine again, but the `repo/llama` directory's mtime changed.
- Handoff: the manifest is `out/llama.auto.manifest.json`. The read-side
  `eqty-manifest` skill is run separately by someone else.
