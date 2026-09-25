# Changelog

## 0.1.2 — 2026-09-25

- **eqty-manifest** — clearer in a sandbox such as Codex: point `UV_CACHE_DIR` at
  a writable temp directory when `~/.cache/uv` is not writable; ask network
  approval for the first verifying run only; never fall back to `--no-verify`
  unless asked; write the report narrative after the verified run, about what the
  run did, never whether it verified.

## 0.1.1 — 2026-09-25

- **eqty-instrument, Mode 2** — step 1 runs under Codex too: `isolated_cfg.py
  --agent codex` starts the control-flow-graph agents as isolated `codex exec`
  processes, audited like the Claude ones. Claude stays the default when both
  are installed.

## 0.1.0 — 2026-09-25

First release of the two EQTY skills, as a Claude Code and Codex plugin.

- **eqty-manifest** — reads an EQTY lineage manifest: parses it, verifies its
  signatures, content addresses, statement integrity and hardware attestation
  evidence, and writes an HTML report. `summary.py` prints every check as one
  block.
- **eqty-instrument** — makes a repo emit a manifest. Mode 1 for LangChain,
  LangGraph and DeepAgents repos (a fixed edit, with `detect.py` to plan it);
  Mode 2 for arbitrary Python (a control-flow-graph-first procedure, auto or
  human-in-the-loop node selection, with `check_graph.py` for the result).
  Ships real before/after runs in `examples/`.
