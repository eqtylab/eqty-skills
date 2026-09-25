# llama — HITL node selection

Written at step 3, before any code was touched.

## The user's pick (verbatim, from the user)

> "For llama, i just want the model call and then the result raw token"

This selection came from the user at step 2 (HITL). Nothing below adds a node
they did not ask for; see "Chaining" for what had to be filled in around it.

## The run this selection is against

As L1 stated it (carried verbatim into L2 by `isolated_cfg.py`); the user did not
name a different run:

> Run: `torchrun --nproc_per_node 1 example_chat_completion.py --ckpt_dir llama-2-7b-chat/ --tokenizer_path tokenizer.model` (entry: `example_chat_completion.py:main` via `fire.Fire`)

### Other runnable paths, uninstrumented or only partly covered

- `example_text_completion.py:main` → `Llama.text_completion` → `Llama.generate`.
  Not the run selected. Because the node lives inside `Llama.generate`, this path
  **does** emit the same single model-call node when run; its own steps
  (prompt encoding, decoding, printing) are not recorded.
- `Llama.generate` / `text_completion` / `chat_completion` called as a library with
  `logprobs=True` or `echo=True`. The model-call node is still recorded; the
  per-token log-probs are **not** recorded (the user asked for raw tokens only).
- `Llama.build` — distributed init, checkpoint + `params.json` load, tokenizer and
  Transformer construction. Not picked; not a node.
- `download.sh`, `setup.py` — not Python runs of the program; not instrumented.

## Mapping the pick onto the L2 boxes

| User's words | L2 boxes | Where it lives |
|---|---|---|
| "the model call" | **M** `generate()` pad prompts → **D5/N** (full-prompt branch, not taken) → **O/P/Q/R/D9/S** `Transformer.forward()` → **D6/T/U** sample/argmax → **V** write token → **D7** loop test | `llama/generation.py:Llama.generate` (lines 158–212), which drives `llama/model.py:Transformer.forward` and `sample_top_p` once per position |
| "the result raw token" | **W** trim each sequence to `max_gen_len` / first EOS | `llama/generation.py:Llama.generate` (lines 214–231) — the `out_tokens` list it returns, **before** D8/X/Y (`UNSAFE_ERROR` substitution and `Tokenizer.decode`) |

"The model call" is placed at the level of one `Llama.generate` call, not per
`Transformer.forward` step: O–S run once per generated position, which is a loop
body below the turn level (`mode2ideas.md` "Never"). One `generate` call is the
whole autoregressive model call for the batch — *"sampled up to N tokens for 6
prompts from the model"* passes the caption test.

"The result raw token" is not a separate function: box W is the tail of
`generate` itself, and its product is `generate`'s return value. So it becomes the
**output data** of the model-call node, not a second computation.

## Nodes

| # | Node | Archetype / `computation_type` | Inputs (recorded) | Outputs (recorded) | Why |
|---|---|---|---|---|---|
| 1 | `Llama.generate` — the model call | Model / oracle call — `model_call` | `generate` source (`Code`); model configuration `self.model.params` read at line 158 (`Configuration`); one prompt-token list per dialog (`Token` ×6) | one raw generated token-id list per dialog, after trim at `max_gen_len`/EOS (`Token` ×6) | The user's two picks. Tier 1: nondeterministic, the thing people question |

Sampler parameters and seed ride as **metadata** on node 1, not as data:
`temperature`, `top_p`, `max_gen_len`, `logprobs`, `echo`, and
`torch.initial_seed()` at call time (set by `Llama.build`'s
`torch.manual_seed(seed)`). This records what the run used; it does not make the
run reproducible.

## Chaining

The user's first pick (the model call) and last pick (its raw tokens) are the same
function's call and return, so **no intermediate computation is added** to connect
them. The chaining work here is the other half of HITL: **giving the first pick
honest inputs.** Added, as data only (no new computation):

- **Prompt tokens ×6** — the `prompt_tokens` argument `generate` really receives.
  Without them the node has outputs and no inputs.
- **Model configuration** — `self.model.params`, which `generate` reads to bound
  batch size and sequence length.

Deliberately **not** added (left open, as HITL leaves caller-side gaps open):

- No Ingest above the prompt tokens: the dialog text, the `[INST]`/`<<SYS>>`
  formatting and `Tokenizer.encode` (boxes J/D3/K/L) are not nodes, so the prompt
  tokens are roots with nothing above them.
- No Emit below the raw tokens: `Tokenizer.decode`, the `UNSAFE_ERROR`
  substitution and the print loop (D8/X/Y/Z) are not nodes, so the raw tokens are
  leaves with nothing below them. Note the unsafe dialog (#6) still gets raw tokens
  recorded; the user saw `UNSAFE_ERROR` instead, and that substitution is not
  recorded.
- The **model weights** are not an input. They are loaded in `Llama.build`
  (checkpoint `*.pth`), which was not picked; `generate` holds only the in-memory
  `Transformer`. Reported gap: the manifest does not identify which weights
  produced the tokens.

## Placement (preliminary — step 4 settles it)

`@compute` directly on `Llama.generate` is expected **not** to work:
- positional `self` is a `Llama`, which the SDK serializer takes down its
  `.model` branch (`json.dumps(model.state_dict())`) → `TypeError` before the
  function runs;
- the return value `(out_tokens, None)` is a tuple of lists of ints, which the
  decorator would explode into one `Custom` asset per token id;
- both callers pass `prompt_tokens` by keyword, so the decorator would not see it.

So the expected rung is the `Computation` builder **inside `Llama.generate`**,
hashing `prompt_tokens`, `self.model.params` and `out_tokens` at the points where
`generate` reads and produces them, with `generate`'s own source registered as
its `Code` input.

## Prediction (written before the run)

For the selected run (6 dialogs, one batched `chat_completion` → one `generate`):

- **Computations: 1** (`Llama.generate`, `computation_type: model_call`).
- **Data nodes: 14** — 1 `Code`, 1 `Configuration`, 6 prompt `Token`, 6 raw-output
  `Token` — assuming no two prompt or output token lists are byte-identical (the 6
  prompts are all distinct; a collision among outputs would lower the count and
  is not an error).
- **Connected components: 1.**
- **Roots: 8** (Code, Configuration, 6 prompt-token lists). **Leaves: 6** (the raw
  token lists).
- **Shape:** a single star — 8 inputs → `generate` → 6 outputs. No cycles, no
  multi-producer nodes.
