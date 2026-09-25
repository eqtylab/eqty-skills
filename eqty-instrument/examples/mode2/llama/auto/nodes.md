# llama — node selection (auto)

**Run (as L1 stated it, carried verbatim to L2):**
`torchrun --nproc_per_node 1 example_chat_completion.py --ckpt_dir llama-2-7b-chat/ --tokenizer_path tokenizer.model` (entry: `example_chat_completion.py:main` via `fire.Fire`)

The run was chosen by L1 from the code; the user did not name one. Mode: **auto**
(user's choice at step 2) — every node the CFG proposes, filtered only by
`references/mode2ideas.md`.

**Other runnable paths — not the run this selection is about:**

| Path | Status in the patch |
|---|---|
| `example_text_completion.py:main` → `Llama.text_completion` | **Partly covered.** It shares `Llama.build` and `Llama.generate`, which are instrumented in place, so it records the ingest and the model call; its own prompt encoding and decoding in `text_completion` are **not** nodes (not in the CFG's run). Its prompt tokens therefore appear as a root with no producer. It gets the same SDK start-up as the chat entry point, so its command line still works. |
| `generate` / `chat_completion` / `text_completion` with `logprobs=True` or `echo=True` | Not exercised. `generate`'s node records log-probs as an extra output when they are requested; the chat logprobs branch of the decode goes through the same recorded step. Not run, so not verified. |
| Library use (`from llama import Llama`) from a caller's own program | Instrumented code runs, but the caller must initialise the SDK and set a signer; see the gap list in the placement doc. |
| `download.sh`, `setup.py` | Uninstrumented (shell download / packaging). |

## CFG boxes → lineage nodes

L2 is the level selected from. Every L2 box is accounted for.

| L2 box(es) | Archetype (mode2ideas) | Decision | Why |
|---|---|---|---|
| A `main()` — fire parses CLI | CLI parsing | **no node** | Never: config and CLI parsing. SDK start-up and export go here (entry point), not a node. |
| D1, B, D2, C, E — process group, model parallel, device, seed, mute ranks | config / setup | **no node** | Never: config. Seed and model-parallel size ride as metadata on the ingest node and on the model call (seed). |
| F load `*.pth` + `params.json` | **Ingest** (Tier 1) | **NODE 1** (with G, H, I) | Outside world → in-process: where trust enters. Root of the DAG. |
| G `Tokenizer.__init__()` | Ingest, but an `__init__` | folded into NODE 1 | Never: `__init__`. The tokenizer file it reads is recorded as an input of NODE 1 (its parent, `Llama.build`, which calls it). |
| H `Transformer.__init__()` | `__init__` | folded into NODE 1 | Never: `__init__`. |
| I `load_state_dict()` — return Llama | Ingest | folded into NODE 1 | Same function (`Llama.build`), same stage. |
| J `chat_completion()` — flag unsafe tags | **Validate / gate** + Transform | **NODE 2** (with D3, K, L, D4) | Named stage: "screened and formatted the six dialogs into prompt tokens". The unsafe flag it computes is what the later Decide branches on. |
| D3, K merge system prompt | Transform | folded into NODE 2 | Part of the same named stage; one line of branching inside it. |
| L assert roles, `Tokenizer.encode()` | Transform / Validate | folded into NODE 2 | `Tokenizer.encode` is a pure helper called per turn: Never (pure, below the stage level). The role assert is the gate that can stop the run. |
| D4 more dialogs? | loop | folded into NODE 2 | Loop over dialogs inside the stage. |
| M `generate()` — pad prompts into buffer | **Model call** (Tier 1) | **NODE 3** (with D5, N, O–S, D6, T, U, V, D7, W) | Nondeterministic model call — the thing people question. One node for the whole generation. |
| D5, N full-prompt forward + logprobs | inside model call | folded into NODE 3 | Branch inside `generate`. |
| O–S, P, Q, R, D9 `Transformer.forward` / block / attention / FFN / norm | loop body | **no node** | Never: loop bodies below the turn level (`forward(batch)`), called once per token position. |
| D6, T `sample_top_p()`, U argmax | loop body | **no node** | Never: per-token loop body. Temperature and top-p ride as metadata on NODE 3 (nondeterminism recorded, not removed). |
| V write token, D7 stop test, W trim | inside model call | folded into NODE 3 | Statements of `generate`. |
| D8 dialog flagged unsafe? | **Decide** (Tier 1) | **NODE 4** (with X, Y) | A branch taken on data: the generation is discarded and replaced by `UNSAFE_ERROR`. "Why did dialog 6 get an error?" is answerable only with a node here. |
| X content = UNSAFE_ERROR | Decide | folded into NODE 4 | The branch's outcome. |
| Y `Tokenizer.decode()` | Transform | folded into NODE 4 | Pure helper per sequence: Never on its own; the decode stage it belongs to is NODE 4. |
| Z `main()` print loop | printing | **no node** | Never: printing. The predictions it prints are NODE 4's recorded output; stdout itself is not recorded. |

Tie-breakers applied: `chat_completion` (parent) and `generate` (child) both
qualify; the child is Tier 1 (model call), so both are kept and the nesting is
deliberate — `chat_completion` is recorded as two steps (before and after the
model call) so the data really flows format → generate → decide.

Count test: four bullets to a manager — *loaded the checkpoint and tokenizer;
screened and formatted six dialogs; generated replies; replaced the unsafe one
and decoded the rest.* Four nodes.

## The nodes

Placement is step 4's job; the functions named here are the repo's own
definitions, in the files where they live.

| # | Node (`computation_type`) | Lives in | Inputs (really read) | Outputs (really produced) |
|---|---|---|---|---|
| 1 | Load checkpoint and tokenizer (`ingest`) | `llama/generation.py:Llama.build` | the rank's checkpoint shard file (weights, by CID only); `params.json`; the tokenizer model file; `Llama.build` source (Code) | the model configuration `ModelArgs` built from `params.json` + CLI limits + tokenizer vocab |
| 2 | Screen and format dialogs (`transform`) | `llama/generation.py:Llama.chat_completion` (before `generate`) | the dialogs; the tokenizer as loaded; `chat_completion` source (Code) | prompt token lists; per-dialog unsafe flags |
| 3 | Generate (`model_call`) | `llama/generation.py:Llama.generate` | prompt token lists; model configuration; `generate` source (Code). Metadata: temperature, top-p, max_gen_len, logprobs, echo, seed | generated token lists (+ log-probs when requested) |
| 4 | Decide unsafe / decode replies (`decide`) | `llama/generation.py:Llama.chat_completion` (after `generate`) | generated token lists; unsafe flags; the tokenizer as loaded; `chat_completion` source (Code) | the chat predictions (assistant messages) |

Expected edges, all from data that really flows:
- 1 → 3: the `ModelArgs` object `build` creates is the `self.model.params` that `generate` reads (batch and length limits).
- 2 → 3: `prompt_tokens` is passed from `chat_completion` to `generate`.
- 3 → 4: `generation_tokens` is returned from `generate` to `chat_completion`.
- 2 → 4: `unsafe_requests` is computed in the first half of `chat_completion` and read in the second.
- tokenizer → 1, 2, 4: the tokenizer bytes as read by `build` and as held in memory by the formatting and decoding steps (equal bytes expected; if they are not, the tokenizer appears twice, still in the same component).

Known before the run to be missing (reported, not fixed): the **weights** that
`generate` runs are linked to the checkpoint only through NODE 1 and the model
configuration — `generate` does not receive the weights' bytes, so no weights
edge into NODE 3 is observed.

## Prediction (written before the run)

- **Computations: 4** (one per node above; the six dialogs are one batch, one call each).
- **Connected components: 1.**
- **Roots: 7** — Code(`build`), Code(`chat_completion`), Code(`generate`), checkpoint shard, `params.json`, tokenizer model, dialogs. (8 if the in-memory tokenizer bytes differ from the file.)
- **Leaves: 1** — the chat predictions.
- **Data nodes: 12** (13 with a split tokenizer).
- **Shape:** a diamond-ish DAG — `{ckpt, params, tokenizer} → ingest → config → generate`; `{dialogs, tokenizer} → format → {prompt tokens → generate, unsafe flags → decide}`; `generate → generated tokens → decide → predictions`.

Stubbing expected at step 5 (named in full in the placement doc): no GPU and no
Llama weights here — CUDA device placement mapped to CPU, NCCL mapped to gloo,
and a tiny randomly-initialised checkpoint plus a locally trained SentencePiece
model stand in for `llama-2-7b-chat/` and `tokenizer.model`. The real control
flow, node set and chaining execute; the manifest is **not** a claim about the
Llama 2 weights or tokenizer.
