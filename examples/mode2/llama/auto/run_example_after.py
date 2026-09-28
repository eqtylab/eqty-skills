"""Run the instrumented llama chat example end to end — scaffolding, carries no EQTY nodes.

Reproduces the run L1 diagrammed,

    torchrun --nproc_per_node 1 example_chat_completion.py --ckpt_dir llama-2-7b-chat/ --tokenizer_path tokenizer.model

on a CPU with no Llama 2 weights: the environment torchrun would set for one
process, the CPU/gloo stubs in example_stub.py, and a tiny fixture checkpoint and
tokenizer in place of the real ones. The target's own `__main__` block
initialises the SDK, runs `main` via fire, and exports the manifest to
$EQTY_MANIFEST (default .eqty/manifests/chat_completion.rank0.json).

    python run_example.py
"""

import os
import runpy
import socket
import subprocess
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
os.chdir(here)
sys.path.insert(0, str(here))

# what `torchrun --nproc_per_node 1` puts in the environment
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
os.environ.update(RANK="0", LOCAL_RANK="0", WORLD_SIZE="1", LOCAL_WORLD_SIZE="1",
                  MASTER_ADDR="127.0.0.1", MASTER_PORT=str(port))

fixture = here / "example_fixture"
if not (fixture / "llama-tiny-chat" / "consolidated.00.pth").exists():
    subprocess.run([sys.executable, "example_stub.py", "make-fixture", str(fixture)], check=True)

import example_stub  # noqa: E402,F401  installs the CPU/gloo stubs

sys.argv = ["example_chat_completion.py",
            "--ckpt_dir", str(fixture / "llama-tiny-chat") + "/",
            "--tokenizer_path", str(fixture / "tokenizer.model")]
runpy.run_path("example_chat_completion.py", run_name="__main__")
