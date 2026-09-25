# EQTY Skills

Two agent skills for [EQTY](https://eqtylab.io) lineage manifests — signed,
content-addressed records of what a computation did: its inputs, tool calls,
model calls and outputs, checkable by a third party without trusting anyone's
word for it.

| Skill | Fires when | What it does |
|---|---|---|
| **`eqty-manifest`** | a manifest is in hand | parses it, verifies its signatures, content addresses, statement integrity and hardware attestation evidence, and writes a report |
| **`eqty-instrument`** | you want your code to emit one | makes a repo emit a manifest, delivered as a patch for a human to review |

They are two halves of one loop: `eqty-instrument` produces a manifest and
points at it; `eqty-manifest` reads it back.

## Install

Both skills install together, in Claude Code or Codex.

**Claude Code plugin:**

```
/plugin marketplace add eqtylab/eqty-skills
/plugin install eqty-skills@eqty-lab
```

**Codex plugin:**

```sh
codex plugin marketplace add eqtylab/eqty-skills
codex plugin add eqty-skills@eqty-lab
```

**Manually** — copy both skill directories into a skills folder, side by side
(`eqty-instrument` calls `eqty-manifest` as its sibling):

| Agent | For you, in every project | For one project |
|---|---|---|
| Claude Code | `~/.claude/skills/` | `.claude/skills/` |
| Codex | `~/.agents/skills/` | `.agents/skills/` |

```sh
cp -R eqty-manifest eqty-instrument ~/.claude/skills/    # or ~/.agents/skills/ for Codex
```

Each skill is self-contained: its directory carries everything its `SKILL.md`
tells an agent to read or run, and refers to its own files relative to that
directory.

## Requirements

- **Python 3.10+.**
- **`eqty-manifest`** needs `eqty-sdk`, `cryptography` and `base58` to verify.
  With [uv](https://docs.astral.sh/uv/), `uv run` installs them per script;
  otherwise `pip install -r eqty-manifest/requirements.txt`. Without them the
  scripts still parse and report, and every check they cannot run says
  *not checked* — never a pass.
- **`eqty-instrument`** needs nothing installed to run: its scripts
  (`detect.py`, `check_graph.py`) use the standard library only. What it
  produces is a patch to *your* code, and that patch adds `eqty-sdk` to your
  project's dependencies, because the instrumented code imports it. In Mode 1
  the patch also adds EQTY's LangChain or DeepAgents handler package, installed
  from EQTY's private package index (see [`LIMITATIONS.md`](LIMITATIONS.md)).

Both skills build on the open-source EQTY SDK (`eqty_sdk`):
[eqtylab/integrity-py](https://github.com/eqtylab/integrity-py), installed with
`pip install eqty-sdk`.

## Try it

A real manifest ships with the examples. Verify it:

```sh
uv run eqty-manifest/summary.py \
  eqty-instrument/examples/mode1/deepagent/research_agent/research_agent_manifest.json
```

```
Summary
=======
173 / 173 MetadataRegistration verified
120 / 120 DataRegistration verified
52 / 52 ComputationRegistration verified

Signers:
- did:key:z6MkqWiZdvahw5qQMBvrJfgTh7sLafRq18VpubrhnGnkRsrj signed 366 / 366 valid credentials
- 366 / 366 credentials: issuer is the registration's signer

Hashes:
- Total of 163 hashes
- 163 have pre-images attached
- 163 / 163 verified
- No tampering detected
- No missing pre-image blobs
```

Or ask Claude about it — *"what does this manifest show?"* — and
`eqty-manifest` takes over.

## eqty-instrument, in two modes

- **Mode 1** — repos built on **LangChain, LangGraph or DeepAgents**. The
  framework already fixed the node boundaries, so the edit is the same every
  time: initialise the SDK once, attach one callback handler per run, fingerprint
  the repo's own tools, export. `detect.py` plans it.
- **Mode 2** — **arbitrary Python**. No framework has decided which operations
  deserve nodes, so the skill derives a control-flow graph first, you choose
  **auto** or **human-in-the-loop** node selection, and only then does it touch
  code. `check_graph.py` checks the result.

`eqty-instrument/examples/` holds real runs of both: each changed file as
`*_before.py` and `*_after.py`, with `changes.diff` between them.

## Known limitations

See [`LIMITATIONS.md`](LIMITATIONS.md).

## License

Apache License 2.0 — see [`LICENSE`](LICENSE). Third-party material under
`eqty-instrument/references/upstream/` and `eqty-instrument/examples/` carries
its own license beside it; see [`NOTICE`](NOTICE).
