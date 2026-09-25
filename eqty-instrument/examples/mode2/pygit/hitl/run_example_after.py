"""Scaffolding for the pygit HITL run. Not part of pygit, and carries no nodes.

Runs L1's command sequence through pygit's own command line:
    init myrepo -> (cd myrepo) add FILE... -> commit -m MSG -> push GIT_URL

The remote is stubbed: instead of GitHub, a local HTTP server on 127.0.0.1
hands each request to the real `git http-backend` CGI over a bare repo in
./example/remote.git. The push leg initialises the SDK in pygit's own
`__main__` (SDK directory: example/myrepo/.git/eqty_sdk) and exports its
manifest there; this script then moves that one file to ../pygit.hitl.manifest.json.
"""

import http.server, os, shutil, subprocess, sys, threading

HERE = os.path.dirname(os.path.abspath(__file__))
PYGIT = os.path.join(HERE, 'pygit.py')
WORK = os.path.join(HERE, 'example')
REMOTE = os.path.join(WORK, 'remote.git')
REPO = os.path.join(WORK, 'myrepo')
MANIFEST_OUT = os.path.join(os.path.dirname(HERE), 'pygit.hitl.manifest.json')
GIT_BACKEND = os.path.join(subprocess.check_output(
        ['git', '--exec-path'], text=True).strip(), 'git-http-backend')


class GitBackend(http.server.BaseHTTPRequestHandler):
    """Local stand-in for the GitHub endpoint: a CGI bridge to git http-backend."""

    def _cgi(self, method):
        path, _, query = self.path.partition('?')
        body = self.rfile.read(int(self.headers.get('Content-Length') or 0))
        service = path.rsplit('/', 1)[-1]
        env = {
            'PATH': os.environ['PATH'],
            'GIT_PROJECT_ROOT': WORK,
            'GIT_HTTP_EXPORT_ALL': '1',
            'REQUEST_METHOD': method,
            'PATH_INFO': path,
            'QUERY_STRING': query,
            'CONTENT_LENGTH': str(len(body)),
            # pygit sends urllib's default form content type; http-backend
            # insists on the git one. The stand-in supplies it.
            'CONTENT_TYPE': 'application/x-{}-request'.format(service)
                            if method == 'POST' else '',
            'REMOTE_USER': self.headers.get('Authorization') and 'pygit' or '',
            'REMOTE_ADDR': '127.0.0.1',
        }
        out = subprocess.run([GIT_BACKEND], input=body, env=env,
                             capture_output=True, check=True).stdout
        head, _, payload = out.partition(b'\r\n\r\n')
        status, headers = 200, []
        for line in head.decode().split('\r\n'):
            key, _, value = line.partition(': ')
            if key.lower() == 'status':
                status = int(value.split()[0])
            else:
                headers.append((key, value))
        self.send_response(status)
        for key, value in headers:
            self.send_header(key, value)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self._cgi('GET')

    def do_POST(self):
        self._cgi('POST')

    def log_message(self, fmt, *args):
        print('  remote:', fmt % args)


def pygit(*args, cwd):
    print('$ pygit.py', ' '.join(args))
    subprocess.run([sys.executable, PYGIT, *args], cwd=cwd, env=ENV, check=True)


if __name__ == '__main__':
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK)
    subprocess.run(['git', 'init', '-q', '--bare', '-b', 'master', REMOTE], check=True)
    subprocess.run(['git', '-C', REMOTE, 'config', 'http.receivepack', 'true'], check=True)

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), GitBackend)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    git_url = 'http://127.0.0.1:{}/remote.git'.format(server.server_port)

    ENV = dict(os.environ, GIT_AUTHOR_NAME='Example Author',
               GIT_AUTHOR_EMAIL='author@example.com',
               GIT_USERNAME='example-user', GIT_PASSWORD='example-password')

    pygit('init', 'myrepo', cwd=WORK)
    with open(os.path.join(REPO, 'hello.txt'), 'w') as f:
        f.write('hello, pygit\n')
    with open(os.path.join(REPO, 'notes.md'), 'w') as f:
        f.write('# notes\n\npushed by pygit to a local git http-backend\n')
    pygit('add', 'hello.txt', 'notes.md', cwd=REPO)
    pygit('commit', '-m', 'first commit from pygit', cwd=REPO)
    pygit('push', git_url, cwd=REPO)
    server.shutdown()

    # The remote is real git: confirm it accepted what pygit pushed.
    subprocess.run(['git', '-C', REMOTE, 'fsck', '--strict'], check=True)
    subprocess.run(['git', '-C', REMOTE, 'log', '--stat', 'master'], check=True)

    shutil.move(os.path.join(REPO, '.git', 'eqty_sdk', 'manifest.json'), MANIFEST_OUT)
    print('manifest:', MANIFEST_OUT)
