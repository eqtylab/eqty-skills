# LoRA fine-tuning on banking77 — Mode 2, auto

A small LoRA fine-tune (`transformers` + `peft` + `datasets`) instrumented with
the current Mode 2 flow. **Nothing in it was run by the skill**: it read the code,
wrote the patch, and said what the patched program should record.

Read it in the order the skill produced it:

| Step | File | What it is |
|---|---|---|
| 1 | [`cfg/L1.md`](cfg/L1.md), [`cfg/L2.md`](cfg/L2.md) | The control-flow graph, drawn by two isolated agents that never heard of EQTY ([`cfg.isolation.json`](cfg.isolation.json) is the proof). The skill also writes these as `cfg.html`; the Markdown renders on GitHub without the 3 MB inlined renderer. |
| 1b | [`flow.json`](flow.json) | The data flow, read from the source by `static_flow.py` with pyright: which function's result feeds which, what is changed in place, what a bare `@compute` would raise on, and the recipe each hand-off needs. |
| 1c | [`merged.md`](merged.md) | The CFG joined with the data flow: the annotated L2 diagram, the cross-checks, the prediction. Its first line is the summary shown with the auto/HITL question. |
| 3 | [`auto/nodes.md`](auto/nodes.md) | The node selection: every L2 box accounted for, each node's inputs, outputs and recipe, and the gaps. |
| 4 | [`auto/changes.diff`](auto/changes.diff) | The patch: [`train_lora_before.py`](auto/train_lora_before.py) → [`train_lora_after.py`](auto/train_lora_after.py), plus [`requirements.txt`](auto/requirements_after.txt) declaring `eqty-sdk`. |
| 6 | [`auto/prediction.md`](auto/prediction.md) | How to run it, what the manifest should contain, the assumptions, and the checks to run on it. |

**What to look at first:** the dashed arrow in `merged.md`. The CFG draws the
model going from `get_peft_model()` straight to `save_adapter()`; the data flow
knows `trainer.train()` changes it in place first. The patch records the adapter
before and after training as two assets, so the edge into the save starts at the
training step.

There is no manifest here: the skill doesn't produce one. The prediction says
what it should look like when the patched script runs.
