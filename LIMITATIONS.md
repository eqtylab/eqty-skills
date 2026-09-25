# Known limitations

What each skill cannot do today, so a result is never read as more than it is.

## eqty-instrument, Mode 1

Limits of the framework handlers themselves, which the skill cannot work around.
Items 1 and 3–6 are documented by `eqty-lineage`, whose docs are vendored in
`eqty-instrument/references/upstream/`; item 2 is how its packages are
distributed today.

1. **Your agent needs an in-process call site.** A callback handler attaches to
   a Python call such as `.invoke()`. An agent that only runs in a
   `langgraph dev` subprocess, on LangSmith's platform from an `agent.json`, or
   inside a container has no such call site, so there is nothing to attach the
   handler to; recording it would need a hook the runtime exposes. `detect.py`
   refuses such a repo with a non-zero exit rather than producing an empty plan.
   (`eqty-instrument/references/upstream/eqty-lineage-deepagents.EXAMPLES.md:205`)
2. **The handler packages come from EQTY's private index.** `eqty-sdk` is on
   public PyPI, but `eqty-lineage-langchain` and `eqty-lineage-deepagents`
   resolve only through `pypi.eqtylab.io`. Your CI and teammates need access to
   it.
3. **Compaction is recorded as an ordinary step.** On long DeepAgents runs,
   `SummarizationMiddleware` compacts the conversation. The manifest shows that a
   compaction happened, on which turn, and what survived it — but it is recorded
   as an ordinary derivation, so its edges cannot say that most of the input was
   discarded. Anyone auditing the manifest should know this.
   (`eqty-instrument/references/upstream/eqty-lineage-deepagents.README.md:223`)
4. **Shell commands leave gaps.** Under a sandbox or local-shell backend, whatever
   an `execute` call did to files cannot be recovered from the command, so it is
   not recorded. Rather than assert files it may have changed, later edits and
   reads stop linking to earlier versions.
   (`eqty-instrument/references/upstream/eqty-lineage-deepagents.README.md:158`)
5. **Files the agent only reads are recorded as renderings, not as the file.**
   Under a filesystem, store or sandbox backend, a file that already existed and
   is only read is recorded as what `read_file` returned — line-numbered, chunked
   at long lines and truncated when large — labelled as a rendering, not under
   the file's own content hash. (`eqty-instrument/references/upstream/eqty-lineage-deepagents.README.md:141`)
6. **Tool source capture needs real files.** `@eqty_tool` reads a tool's source
   with `inspect.getsource`, so tools defined in a REPL or in `exec`'d code fall
   back to a name-and-description stub.
   (`eqty-instrument/references/upstream/eqty-lineage-langchain.README.md:111`)

## eqty-instrument, Mode 2

- **Step 1 needs the `claude` CLI.** Mode 2 draws its control-flow graph with
  fresh, isolated `claude -p` processes (`isolated_cfg.py`) and stops if the CLI
  is not on PATH, rather than fall back to an in-session agent. Under Codex,
  Mode 2 therefore needs Claude Code installed too; a `codex exec` route is in
  progress.
- **Each run starts two agent sessions on your account.** They count against
  your usage.
- A fuller Mode 2 list is still being written.

## eqty-manifest

Every check the skill cannot run is reported as *not checked* — never as a pass.

- **Hardware key binding is not checked.** Whether a hardware report's signed
  key material derives to the DID an attestation claims is printed as
  *not checked*: `eqty-sdk` exposes no primitive for it.
- **Measurements are not compared with reference values.** Verified hardware
  evidence means the report came from genuine Intel, AMD or NVIDIA silicon and is
  intact — not that the enclave ran the code you expect. Reference measurements
  (Intel TCB info, NVIDIA RIM, expected firmware and kernel values, TPM PCR
  values) are not bundled, so every result carries `measurements_checked: false`.
- **A TPM attestation key's certificate is not checked.** No TPM root is pinned;
  a TPM quote verifies only through its binding to a verified AMD SEV-SNP or
  Intel TDX report in the same manifest, and stays *not checked* without one.
- **Sigstore bundles are not checked against the transparency log.** A valid
  signature means the payload was signed by that DID, not that it was publicly
  logged (`transparencyLogChecked: false`).
- **"Not checked" versus "failed" is inferred for signatures.** `eqty-sdk`'s
  `verify_vc` returns only true or false, so when it says false the skill reads
  the credential's fields to tell an unsupported proof type or context from a bad
  signature. The supported lists are the ones observed verifying; this can only
  turn a *failed* into *not checked*, never make anything verified.
- **The first run needs network.** `uv run` fetches `eqty-sdk`, `cryptography`
  and `base58` once; in a sandboxed agent such as Codex you may need to approve
  that command. After that it works offline.
