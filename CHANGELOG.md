# Changelog

## 0.1.5 — 2026-09-25

- **eqty-manifest** — when a hardware attestation's evidence blob is absent,
  unreadable or of an unsupported type, its line now reads *nothing to check —
  evidence blob not in the manifest (urn:cid:…)* instead of *report signature not
  checked · vendor chain not checked*, so it cannot be mistaken for evidence that
  is present but uncheckable. Same wording in the HTML report.

## 0.1.4 — 2026-09-25

- **eqty-manifest** — blobs referenced from an IdentityAttestation's `identity`
  (a confidential VM's runtime data, cloud-init user data, kernel command line,
  container configs, init data) are no longer reported as orphaned; when absent,
  they now count as missing pre-images, which they are.

## 0.1.3 — 2026-09-25

- **eqty-manifest** — key binding is now checked through TPM quotes. When a TPM
  credential declares `userData: {type: "key"}`, the quote's signed extraData must
  be the DID's public key (its P-256 X coordinate). It reads *verified* only when
  the quote verified and is bound to a verified AMD SEV-SNP or Intel TDX report,
  and that report then reads bound to the DID through the quote; a mismatch
  fails. Bare TDX, SEV-SNP and NVIDIA reports stay *not checked*.

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
