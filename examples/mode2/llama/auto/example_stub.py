"""Scaffolding for run_example.py — NOT part of the llama target and carries no EQTY nodes.

Stubs the hardware boundary only, so the target's real control flow runs on a
machine with no GPU and no Llama 2 weights:

* CUDA -> CPU: `torch.cuda.set_device` is a no-op, `Tensor.cuda()` returns the
  tensor, `device="cuda"` in `torch.full` / `torch.tensor` becomes CPU, and the
  `torch.cuda.HalfTensor` default tensor type becomes float32 CPU.
* NCCL -> gloo: `torch.distributed.init_process_group("nccl")` uses gloo.

Importing this module installs the stubs. Run it as a script to write the tiny
fixture that stands in for `llama-2-7b-chat/` and `tokenizer.model`:

    python example_stub.py make-fixture example_fixture
"""

import functools
import json
import os
import sys
from pathlib import Path

import torch
import torch.distributed

_init_process_group = torch.distributed.init_process_group
_set_default_tensor_type = torch.set_default_tensor_type
_full = torch.full
_tensor = torch.tensor


def _cpu(kwargs):
    if str(kwargs.get("device", "")).startswith("cuda"):
        kwargs["device"] = "cpu"
    return kwargs


@functools.wraps(_init_process_group)
def init_process_group(backend=None, *args, **kwargs):
    return _init_process_group("gloo" if backend == "nccl" else backend, *args, **kwargs)


def set_default_tensor_type(t):
    return _set_default_tensor_type(torch.FloatTensor if t is torch.cuda.HalfTensor else t)


torch.distributed.init_process_group = init_process_group
torch.set_default_tensor_type = set_default_tensor_type
torch.cuda.set_device = lambda device: None
torch.Tensor.cuda = lambda self, *args, **kwargs: self
torch.full = lambda *args, **kwargs: _full(*args, **_cpu(kwargs))
torch.tensor = lambda *args, **kwargs: _tensor(*args, **_cpu(kwargs))


def make_fixture(out_dir: Path) -> None:
    """Train a small SentencePiece model and save a randomly initialised tiny Transformer."""
    import sentencepiece as spm
    from fairscale.nn.model_parallel.initialize import initialize_model_parallel

    from llama.model import ModelArgs, Transformer

    here = Path(__file__).resolve().parent
    out_dir.mkdir(parents=True, exist_ok=True)
    corpus = out_dir / "corpus.txt"
    corpus.write_text(
        "\n".join(
            (here / name).read_text()
            for name in ["README.md", "MODEL_CARD.md", "USE_POLICY.md", "CONTRIBUTING.md",
                         "CODE_OF_CONDUCT.md", "UPDATES.md"]
        )
    )
    # Train from inside out_dir with relative paths: SentencePiece embeds `input`
    # and `model_prefix` in the model file, and that file is exported as a blob.
    cwd = os.getcwd()
    os.chdir(out_dir)
    try:
        spm.SentencePieceTrainer.train(
            input=corpus.name, model_prefix="tokenizer", vocab_size=1000,
            model_type="unigram", hard_vocab_limit=False, num_threads=1, pad_id=-1,
            bos_id=1, eos_id=2, unk_id=0, minloglevel=2,
        )
    finally:
        os.chdir(cwd)
    sp = spm.SentencePieceProcessor(model_file=str(out_dir / "tokenizer.model"))

    params = {"dim": 64, "multiple_of": 32, "n_heads": 4, "n_layers": 2, "norm_eps": 1e-05, "vocab_size": -1}
    ckpt_dir = out_dir / "llama-tiny-chat"
    ckpt_dir.mkdir(exist_ok=True)
    (ckpt_dir / "params.json").write_text(json.dumps(params))

    torch.distributed.init_process_group("nccl")
    initialize_model_parallel(1)
    torch.manual_seed(0)
    model = Transformer(ModelArgs(**{**params, "vocab_size": sp.vocab_size()}))
    # llama's parallel layers use init_method=lambda x: x, i.e. uninitialised memory
    with torch.no_grad():
        for p in model.parameters():
            torch.nn.init.normal_(p, std=0.02) if p.dim() > 1 else torch.nn.init.ones_(p)
    torch.save(model.state_dict(), ckpt_dir / "consolidated.00.pth")


if __name__ == "__main__" and sys.argv[1:2] == ["make-fixture"]:
    make_fixture(Path(sys.argv[2]))
