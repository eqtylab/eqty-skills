# Changelog

## 0.1.9 — 2026-09-28

- **eqty-manifest** — a request for "a summary" of a manifest now shows the
  verification summary: the agent runs `summary.py`, pastes its block unchanged,
  then says what the run did. Before, "show a summary" could get a prose account
  with no checks run, and a trust answer could retell the block instead of
  pasting it. The paste rule now holds even when the agent's harness already
  shows command output or prefers short answers, and a narrative may call
  something verified only on the block's word. Codex 0.158 and Claude Sonnet: 12
  of 12 summary and check answers pasted the block exactly, up from 0 of 2
  summaries on Codex.
- This release also carries 0.1.8's eqty-instrument fix, which was not published
  on its own.

## 0.1.8 — 2026-09-27

- **eqty-instrument** — the L1 and L2 CFG agents now write every Mermaid node and
  edge label in double quotes, with no backticks, so the diagrams render. The
  label example in the prompt was itself a quote followed by a backtick
  (`"`_cast()` — …"`), which Mermaid reads as an unclosed markdown string; it no
  longer has backticks. On llama, with the purpose-paragraph L1 message, 4/8
  diagrams rendered before and 8/8 after (Mermaid 11.4.1), with boxes, coverage
  and run-to-run agreement unchanged. L2 was not re-tested.

## 0.1.7 — 2026-09-26

- **eqty-manifest** — key binding is now checked on Intel TDX and NVIDIA reports
  too, not only through TPM quotes. When a credential declares `userData: {type:
  "key"}`, the DID's public key must sit in a field the hardware signs: TDX
  `REPORTDATA[32:64]`, the NVIDIA SPDM request nonce. It counts only when the
  report itself verified; a mismatch fails. Evidence without a key claim (older
  `EqtyVComp…V0` reports, Azure runtime-data evidence with no TPM quote) stays
  *not checked*.

## 0.1.6 — 2026-09-25

- **eqty-manifest** — loads more reliably: the description now leads with what
  people ask ("check this manifest", "was it tampered with", "does it verify"),
  says to load the skill as soon as a JSON file turns out to be a manifest, and
  forbids judging tampering from the raw JSON. On Haiku 4.5, "check the manifest
  file … for any tampering" went from reading the raw JSON first (2/2) to loading
  the skill first (4/4).
- **eqty-manifest** — the explanation must quote the summary block through its
  last line, never treat statement order or timestamps as evidence of tampering,
  never swap "missing" and "orphaned", and never attribute intent.
- **eqty-manifest** — statements that all name the same missing system as
  `executedOn` fold into one line (`--full` lists them); absent hardware evidence
  reads *nothing to check* in the problem list too.

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
