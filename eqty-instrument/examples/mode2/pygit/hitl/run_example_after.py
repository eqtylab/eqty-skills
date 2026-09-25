#!/usr/bin/env python3
"""Run the instrumented pygit against a stub git server and export a manifest.

Scaffolding, not part of pygit: this file is not in the pristine target, so it
carries no lineage nodes of its own. It initialises the SDK, drives pygit's own
entry points in the command sequence the CFG was pinned to --

    init -> add -> commit -> push

-- and exports the context pygit's own code filled in. Every node in the
manifest comes from the two `Computation` builders inside pygit.py: one in
`create_pack` (the pack), one in `push` (the POST).

It calls pygit exactly as pygit's own CLI does: plain strings and lists in,
`push(git_url, username=..., password=...)` with the credentials passed by
keyword. The builders never record the credentials either way.

pygit's own command line initialises the SDK itself (in its `push` branch,
into `.git/eqty_sdk`). Imported as a library, as here, it is the caller's job,
so this script does it before calling `push`.

WHAT IS STUBBED
---------------
The remote. `_ReceivePackHandler` below is ~40 lines of the smart-HTTP
receive-pack protocol: it answers `info/refs` with an empty repository and
answers the POST with `unpack ok` / `ok refs/heads/master`. Nothing else is
faked -- the objects, the index, the commit, the pack and the pkt-line framing
are all produced by pygit.

**The manifest is therefore not a claim that a real git server accepted this
push.** It records which loose object files the pack read, the pack bytes, the
ref-update commands and pack the POST body carried, and the reply the stub
sent back.

    python3 run_example.py [--manifest PATH] [--store DIR] [--keep]
"""

from __future__ import annotations

import argparse
import http.server
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

import eqty_sdk as sdk

HERE = Path(__file__).resolve().parent
DEFAULT_MANIFEST = HERE.parents[1] / "outputs" / "pygit" / "pygit.hitl.manifest.json"

ZERO = b"0" * 40


def pkt(payload: bytes) -> bytes:
    """One pkt-line: a 4-hex-digit length prefix covering itself."""
    return ("%04x" % (len(payload) + 4)).encode() + payload


FLUSH = b"0000"

INFO_REFS = (
    pkt(b"# service=git-receive-pack\n")
    + FLUSH
    + pkt(ZERO + b" capabilities^{}\x00report-status\n")
    + FLUSH
)
RECEIVE_PACK_OK = pkt(b"unpack ok\n") + pkt(b"ok refs/heads/master\n") + FLUSH


class _ReceivePackHandler(http.server.BaseHTTPRequestHandler):
    """The stubbed boundary: just enough git smart-HTTP to complete a push."""

    posted: list[bytes] = []

    def _send(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - http.server's spelling
        if "service=git-receive-pack" not in self.path:
            self.send_error(404)
            return
        self._send(INFO_REFS, "application/x-git-receive-pack-advertisement")

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        _ReceivePackHandler.posted.append(self.rfile.read(length))
        self._send(RECEIVE_PACK_OK, "application/x-git-receive-pack-result")

    def log_message(self, *args):
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--store", type=Path, default=HERE / ".eqty_sdk")
    parser.add_argument("--keep", action="store_true",
                        help="leave the scratch repository on disk")
    args = parser.parse_args()

    sys.path.insert(0, str(HERE))
    import pygit

    # One identity, one context, for this process. store_all_blobs keeps every
    # preimage in the manifest, so a reader can open what was committed to.
    config = sdk.init(default_context=sdk.Context.new("pygit push"),
                      custom_dir=str(args.store))
    config.set_store_all_blobs(True)
    sdk.set_active_signer(sdk.Signer.load_or_create(name="pygit_hitl"))

    server = http.server.HTTPServer(("127.0.0.1", 0), _ReceivePackHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    git_url = "http://127.0.0.1:{}/example.git".format(server.server_address[1])

    workdir = Path(tempfile.mkdtemp(prefix="pygit-hitl-"))
    cwd = Path.cwd()
    try:
        os.chdir(workdir)
        pygit.init("repo")
        os.chdir(workdir / "repo")

        pygit.write_file("README.md", b"# example\n\nA repository with two files.\n")
        pygit.write_file("hello.txt", b"hello from pygit\n")
        pygit.add(["README.md", "hello.txt"])
        pygit.commit("first commit", author="Example <example@example.com>")

        pygit.push(git_url, username="example", password="not-a-real-token")
    finally:
        os.chdir(cwd)
        server.shutdown()
        if not args.keep:
            shutil.rmtree(workdir, ignore_errors=True)

    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    config.get_default_context().export(args.manifest)
    print("wrote {}".format(args.manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
