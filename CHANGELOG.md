# Changelog

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
