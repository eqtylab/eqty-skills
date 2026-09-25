"""Local stand-in for a GitHub smart-HTTP remote. Scaffolding, not the target.

Stubs the network boundary only: the pkt-line wire format pygit speaks
(`GET /info/refs?service=git-receive-pack`, `POST /git-receive-pack`) behind
HTTP Basic auth, for a fresh, empty repository. It checks the pack it
receives (signature, version, object count, SHA-1 trailer) and replies
`unpack ok` / `ok refs/heads/master` only when the pack is well formed. It
does not store objects or move a ref. Carries no EQTY instrumentation.
"""

import base64, hashlib, http.server, struct, threading

USERNAME, PASSWORD = 'demo', 'demo-password'


def pkt(line):
    return '{:04x}'.format(len(line) + 4).encode() + line


class Handler(http.server.BaseHTTPRequestHandler):
    received = []  # (object count, pack bytes) for each accepted push

    def log_message(self, *args):
        pass

    def authorised(self):
        expected = 'Basic ' + base64.b64encode(
            '{}:{}'.format(USERNAME, PASSWORD).encode()).decode()
        if self.headers.get('Authorization') == expected:
            return True
        self.send_response(401)
        self.send_header('WWW-Authenticate', 'Basic realm="stub"')
        self.send_header('Content-Length', '0')
        self.end_headers()
        return False

    def reply(self, body):
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.authorised():
            return
        assert self.path.endswith('/info/refs?service=git-receive-pack')
        self.reply(pkt(b'# service=git-receive-pack\n') + b'0000' +
                   pkt(b'0' * 40 + b' capabilities^{}\x00report-status\n') +
                   b'0000')

    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length']))
        if not self.authorised():
            return
        assert self.path.endswith('/git-receive-pack')
        pack = body[body.index(b'0000PACK') + 4:]
        signature, version, count = struct.unpack('!4sLL', pack[:12])
        ok = (signature == b'PACK' and version == 2 and
              hashlib.sha1(pack[:-20]).digest() == pack[-20:])
        if ok:
            Handler.received.append((count, pack))
            status = pkt(b'unpack ok\n') + pkt(b'ok refs/heads/master\n')
        else:
            status = pkt(b'unpack bad pack\n') + pkt(b'ng refs/heads/master\n')
        self.reply(status + b'0000')


def start():
    server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
