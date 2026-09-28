"""Run the instrumented llama chat example on CPU, with no downloaded weights, and export a manifest.

Scaffolding written by the eqty-instrument skill. It is not part of the target and
carries no instrumentation of its own: the only node in the manifest is the one
inside llama/generation.py:Llama.generate.

What is stubbed (the boundary, not the logic):
  - weights:   a tiny Transformer (dim 64, 2 layers) with seeded random weights,
               saved as stub/ckpt/consolidated.00.pth + params.json;
  - tokenizer: a small SentencePiece model trained here on the text of
               the repo's Markdown docs (untouched by the patch), saved as
               stub/tokenizer.model;
  - hardware:  CUDA -> CPU (device="cuda" arguments, Tensor.cuda(), set_device,
               the cuda.HalfTensor default type, so float32 instead of fp16),
               and NCCL -> a gloo process group initialised here (world size 1).

Everything else runs as shipped: example_chat_completion.main with its six
dialogs and default flags, Llama.build (checkpoint glob, params.json,
model-parallel init, Tokenizer, Transformer, load_state_dict), chat_completion,
generate, Transformer.forward, sample_top_p and Tokenizer.decode.

    ../../venv/bin/python run_example.py      # from this directory
"""

import json
import os
import sys
from pathlib import Path

import torch

HERE = Path(__file__).resolve().parent
os.chdir(HERE)
sys.path.insert(0, str(HERE))

# --- hardware stub: CUDA -> CPU ---------------------------------------------------
_full, _tensor = torch.full, torch.tensor


def _cpu(kwargs):
    if str(kwargs.get("device", "")).startswith("cuda"):
        kwargs["device"] = "cpu"
    return kwargs


torch.full = lambda *a, **k: _full(*a, **_cpu(k))
torch.tensor = lambda *a, **k: _tensor(*a, **_cpu(k))
torch.Tensor.cuda = lambda self, *a, **k: self
torch.cuda.set_device = lambda *a, **k: None
_set_default_tensor_type = torch.set_default_tensor_type
torch.set_default_tensor_type = lambda t: None if "cuda" in str(t) else _set_default_tensor_type(t)

# --- distributed stub: NCCL -> gloo, world size 1 -----------------------------------
os.environ.update(WORLD_SIZE="1", LOCAL_RANK="0", RANK="0")
STUB = HERE / "stub"
STUB.mkdir(exist_ok=True)
(STUB / "gloo-store").unlink(missing_ok=True)
torch.distributed.init_process_group(
    "gloo", init_method=f"file://{STUB / 'gloo-store'}", rank=0, world_size=1
)

import sentencepiece as spm  # noqa: E402
import fairscale.nn.model_parallel.initialize as fs_init  # noqa: E402

from eqty_sdk import Context, Signer, init, set_active_signer  # noqa: E402
from llama.model import ModelArgs, Transformer  # noqa: E402
import example_chat_completion  # noqa: E402

# --- weights / tokenizer stub ---------------------------------------------------------
CKPT = STUB / "ckpt"
CKPT.mkdir(parents=True, exist_ok=True)
TOKENIZER = STUB / "tokenizer.model"

DOCS = ["README.md", "MODEL_CARD.md", "USE_POLICY.md", "CONTRIBUTING.md", "UPDATES.md"]  # untouched by the patch
corpus = [line for doc in DOCS for line in Path(doc).read_text().splitlines() if line.strip()]
spm.SentencePieceTrainer.train(
    sentence_iterator=iter(corpus),
    model_prefix="stub/tokenizer",  # relative: the path is embedded in the model file
    vocab_size=2000,
    hard_vocab_limit=False,
    model_type="bpe",
    character_coverage=1.0,
    num_threads=1,
    minloglevel=2,
)
n_words = spm.SentencePieceProcessor(model_file=str(TOKENIZER)).vocab_size()

params = {"dim": 64, "n_layers": 2, "n_heads": 4, "multiple_of": 16, "norm_eps": 1e-5, "vocab_size": -1}
(CKPT / "params.json").write_text(json.dumps(params))
fs_init.initialize_model_parallel(1)
model = Transformer(ModelArgs(max_seq_len=512, max_batch_size=8, **{**params, "vocab_size": n_words}))
g = torch.Generator().manual_seed(0)
state = {
    k: (torch.ones_like(v) if "norm" in k else torch.randn(v.shape, generator=g) * 0.02)
    for k, v in model.state_dict().items()
}
torch.save(state, CKPT / "consolidated.00.pth")
del model
fs_init.destroy_model_parallel()  # Llama.build initialises model parallel itself

# --- SDK: initialise once, run the target's own entry point, export once --------------
cfg = init(default_context=Context.new("llama hitl run_example"), custom_dir=HERE / ".eqty").set_store_all_blobs(True)
set_active_signer(Signer.load_or_create(name="llama"))

# Same call fire.Fire(main) makes for:
#   example_chat_completion.py --ckpt_dir stub/ckpt --tokenizer_path stub/tokenizer.model
example_chat_completion.main(ckpt_dir=str(CKPT), tokenizer_path=str(TOKENIZER))

manifest = HERE.parent / "llama.hitl.manifest.json"
cfg.get_default_context().export(manifest)
print(f"manifest: {manifest}")
