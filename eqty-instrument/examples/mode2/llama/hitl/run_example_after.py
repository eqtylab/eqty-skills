"""Small, scripted run of `example_text_completion.py` on a laptop, no weights, no GPU.

Scaffolding only: nothing in this file is instrumented. It stubs three boundaries
and leaves every line of the target's own logic to run as shipped:

1. **Weights and tokenizer.** Llama 2 weights are licence-gated and 13 GB. This
   builds a tiny random checkpoint (`consolidated.00.pth` + `params.json`, same
   layout `Llama.build` reads) and trains a ~400-piece SentencePiece model.
   A manifest from this run therefore says nothing about any real Llama model:
   the tokens it records come from random weights.
2. **The GPU.** The target hard-codes CUDA (`nccl`, `torch.cuda.set_device`,
   `cuda.HalfTensor`, `device="cuda"`, `.cuda()`). Those calls are redirected to
   CPU / float32 / `gloo`, so the numbers are not what a GPU in fp16 would give.
3. **torchrun.** The rendezvous env vars a single-process `torchrun
   --nproc_per_node 1` would set are set here instead.

Then it runs the target's own command line, unchanged:

    example_text_completion.py --ckpt_dir <stub> --tokenizer_path <stub>
                               --max_seq_len 128 --max_batch_size 4

which initialises the SDK in its working directory (`.eqty/`), and exports
`.eqty/manifests/text_completion.json`. Run this from a scratch directory.

    python run_example.py [--repo <path to the llama checkout>]
"""

import argparse
import json
import os
import runpy
import sys
import tempfile
from pathlib import Path

import sentencepiece as spm
import torch

HERE = Path(__file__).resolve().parent

parser = argparse.ArgumentParser()
parser.add_argument("--repo", default=str(HERE))
args = parser.parse_args()
repo = Path(args.repo).resolve()

# -- 3. torchrun --------------------------------------------------------------------
os.environ.update(
    RANK="0", LOCAL_RANK="0", WORLD_SIZE="1", MASTER_ADDR="127.0.0.1", MASTER_PORT="29533"
)

# -- 2. the GPU boundary: CUDA -> CPU -----------------------------------------------
_init_pg = torch.distributed.init_process_group
torch.distributed.init_process_group = lambda backend=None, *a, **k: _init_pg("gloo", *a, **k)
torch.cuda.set_device = lambda *a, **k: None
torch.set_default_tensor_type = lambda *a, **k: None  # stays CPU float32
torch.Tensor.cuda = lambda self, *a, **k: self


def _on_cpu(fn):
    def call(*a, **k):
        if str(k.get("device", "")).startswith("cuda"):
            k["device"] = "cpu"
        return fn(*a, **k)

    return call


torch.full = _on_cpu(torch.full)
torch.tensor = _on_cpu(torch.tensor)

# -- 1. weights and tokenizer -------------------------------------------------------
stub = Path(tempfile.mkdtemp(prefix="llama-stub-"))
corpus = stub / "corpus.txt"
corpus.write_text(
    "\n".join(
        [
            "I believe the meaning of life is to find your gift.",
            "Simply put, the theory of relativity states that time is relative.",
            "A brief message congratulating the team on the launch.",
            "Hi everyone, I just wanted to say congratulations.",
            "Translate English to French: sea otter => loutre de mer",
            "peppermint => menthe poivree, plush girafe => girafe peluche, cheese => fromage",
        ]
        * 20
    )
)
spm.SentencePieceTrainer.train(
    input=str(corpus),
    model_prefix=str(stub / "tokenizer"),
    vocab_size=400,
    minloglevel=2,
    model_type="bpe",
    byte_fallback=True,
    character_coverage=1.0,
)
n_words = spm.SentencePieceProcessor(model_file=str(stub / "tokenizer.model")).vocab_size()

ckpt = stub / "ckpt"
ckpt.mkdir()
params = {"dim": 64, "n_layers": 2, "n_heads": 4, "multiple_of": 32, "norm_eps": 1e-5}
(ckpt / "params.json").write_text(json.dumps(params))

sys.path.insert(0, str(repo))
from llama.model import ModelArgs, Transformer  # noqa: E402  (the target's own class)

torch.distributed.init_process_group("gloo")
import fairscale.nn.model_parallel.initialize as fs_init  # noqa: E402

fs_init.initialize_model_parallel(1)
torch.manual_seed(0)
model = Transformer(ModelArgs(max_seq_len=128, max_batch_size=4, vocab_size=n_words, **params))
with torch.no_grad():  # the target builds with identity init; give the stub real random weights
    for name, p in model.named_parameters():
        p.copy_(torch.ones_like(p) if "norm" in name else torch.randn_like(p) * 0.2)
torch.save(model.state_dict(), ckpt / "consolidated.00.pth")
del model
fs_init.destroy_model_parallel()  # leave Llama.build to do its own setup
torch.distributed.destroy_process_group()

# -- the target's own command line --------------------------------------------------
sys.argv = [
    str(repo / "example_text_completion.py"),
    "--ckpt_dir", str(ckpt),
    "--tokenizer_path", str(stub / "tokenizer.model"),
    "--max_seq_len", "128",
    "--max_batch_size", "4",
]
runpy.run_path(sys.argv[0], run_name="__main__")
