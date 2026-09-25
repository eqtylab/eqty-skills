"""Small, self-contained run of the instrumented chat entry point (EQTY lineage example).

Scaffolding only. This file adds no lineage nodes: no @compute, no Computation builder.
It runs the pinned entry point, `example_chat_completion.main`, which is the instrumented
target and sets up the SDK and exports the manifest itself.

What is stubbed, at the boundary and never in the logic:
  * Weights: a tiny random Llama (dim 64, 2 layers, 4 heads) saved as a real `.pth` shard
    plus `params.json`. It is not Llama-2-7B-chat, so the manifest makes no claim about those
    weights, and the replies are noise.
  * Tokenizer: a small SentencePiece BPE model trained here on this repo's README and
    example script. It is not Meta's `tokenizer.model`.
  * Hardware: no CUDA and no NCCL. A one-process `gloo` group is created first, so
    `Llama.build` skips `init_process_group("nccl")`. `torch.cuda.set_device`,
    `Tensor.cuda`, `torch.set_default_tensor_type(cuda.HalfTensor)` and `device="cuda"`
    are redirected to CPU float32.

Usage:  python run_example.py --workdir /tmp/llama-run --manifest-out out/llama.auto.manifest.json
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

import torch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def make_stub_assets(stub_dir: Path) -> tuple:
    import sentencepiece as spm
    from fairscale.nn.model_parallel.initialize import destroy_model_parallel, initialize_model_parallel

    from llama.model import ModelArgs, Transformer

    stub_dir.mkdir(parents=True, exist_ok=True)
    corpus = stub_dir / "corpus.txt"
    corpus.write_text((HERE / "README.md").read_text() + "\n" + (HERE / "example_chat_completion.py").read_text())
    spm.SentencePieceTrainer.train(
        input=str(corpus), model_prefix=str(stub_dir / "tokenizer"), vocab_size=800,
        model_type="bpe", character_coverage=1.0, num_threads=1, minloglevel=2,
    )

    ckpt_dir = stub_dir / "ckpt"
    ckpt_dir.mkdir(exist_ok=True)
    params = {"dim": 64, "n_layers": 2, "n_heads": 4, "multiple_of": 32, "norm_eps": 1e-05}
    (ckpt_dir / "params.json").write_text(json.dumps(params))

    torch.manual_seed(0)
    initialize_model_parallel(1)
    model = Transformer(ModelArgs(max_seq_len=512, max_batch_size=8, vocab_size=800, **params))
    for name, p in model.named_parameters():
        with torch.no_grad():
            if p.dim() >= 2:
                p.normal_(0.0, 0.05)
    torch.save(model.state_dict(), ckpt_dir / "consolidated.00.pth")
    destroy_model_parallel()  # let Llama.build initialise model parallelism itself
    return ckpt_dir, stub_dir / "tokenizer.model"


def stand_in_for_cuda_and_nccl() -> None:
    os.environ.update(MASTER_ADDR="127.0.0.1", MASTER_PORT="29533", RANK="0", WORLD_SIZE="1", LOCAL_RANK="0")
    torch.distributed.init_process_group("gloo", rank=0, world_size=1)
    torch.cuda.set_device = lambda *a, **k: None
    torch.set_default_tensor_type = lambda *a, **k: None
    torch.Tensor.cuda = lambda self, *a, **k: self

    def on_cpu(fn):
        def call(*a, **k):
            if k.get("device") == "cuda":
                k["device"] = "cpu"
            return fn(*a, **k)
        return call

    torch.full = on_cpu(torch.full)
    torch.tensor = on_cpu(torch.tensor)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True, type=Path)
    ap.add_argument("--manifest-out", type=Path)
    args = ap.parse_args()
    workdir = args.workdir.resolve()
    manifest_out = args.manifest_out.resolve() if args.manifest_out else None

    stand_in_for_cuda_and_nccl()
    ckpt_dir, tokenizer_path = make_stub_assets(workdir / "stub")

    import example_chat_completion  # the pinned entry point, unmodified by this file

    os.chdir(workdir)  # .eqty/ and eqty-manifests/ land here, outside the repo
    example_chat_completion.main(str(ckpt_dir), str(tokenizer_path))

    emitted = workdir / "eqty-manifests" / "example_chat_completion.rank0.json"
    if manifest_out:
        manifest_out.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(emitted), str(manifest_out))  # moved, not rewritten
        emitted = manifest_out
    print(f"manifest: {emitted}")


if __name__ == "__main__":
    main()
