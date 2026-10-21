#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""explorer.py — show an EQTY manifest in the lineage explorer and drive it.

    python3 explorer.py serve <manifest.json> [--no-open]   start (in the background), print the URL
    python3 explorer.py nodes [--json]                       every node: id, type, name, hidden?
    python3 explorer.py ids <selector>                       node ids for a question (see below)
    python3 explorer.py only <id>... [--note TEXT]           show these, hide the rest
    python3 explorer.py highlight <id>... [--note TEXT]      the same as `only` (the explorer can't dim yet)
    python3 explorer.py hide <id>...                         take nodes off the canvas
    python3 explorer.py show [<id>...]                       put them back (no ids: all)
    python3 explorer.py query TEXT [--note TEXT]             the explorer's own query (see below)
    python3 explorer.py select <id>                          select one node, open its sidebar
    python3 explorer.py reset                                everything visible, nothing selected
    python3 explorer.py selection                            the node the user last clicked, and their query
    python3 explorer.py trust <did>                          chain of trust for that machine
    python3 explorer.py note [TEXT]                          banner text (none: clear it)
    python3 explorer.py status | stop

Selectors for `ids` (one per call; add --with-io to include a computation's
inputs and outputs):
    --upstream ID        ID and everything it was derived from
    --downstream ID      ID and everything derived from it
    --lineage ID         both
    --between A B        every node on a path from A to B
    --on DID             computations whose executedOn is DID
    --signer DID         nodes whose registration was signed by DID
    --computations       every computation
    --missing            data nodes whose bytes are not in the manifest

Ids are `urn:cid:...` node ids, or the bare address. Pass `-` to read ids from
stdin, so `explorer.py only - < ids.txt` and `explorer.py only $(explorer.py ids
--upstream X)` both work.

`only`, `hide` and `show` become one explorer query naming node ids, so this
script always knows what is on the canvas. `query` passes the explorer's
Lucene-style query through as written, for what `ids` can't select:
`metadata.owner:ml*`, `after:2025-10`, `assetType:Model`, `registeredBy:"name"`,
`has:vc`. Unknown fields are refused, since the explorer shows an empty canvas
for them rather than an error.

How it works: `serve` starts a server on 127.0.0.1 with a random port and
token. The page it serves loads the published explorer (`<eqty-explorer>`, from
jsdelivr, pinned by version and hash) and hands it the manifest in the page. On
the first `trust` it frames the chain-of-trust app, a copy pinned in `trust/`
and served on localhost:<port> (its own origin, so it can't reach the page or
the token), and hands it the manifest by postMessage. Both run under a CSP that
lets them connect only to where their own code comes from, so the manifest
never leaves the machine. The other commands write a file to the server's
inbox (in the temp directory); the server pushes each to the page over
Server-Sent Events and writes the page's reply to its outbox. Files rather than HTTP, because a sandbox such as Codex's
blocks connecting to 127.0.0.1 but allows writing there: only `serve`, which
opens the port, needs to run outside it. Standard library only; the graph comes
from eqty-manifest's parse_manifest.py, this skill's sibling.
"""
import argparse
import hmac
import http.server
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
# resolve() follows a symlinked install back to the checkout, where eqty-manifest sits beside us.
sys.path.insert(0, str(HERE.parent / "eqty-manifest"))
import parse_manifest as P  # noqa: E402

# The published explorer. A new version means a new hash:
#   curl -s <EXPLORER_JS> | openssl dgst -sha384 -binary | openssl base64 -A
EXPLORER_VERSION = "0.19.0"
EXPLORER_BASE = f"https://cdn.jsdelivr.net/npm/@eqtylab/explorer@{EXPLORER_VERSION}/dist/standalone/"
EXPLORER_JS = EXPLORER_BASE + "explorer.js"
EXPLORER_SRI = "sha384-wePxnL/hqJZdlNQPrjNNXr13ODwp5VDoV/nyGh+FHqD022h/oVfUXlvmZvz8mSdP"
# JSON-LD contexts the explorer fetches, pinned (contexts/SOURCE.md). sw.js answers these URLs
# from the copies; the page's CSP allows exactly these URLs and nothing else on their host.
PINNED_CONTEXTS = {"https://ns.eqtylab.io/vc/identity-attestation/v1": HERE / "contexts" / "identity-attestation-v1.json"}
# The chain-of-trust app, pinned: see trust/SOURCE.md.
TRUST_DIR = HERE / "trust"
TRUST_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css",
               ".wasm": "application/wasm"}
# ponytail: one session per user at a time; a second `serve` replaces the first.
# Commands travel as files, not HTTP: a sandbox (Codex) that forbids connecting to
# 127.0.0.1 still lets a command write here, so only `serve` needs approval.
# Per user, and checked to be ours and private before use: on Linux the temp directory is
# shared, and a directory another user made first would let them read or forge commands.
DIR = Path(tempfile.gettempdir()) / f"eqty-explorer-{os.getuid()}"
STATE = DIR / "state.json"

ID_RE = re.compile(r"^(urn:cid:)?[a-z0-9]{8,}(/(urn:cid:)?[a-z0-9]{8,})*$")
DID_RE = re.compile(r"^did:key:z[1-9A-HJ-NP-Za-km-z]{20,}$")
NOTE_MAX = 300
TIMEOUT = {"trust": 45}  # the chain-of-trust app builds its graph after it says loaded
# The explorer's query fields and aliases (its query/fields.ts), lower-cased, plus `metadata.<key>`.
QUERY_FIELDS = {"name", "assettype", "type", "id", "cid", "is", "registeredby", "by", "operatedby",
                "executedon", "statement", "has", "associationtype", "assoc", "association",
                "timestamp", "after", "before", "upstream", "downstream", "lineage", "metadata", "meta"}


# ---------------------------------------------------------------- the graph


def graph(m):
    """The nodes and edges the explorer draws, built the way its generateGraph builds them:
    a data node per DataRegistration `data` and per computation input/output, a computation
    node per ComputationRegistration (keyed by statement id), edges input -> step -> output."""
    nodes, edges = {}, set()

    def add(nid, kind, signer=None):
        node = nodes.setdefault(nid, {"id": nid, "kind": kind, "signers": set()})
        if signer:
            node["signers"].add(signer)

    for s in m.by_type.get("DataRegistration", {}).values():
        add(s["data"], "data", s.get("registeredBy"))
    for sid, s in m.by_type.get("ComputationRegistration", {}).items():
        add(sid, "computation", s.get("registeredBy"))
        nodes[sid]["executedOn"] = s.get("executedOn")
        for i in P.as_list(s.get("input")):
            add(i, "data")
            edges.add((i, sid))
        for o in P.as_list(s.get("output")):
            add(o, "data")
            edges.add((sid, o))
    for nid, node in nodes.items():
        meta = m.metadata_for(nid)
        node["name"] = meta.get("name") if isinstance(meta, dict) else None
        node["type"] = (meta.get("assetType") if isinstance(meta, dict) else None) or node["kind"].title()
        node["missing"] = node["kind"] == "data" and P.strip_urn(nid) not in m.blobs
    return nodes, edges


def _reach(start, edges, forward):
    step = {}
    for a, b in edges:
        src, dst = (a, b) if forward else (b, a)
        step.setdefault(src, []).append(dst)
    seen, todo = {start}, [start]
    while todo:
        for nxt in step.get(todo.pop(), []):
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    return seen


def resolve(requested, nodes):
    """A node id from a full or bare address, or None."""
    if requested in nodes:
        return requested
    bare = P.strip_urn(requested)
    return next((n for n in nodes if P.strip_urn(n) == bare), None)


def quoted(value):
    """A query string literal. Ids come from the manifest, so a quote in one must not end it."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def id_query(keep, all_ids):
    """The explorer query that shows exactly `keep`: the shorter of an OR of their ids and a
    NOT of the rest. Everything is the empty query. `keep` must not be empty."""
    rest = [n for n in all_ids if n not in keep]
    if not rest:
        return ""
    shown = " OR ".join(f"id:{quoted(n)}" for n in all_ids if n in keep)
    hidden = " ".join(f"-id:{quoted(n)}" for n in rest)
    return min(shown, hidden, key=len)


def query_fields(text):
    """The field names a query uses, ignoring quoted values and regexes."""
    bare = re.sub(r'"(?:\\.|[^"\\])*"|/(?:\\.|[^/\\])*/', '""', text)
    return re.findall(r"(?<![\w.:])([A-Za-z][\w.]*)\s*:", bare)


def unknown_fields(text):
    return [f for f in query_fields(text)
            if f.lower() not in QUERY_FIELDS and f.lower().split(".")[0] not in ("metadata", "meta")]


def select(nodes, edges, args):
    """The ids an `ids` selector names, in graph order."""
    need = lambda x: resolve(x, nodes) or sys.exit(f"no node {x} in this manifest")  # noqa: E731
    if args.upstream:
        picked = _reach(need(args.upstream), edges, forward=False)
    elif args.downstream:
        picked = _reach(need(args.downstream), edges, forward=True)
    elif args.lineage:
        start = need(args.lineage)
        picked = _reach(start, edges, False) | _reach(start, edges, True)
    elif args.between:
        a, b = need(args.between[0]), need(args.between[1])
        picked = _reach(a, edges, True) & _reach(b, edges, False)
    elif args.on:
        picked = {n for n, d in nodes.items() if d.get("executedOn") == args.on}
    elif args.signer:
        picked = {n for n, d in nodes.items() if args.signer in d["signers"]}
    elif args.computations:
        picked = {n for n, d in nodes.items() if d["kind"] == "computation"}
    else:  # --missing
        picked = {n for n, d in nodes.items() if d["missing"]}
    if args.with_io:
        for a, b in edges:
            if a in picked and nodes[a]["kind"] == "computation":
                picked.add(b)
            if b in picked and nodes[b]["kind"] == "computation":
                picked.add(a)
    return [n for n in nodes if n in picked]


# ---------------------------------------------------------------- the server


class Hub:
    """Commands out to the page, replies back, plus what the page last reported."""

    def __init__(self, all_ids=()):
        self.cond = threading.Condition()
        self.commands = []  # pushed to every connected page, in order
        self.replies = {}
        self.all_ids = list(all_ids)
        self.query = ""  # the query on the canvas, whoever set it
        self.built = ("", None)  # the last query built from an id set, and that set
        self.selection = None
        self.last_seen = 0.0

    def visible(self):
        """The ids on the canvas, or None while a field query (`query`, or the user's) is applied."""
        if not self.query.strip():
            return self.all_ids
        query, ids = self.built
        return ids if query == self.query else None

    def send(self, command, timeout):
        with self.cond:
            message = command.get("message") or {}
            if "visible" in message:  # ours to remember; the page needs only the query
                self.built = (message["query"], message.pop("visible"))
            request_id = len(self.commands) + 1
            self.commands.append({**command, "requestId": request_id})
            self.cond.notify_all()
            if not self.cond.wait_for(lambda: request_id in self.replies, timeout):
                return {"type": "error", "message": "no reply from the page. Is it open? (explorer.py status)"}
            return self.replies.pop(request_id)

    def receive(self, message):
        with self.cond:
            self.last_seen = time.time()
            kind = message.get("type")
            if kind == "explorer/loaded":  # a fresh page: no query, nothing selected
                self.query, self.selection = "", None
            elif kind == "explorer/selected":
                self.selection = message.get("id")
            elif kind in ("explorer/query-changed", "explorer/applied") and isinstance(message.get("query"), str):
                self.query = message["query"]
            if message.get("requestId") is not None:
                self.replies[message["requestId"]] = message
            write_json(DIR / "view.json", self.view())
            self.cond.notify_all()

    def view(self):
        return {"query": self.query, "visible": self.visible(), "total": len(self.all_ids),
                "selection": self.selection, "lastSeen": self.last_seen}


def write_json(path, obj):
    """Write whole or not at all, so a reader never sees half a file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj))
    os.replace(tmp, path)


def watch_inbox(hub):
    """Answer each command file in inbox/ with a reply file of the same name in outbox/."""
    inbox, outbox = DIR / "inbox", DIR / "outbox"

    def answer(name, command):
        if command.get("kind") == "stop":
            write_json(outbox / name, {"type": "stopped"})
            os._exit(0)
        write_json(outbox / name, hub.send(command, TIMEOUT.get(command.get("kind"), 10)))

    while True:
        for path in sorted(inbox.glob("*.json")):
            try:
                command = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            path.unlink(missing_ok=True)
            threading.Thread(target=answer, args=(path.name, command), daemon=True).start()
        time.sleep(0.05)


def make_handler(hub, token, manifest_bytes, page, csp, worker=b""):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):  # quiet: the agent reads replies, not access logs
            pass

        def _allowed(self):
            port = self.server.server_address[1]
            # A DNS-rebinding page arrives with its own Host; only ours is served. The page and
            # its API live on 127.0.0.1; localhost is the chain-of-trust app's origin.
            if self.headers.get("Host") != f"127.0.0.1:{port}":
                return False
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            given = self.headers.get("X-Eqty-Token") or (query.get("t") or [""])[0]
            return hmac.compare_digest(given, token)

        def _send(self, code, body, ctype="application/json", extra=None):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")  # the token is in the page URL
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length) or b"{}")

        def _trust(self):
            """The pinned chain-of-trust app: public code, so no token, but nothing else either."""
            port = self.server.server_address[1]
            name = urllib.parse.urlsplit(self.path).path.lstrip("/") or "index.html"
            if not re.fullmatch(r"index\.html|assets/[\w.-]+", name) or not (TRUST_DIR / name).is_file():
                return self._send(404, {"error": "not found"})
            csp = (f"default-src 'none'; script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'; "
                   f"style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; "
                   f"connect-src 'self'; frame-ancestors http://127.0.0.1:{port}; base-uri 'none'; form-action 'none'")
            self._send(200, (TRUST_DIR / name).read_bytes(), TRUST_TYPES.get(Path(name).suffix, "application/octet-stream"),
                       {"Content-Security-Policy": csp})

        def do_GET(self):
            if self.headers.get("Host") == f"localhost:{self.server.server_address[1]}":
                return self._trust()
            path = urllib.parse.urlsplit(self.path).path
            # The service worker: public code and pinned public documents, so no token; it
            # can only be registered from this origin's own page.
            if path == "/sw.js" and self.headers.get("Host") == f"127.0.0.1:{self.server.server_address[1]}":
                return self._send(200, worker, "text/javascript", {"Content-Security-Policy": "default-src 'none'"})
            if not self._allowed():
                return self._send(403, {"error": "forbidden"})
            if path == "/":
                return self._send(200, page, "text/html; charset=utf-8", {"Content-Security-Policy": csp})
            if path == "/manifest":
                return self._send(200, manifest_bytes)
            if path == "/events":
                return self._events()
            self._send(404, {"error": "not found"})

        def _events(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with hub.cond:
                cursor = len(hub.commands)  # only commands sent after the page connected
            try:
                while True:
                    with hub.cond:
                        hub.cond.wait_for(lambda: len(hub.commands) > cursor, timeout=15)
                        batch = hub.commands[cursor:]
                        cursor = len(hub.commands)
                    chunk = "".join(f"data: {json.dumps(c)}\n\n" for c in batch) or ": keepalive\n\n"
                    self.wfile.write(chunk.encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_POST(self):
            if not self._allowed():
                return self._send(403, {"error": "forbidden"})
            path = urllib.parse.urlsplit(self.path).path
            if path == "/reply":
                hub.receive(self._body())
                return self._send(200, {"ok": True})
            self._send(404, {"error": "not found"})

    return Handler


def run_server(manifest_path):
    private_dir()
    token = secrets.token_urlsafe(24)
    manifest_bytes = Path(manifest_path).read_bytes()
    nodes, _ = graph(P.Manifest(P.load(manifest_path)))
    hub = Hub(nodes)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
    server.daemon_threads = True
    port = server.server_address[1]
    trust_origin = f"http://localhost:{port}"
    config = {"trust": trust_origin + "/", "token": token, "explorerSrc": EXPLORER_JS,
              "explorerSri": EXPLORER_SRI, "explorerBase": EXPLORER_BASE}
    # `</` can't close the script it sits in. The nonce lets only that script run inline: a
    # `javascript:` link or an inline handler from manifest content would not.
    nonce = secrets.token_urlsafe(16)
    page = (HERE / "host.html").read_text().replace(
        "__CONFIG__", json.dumps(config).replace("</", "<\\/")).replace("__NONCE__", nonce).encode()
    contexts = {url: json.loads(path.read_text()) for url, path in PINNED_CONTEXTS.items()}
    worker = (HERE / "sw.js").read_text().replace("__CONTEXTS__", json.dumps(contexts)).encode()
    # Scripts only from the pinned explorer directory (its chunks and wasm load from there too);
    # the page frames only the chain-of-trust app and talks only to this server, that directory
    # and the pinned context URLs, which the service worker answers locally.
    csp = (f"default-src 'none'; script-src 'nonce-{nonce}' 'wasm-unsafe-eval' {EXPLORER_BASE}; "
           f"style-src 'unsafe-inline'; img-src data: blob:; worker-src 'self'; "
           f"connect-src 'self' {EXPLORER_BASE} {' '.join(PINNED_CONTEXTS)}; frame-src {trust_origin}; "
           f"frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
    server.RequestHandlerClass = make_handler(hub, token, manifest_bytes, page, csp, worker)
    for box in ("inbox", "outbox"):
        shutil.rmtree(DIR / box, ignore_errors=True)  # nothing left over from an earlier session
        (DIR / box).mkdir(parents=True, mode=0o700)
    write_json(DIR / "view.json", hub.view())
    threading.Thread(target=watch_inbox, args=(hub,), daemon=True).start()
    fd = os.open(STATE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # holds the token
    with os.fdopen(fd, "w") as f:
        json.dump({"pid": os.getpid(), "port": port, "token": token,
                   "manifest": str(Path(manifest_path).resolve()),
                   "url": f"http://127.0.0.1:{port}/?t={token}"}, f)
    server.serve_forever()


# ---------------------------------------------------------------- the client


def private_dir():
    """Make DIR if needed, and refuse it unless it is a real directory, ours, and private."""
    DIR.mkdir(mode=0o700, exist_ok=True)
    st = os.lstat(DIR)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        sys.exit(f"{DIR} is not a private directory of yours; remove it and try again")


def is_our_server(pid):
    """Whether pid is still an explorer.py server, so a reused PID is never signalled."""
    try:
        out = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True,
                             timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "explorer.py" in out and " _run " in f"{out} "


def state():
    private_dir()
    try:
        s = json.loads(STATE.read_text())
        os.kill(s["pid"], 0)
        return s
    except PermissionError:  # alive, but a sandbox (Codex) won't let us signal it
        return s
    except (FileNotFoundError, ProcessLookupError, json.JSONDecodeError, KeyError):
        return None


NOT_RUNNING = "no explorer running. Start one with: explorer.py serve <manifest>"


def command(kind, target, message, need_server=True):
    if need_server and not state():
        sys.exit(NOT_RUNNING)
    if not (DIR / "inbox").is_dir():
        return {"type": "error", "message": "the explorer server has no inbox; run explorer.py serve again"}
    name = f"{secrets.token_hex(8)}.json"
    write_json(DIR / "inbox" / name, {"kind": kind, "target": target, "message": message})
    reply_path = DIR / "outbox" / name
    deadline = time.time() + TIMEOUT.get(kind, 10) + 5
    while time.time() < deadline:
        if reply_path.exists():
            reply = json.loads(reply_path.read_text())
            reply_path.unlink(missing_ok=True)
            return reply
        time.sleep(0.05)
    (DIR / "inbox" / name).unlink(missing_ok=True)
    return {"type": "error", "message": "the explorer server did not answer (explorer.py status)"}


def stop_server():
    """Ask the running server to exit; signal it if it can't be asked (an older or wedged one)."""
    s = state()
    if s and command("stop", "server", {}).get("type") != "stopped" and is_our_server(s["pid"]):
        try:
            os.kill(s["pid"], 15)
        except (ProcessLookupError, PermissionError):
            pass
    STATE.unlink(missing_ok=True)


def view():
    state() or sys.exit(NOT_RUNNING)
    try:
        return json.loads((DIR / "view.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"query": "", "visible": None, "total": 0, "selection": None, "lastSeen": 0}


def check_ids(ids):
    ids = [line for x in ids for line in (sys.stdin.read().split() if x == "-" else [x])]
    bad = [x for x in ids if not ID_RE.match(x)]
    if bad:
        sys.exit(f"not node ids: {' '.join(bad)}")
    return ids


def served_manifest():
    s = state() or sys.exit("no explorer running. Start one with: explorer.py serve <manifest>")
    return P.Manifest(P.load(s["manifest"]))


def print_reply(reply):
    print(json.dumps(reply, indent=2))
    if reply.get("type") in ("error", "explorer/error"):
        sys.exit(1)


def cmd_serve(args):
    if not Path(args.manifest).is_file():
        sys.exit(f"no such file: {args.manifest}")
    stop_server()  # checks DIR through state()
    log = open(DIR / "server.log", "w")
    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "_run", args.manifest],
                     stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    for _ in range(100):
        s = state()
        if s:
            break
        time.sleep(0.05)
    else:
        log_text = (DIR / "server.log").read_text()
        if "PermissionError" in log_text and "bind" in log_text:
            sys.exit("the sandbox blocked opening a local port. Run `explorer.py serve` with the user's "
                     "approval, outside the sandbox; the other commands work inside it.")
        sys.exit(f"the server did not start; see {DIR / 'server.log'}")
    print(s["url"])
    if not args.no_open:
        webbrowser.open(s["url"])


FIELD_QUERY = "a field query is on the canvas ({}), so which nodes it shows is not known; `only` or `show` replaces it"


def cmd_status(_):
    s = state()
    if not s:
        print("not running")
        return
    st = view()
    ago = time.time() - st["lastSeen"] if st["lastSeen"] else None
    print(json.dumps({"url": s["url"], "manifest": s["manifest"], "query": st["query"],
                      "visible": f"{len(st['visible'])} of {st['total']}" if st["visible"] is not None
                      else "unknown: a field query (the user's, or `query`)", "selection": st["selection"],
                      "pageLastHeard": f"{ago:.0f}s ago" if ago is not None else "never"}, indent=2))


def cmd_stop(_):
    stop_server()
    print("stopped")


def cmd_nodes(args):
    nodes, _ = graph(served_manifest())
    st = view()
    visible = set(st["visible"]) if st["visible"] is not None else None
    rows = [{"id": n, "type": d["type"], "name": d["name"],
             "hidden": None if visible is None else n not in visible,
             **({"missing": True} if d["missing"] else {})} for n, d in nodes.items()]
    if args.json:
        print(json.dumps(rows, indent=2))
        return
    if visible is None:
        print(f"# a field query is applied ({st['query']}); which nodes it shows is not known")
    for r in rows:
        flags = " [hidden]" * bool(r["hidden"]) + " [bytes missing]" * bool(r.get("missing"))
        print(f"{r['id']}\t{r['type']}\t{r['name'] or '-'}{flags}")


def cmd_ids(args):
    m = P.Manifest(P.load(args.manifest)) if args.manifest else served_manifest()
    nodes, edges = graph(m)
    for node_id in select(nodes, edges, args):
        print(node_id)


def with_note(reply, note):
    if note is not None and reply.get("type") not in ("error", "explorer/error"):
        command("note", "page", {"type": "page/note", "text": note[:NOTE_MAX]})
    print_reply(reply)


def show_set(nodes, keep, unknown=(), note=None, narrowing=False):
    """Put exactly `keep` on the canvas, as one id query. `narrowing`: the caller meant to cut
    the graph down, so say when the set is every node and nothing was cut."""
    if not keep:
        sys.exit("that would leave the canvas empty")
    all_ids = list(nodes)
    visible = [n for n in all_ids if n in keep]
    reply = command("apply", "explorer", {"type": "explorer/apply", "query": id_query(keep, all_ids),
                                          "visible": visible})
    if reply.get("type") == "explorer/applied":
        reply.update(visible=len(visible), hidden=len(all_ids) - len(visible), unknown=list(unknown))
        if reply.get("shown") not in (None, len(visible)):
            reply["warning"] = f"the explorer shows {reply['shown']} nodes, not {len(visible)}"
        elif narrowing and len(visible) == len(all_ids):
            reply["warning"] = "every node is in this set, so nothing was filtered; don't describe the view as narrowed"
    with_note(reply, note)


def found(ids, nodes):
    """The node ids these name, and the ones that name nothing in this manifest."""
    ids = check_ids(ids)
    return {resolve(x, nodes) for x in ids} - {None}, [x for x in ids if not resolve(x, nodes)]


def cmd_only(args):
    nodes, _ = graph(served_manifest())
    keep, unknown = found(args.ids, nodes)
    if not keep:
        sys.exit("none of those ids are in this manifest")
    show_set(nodes, keep, unknown, args.note, narrowing=True)


cmd_highlight = cmd_only  # ponytail: the explorer can't dim yet; `highlight` shows only these


def current(st):
    return set(st["visible"]) if st["visible"] is not None else sys.exit(FIELD_QUERY.format(st["query"]))


def cmd_hide(args):
    nodes, _ = graph(served_manifest())
    drop, unknown = found(args.ids, nodes)
    show_set(nodes, current(view()) - drop, unknown)


def cmd_show(args):
    nodes, _ = graph(served_manifest())
    if not args.ids:
        return show_set(nodes, set(nodes))
    add, unknown = found(args.ids, nodes)
    show_set(nodes, current(view()) | add, unknown)


def cmd_query(args):
    text = " ".join(args.text)
    bad = unknown_fields(text)
    if bad:
        sys.exit(f"unknown query field{'s' * (len(bad) > 1)}: {', '.join(bad)}. Fields: "
                 f"{', '.join(sorted(QUERY_FIELDS))}, metadata.<key>. Quote values that contain ':'.")
    reply = command("apply", "explorer", {"type": "explorer/apply", "query": text})
    if reply.get("shown") == 0:
        reply["warning"] = "no nodes match: the canvas is empty. Check the values, or `reset`."
    with_note(reply, args.note)


def cmd_select(args):
    nodes, _ = graph(served_manifest())
    node = resolve(check_ids([args.id])[0], nodes) or sys.exit(f"no node {args.id} in this manifest")
    message = {"type": "explorer/apply", "select": node}
    visible = view()["visible"]
    if visible is None or node not in visible:  # the explorer ignores a node its query filtered out
        message.update(query="", visible=list(nodes))
    print_reply(command("select", "explorer", message))


def cmd_reset(_):
    nodes, _ = graph(served_manifest())
    command("note", "page", {"type": "page/note", "text": ""})
    print_reply(command("reset", "explorer", {"type": "explorer/apply", "query": "", "visible": list(nodes),
                                              "select": None}))


def cmd_selection(_):
    st = view()
    selected = st["selection"]
    users_query = {"query": st["query"]} if st["visible"] is None else {}  # the user may have typed it
    if not selected:
        print(json.dumps(users_query, indent=2) if users_query else "nothing selected")
        return
    m = served_manifest()
    nodes, _ = graph(m)
    node = nodes.get(selected, {})
    print(json.dumps({"id": selected, "type": node.get("type"), "name": node.get("name"),
                      "metadata": m.metadata_for(selected), **users_query}, indent=2, default=str))


def cmd_trust(args):
    if not DID_RE.match(args.did):
        sys.exit(f"not a did:key: {args.did}")
    print_reply(command("trust", "trust", {"type": "vcomp/focus-did", "did": args.did}))


def cmd_note(args):
    print_reply(command("note", "page", {"type": "page/note", "text": " ".join(args.text)[:NOTE_MAX]}))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["_run"]:
        return run_server(argv[1])
    if argv[:1] == ["query"]:  # by hand: argparse would read `-has:vc` as an option
        words, note = argv[1:], None
        if "--note" in words:
            at = words.index("--note")
            words, note = words[:at] + words[at + 2:], " ".join(words[at + 1:at + 2])
        if not words:
            sys.exit("usage: explorer.py query TEXT [--note TEXT]")
        return cmd_query(argparse.Namespace(text=words, note=note))
    ap = argparse.ArgumentParser(prog="explorer.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("serve")
    p.add_argument("manifest")
    p.add_argument("--no-open", action="store_true")
    sub.add_parser("status")
    sub.add_parser("stop")
    sub.add_parser("nodes").add_argument("--json", action="store_true")
    p = sub.add_parser("ids")
    g = p.add_mutually_exclusive_group(required=True)
    for flag in ("--upstream", "--downstream", "--lineage", "--on", "--signer"):
        g.add_argument(flag, metavar="ID" if flag not in ("--on", "--signer") else "DID")
    g.add_argument("--between", nargs=2, metavar=("A", "B"))
    g.add_argument("--computations", action="store_true")
    g.add_argument("--missing", action="store_true")
    p.add_argument("--with-io", action="store_true")
    p.add_argument("--manifest", help="read this file instead of the served one")
    for name in ("highlight", "only"):
        p = sub.add_parser(name)
        p.add_argument("ids", nargs="+")
        p.add_argument("--note")
    sub.add_parser("hide").add_argument("ids", nargs="+")
    sub.add_parser("show").add_argument("ids", nargs="*")
    sub.add_parser("query", help="TEXT [--note TEXT]; parsed by hand above")
    sub.add_parser("select").add_argument("id")
    sub.add_parser("reset")
    sub.add_parser("selection")
    sub.add_parser("trust").add_argument("did")
    sub.add_parser("note").add_argument("text", nargs="*")
    args = ap.parse_args(argv)
    globals()["cmd_" + args.cmd](args)


if __name__ == "__main__":
    main()
