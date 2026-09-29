# lora_banking77 — node selection (auto)

**Path (as L1 stated it, carried verbatim to L2; the CFG was drawn before the line was renamed, so it reads `Run:`):**
`python train_lora.py --base <hf model id> --out runs/lora` (entry: `train_lora.py:main`
via the `__main__` guard)

The path was chosen by L1 from the code; the user did not name one. Mode: **auto**
— every node the CFG proposes, filtered only by `references/mode2ideas.md`.

**Other paths:** none; the script has one entry point. Importing its
functions from another program runs the instrumented code, but that caller must
set up the SDK itself (gap 5).

## CFG boxes → lineage nodes

Every L2 box is accounted for. The data-flow facts come from `../merged.md`.

| L2 box(es) | Archetype (mode2ideas) | Decision | Why |
|---|---|---|---|
| A `main()` — parse CLI and orchestrate | CLI parsing | **no node** | Never: config and CLI parsing. SDK start-up and export go at the entry point instead. |
| B, C, D `load_dataset()`, `shuffle().select()`, `select()` | **Ingest** (Tier 1) | **NODE 1** | Outside world → in-process: the root of the data. One function, `load_banking77`. |
| E `AutoTokenizer.from_pretrained()` | Ingest | **no node of its own** | It is inline in `main`, with no function to carry a node (cross-check in `merged.md`). The tokenizer is recorded as an input of NODE 2, from its own bytes. |
| F `tokenize()` | Transform, the named stage | **NODE 2** (called twice) | "Tokenized the train and eval splits." |
| G `tokenizer()` in the `map` lambda | loop body | **no node** | Never: called once per batch. |
| H1, H2, H3 load base model, `LoraConfig()`, `get_peft_model()` | Transform | **NODE 3** | One function, `build_model`: "wrapped the base model with LoRA adapters". |
| I1–I4 `TrainingArguments()` … `trainer.train()` | **Fit / train** (Tier 1) | **NODE 4** | One function, `train`. It changes the model in place (`merged.md`, likely). |
| J `evaluate()` | Evaluate, the number gets quoted | **NODE 5** | The metrics are the reason the run is recorded. |
| K `save_adapter()` | **Emit** (Tier 1) | **NODE 6** | The adapter leaves the process: what someone downstream holds. |
| L `write_metrics()` | **Emit** | **NODE 7** | `metrics.json` leaves the process. |

Count test: seven bullets to a manager — *loaded banking77; tokenized it; built
the LoRA model; trained it; evaluated it; saved the adapter; wrote the metrics.*
Seven nodes, eight computations (`tokenize` runs twice).

## The nodes

A bare `@compute` raises on every one of these functions (`merged.md`), so each
node is a `Computation` builder inside the function, hashing what it reads and
writes by the `references/eqtysdk.md` §6.11 recipe named. Producers register a
typed, named asset; consumers add the CID. Each builder also takes the function's
own source as a `Code` input, `inspect.getsource(<function>)`, which is what
`@compute` would have recorded; it is not repeated in the table.

| # | Node (`computation_type`) | Lives in | Inputs (really read) | Outputs (really produced) | Recipe |
|---|---|---|---|---|---|
| 1 | Load banking77 (`ingest`) | `train_lora.py:load_banking77` | the source splits' Arrow files (by reference) | train rows, eval rows | `hf-dataset-source`, `hf-dataset` |
| 2 | Tokenize (`transform`) | `train_lora.py:tokenize` | rows, the tokenizer | tokenized rows | `hf-dataset`, `hf-tokenizer` |
| 3 | Build LoRA model (`transform`) | `train_lora.py:build_model` | base weights (by reference), LoRA config | the initial adapter | `hf-base-weights`, `config-with-sets`, `peft-model` |
| 4 | Train LoRA adapter (`transform`) | `train_lora.py:train` | the initial adapter, tokenized train rows, training arguments | the trained adapter | `peft-model`, `hf-dataset`, `hf-training-args` |
| 5 | Evaluate (`transform`) | `train_lora.py:evaluate` | the trained adapter (`trainer.model`), tokenized eval rows | eval metrics | `hf-trainer` → `peft-model`, `hf-dataset` |
| 6 | Save adapter (`emit`) | `train_lora.py:save_adapter` | the trained adapter | `adapter_model.safetensors`, `adapter_config.json` | `peft-model`, `none-return`, `path-arg` |
| 7 | Write metrics (`emit`) | `train_lora.py:write_metrics` | eval metrics | `metrics.json` | `none-return`, `path-arg` |

Expected edges, all from data that really flows:

- 1 → 2: the train and eval rows, as parquet bytes, on both sides.
- 2 → 4 and 2 → 5: the tokenized train and eval rows.
- 3 → 4: the initial adapter's weights.
- 4 → 5: the trained adapter, which `evaluate` reads through `trainer.model`.
- 4 → 6: the trained adapter. The CFG draws this from `get_peft_model()`; the
  data flow says `train` changed it in place, so it is hashed after training.
- 5 → 7: the metrics.

## Gaps (reported, not fixed)

1. **The tokenizer's load is not a computation.** It happens inline in `main`, so
   the tokenizer is a root: its bytes are recorded, not the step that loaded it.
2. **`Code` is each function's text, as `@compute`'s is.** It includes the
   instrumentation lines, so it never matches the upstream function, and it is not
   a closure over the helpers and libraries the function calls (§6.0).
3. **Base weights are recorded only when cached as one `model.safetensors`** (or
   in a local directory). A sharded or `.bin`-only base is left out rather than
   crashing the run.
4. **By reference:** the base weights and both full source splits. Their bytes are
   not in the manifest; `obtain_from` says where they are.
5. **Library use:** a program importing these functions must set up the SDK and a
   signer itself; the patch does that only at the script's own entry point.
6. **Cost:** every hand-off of a dataset hashes it as parquet. Fine for banking77;
   for a large dataset, hash at ingest and emit only, and report the rest
   (`references/eqtysdk.md` §6.11).
