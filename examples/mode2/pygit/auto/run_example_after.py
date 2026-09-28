"""Run the README workflow through pygit's own command line and emit a manifest.

Scaffolding, not the target: this file carries no EQTY nodes. It runs the
sequence the CFG diagrammed, each command as its own `python pygit.py ...`
process, exactly as a user would type it:

    init myrepo -> (cd myrepo) add hello.txt notes.txt -> commit -m MSG -> push URL

The remote is `stub_remote.py`, a local smart-HTTP stand-in (no network).
All four processes record into one EQTY context (PYGIT_EQTY_CONTEXT), and each
exports it on exit to PYGIT_EQTY_MANIFEST, so the file left after `push` is
the whole run.

    ./venv/bin/python out/pygit/run_example.py
"""

import os, shutil, subprocess, sys, uuid

import stub_remote

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, '.run')             # the user's directory, scratch
EQTY_DIR = os.path.join(HERE, '.eqty')        # SDK store + signer key
MANIFEST = os.path.join(os.path.dirname(HERE), 'pygit.auto.manifest.json')


def pygit(cwd, *args):
    subprocess.run([sys.executable, os.path.join(HERE, 'pygit.py')] + list(args),
                   cwd=cwd, env=ENV, check=True)


for path in (WORK, EQTY_DIR):
    shutil.rmtree(path, ignore_errors=True)
if os.path.exists(MANIFEST):
    os.remove(MANIFEST)
os.makedirs(WORK)

server = stub_remote.start()
git_url = 'http://127.0.0.1:{}/demo/myrepo.git'.format(server.server_port)

ENV = dict(os.environ,
           PYGIT_EQTY_DIR=EQTY_DIR,
           PYGIT_EQTY_CONTEXT=str(uuid.uuid4()),
           PYGIT_EQTY_MANIFEST=MANIFEST,
           GIT_AUTHOR_NAME='Ada Example', GIT_AUTHOR_EMAIL='ada@example.com',
           GIT_USERNAME=stub_remote.USERNAME, GIT_PASSWORD=stub_remote.PASSWORD)

repo = os.path.join(WORK, 'myrepo')
pygit(WORK, 'init', 'myrepo')
with open(os.path.join(repo, 'hello.txt'), 'w') as f:
    f.write('Hello, world!\n')
with open(os.path.join(repo, 'notes.txt'), 'w') as f:
    f.write('pygit: just enough git to commit and push.\n')
pygit(repo, 'add', 'hello.txt', 'notes.txt')
pygit(repo, 'commit', '-m', 'First commit, made with pygit')
pygit(repo, 'push', git_url)
server.shutdown()

count, pack = stub_remote.Handler.received[-1]
print('remote accepted a pack of {} objects ({} bytes)'.format(count, len(pack)))
print('manifest:', MANIFEST)
