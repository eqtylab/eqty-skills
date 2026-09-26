---
name: eqty-manifest
description: Use this for any EQTY lineage manifest — a JSON file (often named *manifest*.json) with top-level "version", "contexts", "statements" and "blobs" keys, "urn:cid:" identifiers, did:key DIDs, or types like ComputationRegistration or CredentialRegistration. Trigger on "check this manifest", "was it tampered with", "does it verify", "who signed it", "what hardware did it run on", "what does this manifest show", "what happened in this run", "summarize this trace", or any request to report on, chart or make such a file readable, even if the user does not name the format. If you open a JSON file and find these keys or urn:cid: identifiers, stop and load this skill before going further. Load it BEFORE answering: never judge tampering or verification by reading the raw JSON yourself. Statement order and timestamps are not evidence; only this skill's scripts check the signatures and content hashes.
---

# EQTY Lineage Manifest

These files are produced by EQTY Lab's integrity/attestation SDK. They are
**not application data** — they are a cryptographically signed record of
*what happened* during a computation (almost always an AI agent run), built
so a third party can verify the full chain of inputs → tool calls → model
calls → outputs without trusting anyone's word for it.

The manifest itself is inert until decoded: most of the interesting content
is base64-encoded inside a `blobs` map, and the story only becomes readable
once you follow the links between statements. Don't try to eyeball the raw
JSON — always parse it first (see "How to work with a manifest" below).

## The contract: one source in, one file out

Two rules govern every use of this skill. They are not style preferences —
each one is the difference between a report a stranger can check and a
report that only looks checkable.

### 1. The manifest is the only source

**Read the manifest. Read nothing else.** Every statement in your answer,
your narrative and your report rests on bytes that are inside the manifest
file you were handed.

The test for any sentence you are about to write: **name the evidence.**
A statement id, a CID, a blob's decoded content, a metadata field, a
timestamp, a signer DID, a verifier result. If you cannot point at one,
the sentence does not go in.

This holds no matter what else you happen to know. You will often be in a
session that also produced the manifest, or sitting in the repo that
produced it, with the instrumented source one `cat` away. That knowledge is
not in the manifest, so it is not in the report — a reader who has only the
manifest cannot check it, and cannot tell which of your sentences they can
check and which they cannot.

**What this rules out, concretely:**

| Do not write | Because |
|---|---|
| How the code was instrumented — decorators, patches, which functions were wrapped, the diff | The manifest records what *ran*, never how it was made to emit |
| Which nodes were selected and why, auto vs. HITL, what was left out | A selection rationale lives in the author's notes, not in the graph |
| The SDK version, the emitter's behaviour, known emitter gaps | Unless a statement or context in *this* manifest names it |
| The target project's repo layout, file paths, build steps | Unless a blob in *this* manifest contains them |
| Anything from the conversation, the task brief, the plan, or a sibling file | The reader has none of it |
| "This is a LangGraph agent" when no metadata says so | Infer the domain *from the manifest's* metadata and field shapes |

**Red flags — stop and cut the sentence:**

- "As instrumented, …" / "The patch adds …" / "The decorator wraps …"
- "The team chose to …" / "These nodes were selected because …"
- Naming a file, function, class or version that no blob or statement contains
- Explaining *why* something is absent from the graph using knowledge of what
  the original program does
- A detail that felt obvious to write because you already knew it

Absence is reportable; its cause usually is not. "No statement records an
ingest above the first computation" is a manifest fact. "The ingest is
missing because the caller wasn't instrumented" is not.

### 2. The report is the only file

**`report.html` is the single artifact this skill writes.** Nothing else
lands beside the manifest — no `summary.json`, no `timeline.txt`, no
`orphans.json`, no `verification.json`, no `attestation.json`, no
`narrative.md`.

Those stages still run; they are how you learn what the run was. They just
stay ephemeral: read them on stdout, or write them under your scratch
directory and let them die there. A file in the output directory is a
deliverable, and a deliverable someone has to keep in sync with the
manifest — every intermediate left behind is a second copy of the truth
that can go stale.

The narrative is not an exception. `--narrative` takes a file because the
prose can be long, not because the prose is an artifact: write it to
scratch, pass it to `report.py`, and it lives on inside the HTML.

```
<outdir>/<target>/
├── <target>.<mode>.manifest.json   input, from the write side
└── <target>.<mode>.report.html     the one thing this skill produces
```

If the user explicitly asks for one of the intermediates — "give me the
timeline as a text file" — give it to them. That is a request, not the
default.

## Trust questions: quote the summary block first

For any question about trust — *was it tampered with, does it verify, who
signed it, what hardware did it run on, can I rely on it* — do this, in order:

1. Run `uv run <skill-dir>/summary.py <manifest>`, where `<skill-dir>` is this
   skill's own directory — the one holding this SKILL.md. Your agent shows its
   path when the skill loads; substitute it. The scripts, `references/` and
   `roots/` all live there.
2. **Paste its output unchanged, in a code block, before any prose** — through
   its last line, including the *Execution environment* lines and the note under
   them. Do not rephrase it, reorder it, shorten it or pick lines out of it.
3. Then explain it in plain words. Every figure you mention must appear in the
   block.

Three things the explanation must never do:

- **Treat statement order or timestamps as evidence of tampering.** `statements`
  is a map keyed by content ID, so its order in the file means nothing, and each
  timestamp is its signer's own clock. Whether anything was tampered with is what
  the block's hashes and signatures say — nothing else.
- **Call missing blobs orphaned, or the reverse.** A *missing pre-image* is a
  blob the manifest references but does not carry; an *orphaned* blob is carried
  but referenced by nothing. Use the block's own words.
- **Attribute intent.** The manifest records what is there. "Deliberately
  omitted" or "selectively shared" is a guess; say what is missing and that the
  export did not include it.

The block already carries every verification result. Run another script only
for a follow-up the block does not answer — typically *what does this blob or
statement say*. For that, use `parse_manifest.py show`:

```bash
uv run <skill-dir>/parse_manifest.py <manifest> show <cid-or-statement-id>
```

Never decode a blob with a snippet of your own. `show` follows collections
and HashSeqs, classifies the content, and reports the blob's
`content_integrity`: `verified`, `tampered`, `invalid_base64`, `missing`,
`by_reference` or `not_checked`. Quote content from a `tampered` blob only as *what the altered
bytes now say*, never as what happened. A `by_reference` blob was left out on
purpose: its signer declared it (`storage="by-reference"`, with a reason) in
metadata that is itself embedded and intact. Say it is committed by hash only
and cannot be checked without the original file; never call it missing or
lost, and never call it verified. A one-liner does none of this. And do
not dump `verify_credentials.py`'s full JSON to re-derive what the block
already says.

## Running the scripts

Run every script with **`uv run`**. Each one declares its own dependencies
(PEP 723), so the first run installs them into a cached environment and later
runs reuse it — no venv to create, no system pip:

```bash
uv run <skill-dir>/summary.py manifest.json
```

- **First run needs network**, to fetch `eqty-sdk`, `cryptography` and `base58`
  (prebuilt wheels, about 15 MB). In a sandbox that blocks network, ask the user
  to allow it for that one command, or to run it once themselves. After that it
  works offline. The command to ask for is the first *verifying* run —
  `summary.py`, or `report.py` without `--no-verify` — never a broader standing
  approval for every `uv run`.
- **A sandbox that cannot write `~/.cache/uv`** (Codex's can't) makes `uv run`
  fail with "Failed to initialize cache". Point uv at a writable temp directory:
  `UV_CACHE_DIR="${TMPDIR:-/tmp}/eqty-uv-cache" uv run <skill-dir>/summary.py manifest.json`,
  and use the same `UV_CACHE_DIR` on every later command so the cache is reused.
- **No `uv`?** Install the same three packages with
  `pip install -r <skill-dir>/requirements.txt`, then run the scripts with
  `python3` in place of `uv run`.
- **Reading a manifest needs no packages**: `parse_manifest.py` runs on the
  standard library alone. Without the packages, every verification reports
  *not checked* — never a pass. Say so rather than presenting a partial answer
  as verified.
- **Never pass `--no-verify` unless the user asked for an unverified report.**
  If the packages cannot be installed, ask for the network approval or tell the
  user; do not downgrade to a report whose trust checks all read *not checked*.

## The verification summary: every check in one block

`summary.py` runs every check this skill has (`references/verification.md`
explains each) and prints one block, in this order:

```bash
uv run <skill-dir>/summary.py manifest.json          # first 10 CIDs per list
uv run <skill-dir>/summary.py manifest.json --full   # every CID
```

```
Summary
=======
32 / 32 DataRegistration verified          <- (1) per statement type
3 / 3 IdentityAttestation verified
* Credential missing for MetadataRegistration urn:cid:…     <- (2) one line per problem
* Signature validation failed for ComputationRegistration urn:cid:…

Signers:                                   <- (3) valid / total per signing DID
Hashes:                                    <- (4) content addresses
Execution environment:                     <- (5) executedOn links, hardware evidence
```

`report.py` prints the same block after writing the HTML, and the HTML's trust
section is rendered from the same result, so the three never disagree. Quote
it as it is (see "Trust questions"): its wording keeps "failed", "not checked"
and "missing" apart, and a paraphrase is where they get merged.

How to read it:

- **A statement is verified** when a credential naming it verifies and the
  statement still hashes to its `@id`. *Credential missing* means no credential
  names it at all: unsigned, not forged.
- **`IdentityAttestation`** credentials name a DID (a machine or workload), not
  a statement, so they get their own row. **Sigstore attestation** bundles sign
  an artifact outside the manifest, so they get their own row too.
- **A credential's status is its signature's.** When a credential verifies but
  its own registration no longer hashes to its `@id`, it counts as *not
  checked*, and these are collapsed into one count line (`--full` lists each).
  Older emitters wrote registrations that drift this way, and the manifest
  alone cannot tell drift from tampering.
- **A signature the SDK cannot check reads *not checked*, never failed**, with
  the reason and what was found — e.g. `(unsupported_proof_type:
  RsaSignature2018)`. That is unknown, not forged (`references/verification.md`).
- **Signers** also reports whether each credential's `issuer` is its
  registration's `registeredBy`. A mismatch is a failure.
- **Execution environment** checks that every `executedOn` names a system the
  manifest gives an identity to, then gives each hardware report its results:
  report signature, vendor chain (a chain too short to reach a root is *not
  checked*, not failed), hardware binding where there is one, and key binding.
  A **TPM** quote has no vendor chain of its own: its attestation key is trusted
  only through *hardware binding* to a report that verified (the line then says
  "vendor chain not needed"). Without that binding it is never verified.
  When the evidence itself is absent, unreadable or of an unsupported type, the
  line reads **nothing to check — <why>** (with the missing blob's CID) instead:
  say the manifest does not carry the evidence, not that the evidence failed or
  could not be checked.
- **Key binding** — does the hardware vouch for the DID it claims? Checked
  when the credential declares `userData: {type: "key"}`: the signed evidence
  must carry the DID's public key — a TPM quote's extraData, an Intel TDX
  report's `REPORTDATA`, an NVIDIA report's SPDM nonce. It reads *verified* only
  when that evidence itself verified; a TPM quote must also be bound to a
  verified hardware report, which then reads *verified (through the TPM quote)*.
  A mismatch is a failure. It shows the attested machine vouched for the DID,
  not that the key never left it — that rests on the measured software, which is
  not compared with reference values.
- **Known gap: key binding without a key claim.** Evidence whose credential
  declares no `userData.type: "key"` (older `EqtyVComp…V0` reports, Azure's
  runtime-data style without a TPM quote) reads *not checked*: its commitment
  rule is defined in EQTY's vcomp code and integrity-py does not expose it. When
  asked "is this hardware bound to that identity" about such a report, say it is
  not verified here.

Exit code: **0** everything verified, **1** something failed or was tampered
with, **2** nothing failed but something is unchecked or missing — including a
missing dependency, when nothing could be checked at all.

## The core mental model: content-addressing + a DAG

1. **Every piece of content has a CID** (`urn:cid:bafkr...` or `urn:cid:baga...`), computed from its own hash (see `blobs`). The same content always gets the same CID — so if you see the same CID reused as an input to multiple computations, that's the *same exact object* being reused (e.g. the running message-history list gets passed through several graph nodes).
2. **`ComputationRegistration.input` → `output` edges form a DAG.** Follow it forward in timestamp order to reconstruct the run as a sequence of steps: each step consumed some prior CIDs and produced a new one.
3. **`MetadataRegistration` is the label-maker.** A raw CID means nothing on its own; check whether some `MetadataRegistration.subject` matches it (or matches the `ComputationRegistration`'s own statement id) to get a name/type for that piece of data or that step.
4. **`CredentialRegistration` is the notarization layer.** It doesn't carry business content — it proves the statement it's about was signed by a specific `did:key` at a specific time.

Because of all this indirection, answering even a simple question like "what
tools did the agent call?" requires: find `ComputationRegistration`s → decode
their `input`/`output` blobs → look for `tool_calls` arrays or `role: tool`
messages in the decoded JSON → cross-reference `MetadataRegistration` for
step names.

## Traps that fail quietly

Each of these produces an answer that looks right and is wrong. The reference
files explain them in full; the rule is here so it applies even when you do
not open them.

- **Field shapes vary.** `input`/`output` can be one CID or a list. Never
  iterate a field without normalising it first (`as_list()`).
- **"Not named by a statement" is not "unused".** A blob can be reachable only
  through another blob (an iroh collection's HashSeq), or only through a
  credential's `evidence` or an attestation's `identity` (runtime data,
  cloud-init, container configs). Trust `orphans` only after that resolution,
  which the scripts do. What remains is unreferenced, not wrong: say what it is
  (a TPM event log nothing links, say) rather than calling it an error.
- **Not every blob is JSON text.** Blobs can be binary, an iroh collection
  listing, or invalid base64. Report each as what it is; never print mangled
  text or guess at what a corrupt blob said.
- **Claimed is not verified.** A step with `executedOn` *claims* an
  environment. Only verified hardware evidence says the hardware is genuine,
  and nothing here says which code ran on it.
- **Unchecked is not failed, and failed is not forged.** A `null` or
  *not checked* is unknown; a failed signature is "not verified", not proof of
  forgery. Never round either up to "looks fine".
- **Personal data.** Metadata about an actor (`subject` is a `did:key`) can
  hold names, emails and phone numbers. Mention it only when the user asks who
  is behind a signature.

## How to work with a manifest

**Always use `<skill-dir>/parse_manifest.py` rather than hand-rolling JSON
parsing.**

> These scripts all live in `<skill-dir>`, alongside the `roots/` folder of
> pinned vendor certificates they read (see "Running the scripts" for
> dependencies). Commands below are written as `<skill-dir>/<script>.py`.
> `parse_manifest.py` already implements CID-stripping, base64 decoding, and
> the subject→metadata / data→registration lookups
> (`references/manifest-format.md`).

```bash
# Quick orientation: statement counts, signers, time range, integrity
# problems, AND orphaned_blobs — the latter already accounts for iroh
# HashSeq/collection resolution (see "Traps that fail quietly"), so a non-empty result here
# means genuinely unused content, not just indirectly-referenced content.
uv run <skill-dir>/parse_manifest.py manifest.json summary

# The reconstructed, chronological, human-labeled computation DAG.
# Previews are fully resolved: a HashSeq pointing at a named file
# collection shows the actual file names and contents, not opaque binary.
uv run <skill-dir>/parse_manifest.py manifest.json timeline

# Blobs unreachable even after HashSeq/collection expansion — a true
# "unused content" check, not just "not directly named in a statement."
uv run <skill-dir>/parse_manifest.py manifest.json orphans

# Where each step ran, and whether any environment was claimed at all.
# Reports CLAIMS — evidence is not cryptographically verified.
uv run <skill-dir>/parse_manifest.py manifest.json attestation

# Everything known about one CID or statement id — content is fully
# resolved through any collection/HashSeq indirection.
uv run <skill-dir>/parse_manifest.py manifest.json show "urn:cid:bafkr4i..."

# Full-text search across every blob's fully-resolved content (finds text
# hiding behind a HashSeq/collection too) and raw statements
uv run <skill-dir>/parse_manifest.py manifest.json search "gpt-4o-mini"

# Full resolved dump (every statement with resolved content inlined, plus
# orphaned_blobs) — use this if you need to grep/filter programmatically
# for a complex question. Scratch only: dump it under your scratch
# directory, never beside the manifest.
uv run <skill-dir>/parse_manifest.py manifest.json export "$SCRATCH/resolved.json"
```

Every command above prints to stdout by design. Read them; don't redirect
them into the output directory. `export` is the one that takes a path, and
that path belongs in scratch.

To hand the user a report rather than an answer in chat, use
`<skill-dir>/report.py` — one self-contained HTML file, no external assets,
no scripts, no network:

```bash
# Structure, participants, phases and all three trust checks, computed
uv run <skill-dir>/report.py manifest.json report.html

# With your own interpretation slotted into "What this run was" — do this
# whenever you have actually read the run, because it is the one section
# the script cannot produce. The notes file is scratch: it is consumed
# into the HTML and does not ship.
uv run <skill-dir>/report.py manifest.json report.html \
    --narrative "$SCRATCH/notes.md"
```

**Write the narrative after the verified run, and only about what the run
did** — the task, the steps, the tools, the output. Never state in it whether
the manifest verified: the report's trust section computes that, and a
sentence written before the checks ran goes stale the moment they do.

Before passing that narrative, walk it sentence by sentence against rule 1
above and name the manifest evidence for each one. Prose is the only part
of the report the script cannot check for you, which makes it the only part
that can smuggle in something the manifest never said.

It runs the verifiers itself rather than taking your word for the results,
so the numbers in the file are computed by the file. See "Making it
consumable for a human" below.

Typical workflow for answering a user's question:

1. Run `summary` to get oriented (how many steps, what time range, one
   signer or many, unusually large/small). Check its `integrity_problems`
   list — if non-empty, decide whether that's relevant to what the user
   asked (a corrupt blob might be exactly what "does this file look
   tampered with" is asking about, and it may correspond to a specific
   named file once you resolve the collection it belongs to —
   `references/manifest-format.md`).
   `orphaned_blob_count` should be trusted here — it already accounts for
   HashSeq resolution, so a non-zero count means something is genuinely
   unused, not just indirectly referenced.
2. Run `timeline` and read it — its previews are already fully resolved
   (collections and HashSeqs included), so for agent-run manifests this
   alone usually answers "what happened," "what tools were used," "what
   was the final answer." For data-pipeline manifests (`references/manifest-format.md`) it shows
   the actual named file contents a computation step consumed/produced,
   not just opaque binary CIDs.
3. For a specific detail, use `show <cid>` on any CID named in the
   timeline output — it returns the same fully-resolved content.
4. For "does X appear anywhere," use `search` (it searches resolved
   content, so text behind a HashSeq/collection is found too).
5. For anything requiring aggregation/filtering across many statements
   (token usage totals, a table of every tool call with arguments, a chart
   of latency between steps), use `export` to get a fully-resolved JSON tree
   and then write a short Python snippet against it rather than the raw
   manifest — the raw manifest's indirection makes ad hoc filtering error
   prone.
6. If the user wants something to keep, read, or pass on — "a report," "a
   summary I can send," "make this readable" — run `report.py` and pass
   your own interpretation via `--narrative`. Don't hand-author HTML: the
   script computes the trust numbers, and a report whose figures you typed
   is hearsay with styling. The HTML is the only file you leave behind;
   steps 1–5 above are reading, not writing.

## Making it consumable for a human

Users generally want one of:

- **A durable report** — "make this readable," "something I can send
  someone," "a report on this run." Run `<skill-dir>/report.py` (see the
  command list above) and pass your own reading of the run via
  `--narrative`. The script computes the metrics, participants, rolled-up
  phases and all three trust checks; your prose supplies what the run
  *meant*, which is the one part no script can derive. The result is one
  self-contained HTML file, so it stays true after the conversation is
  gone — and its trust figures are computed by the file itself rather than
  copied from what you ran in chat. It is also the **only** file to write:
  see "The report is the only file" above.
- **A narrative summary** — "the agent was asked X, called tools A and B,
  and answered Y" — built directly from the `timeline` output. Prefer this
  as the default response shape; don't dump raw CIDs or JSON at the user
  unless they ask for the underlying data. Answer in chat, or fold it into
  the report via `--narrative`; don't leave a `narrative.md` on disk.
  Everything in it is a manifest fact — see "The manifest is the only
  source" above, and note that this is exactly the surface where knowledge
  from elsewhere in the session leaks in.
- **A table/filtered slice** — e.g. "list every tool call with its
  arguments and result," or "show me all the timestamps." Build this from
  `export`'s resolved dump with a short Python/pandas snippet, then render
  it as a markdown table or, for larger/plottable data, use the Visualizer
  or a code-execution chart.
- **A trust/integrity question** — "was this tampered with," "who signed
  this," "can I verify this independently." Run `<skill-dir>/summary.py`,
  quote its block, then explain it (see "The verification summary" above). It
  runs the checks that fail independently: content (do the bytes match their
  CIDs), signatures (are the credentials genuine) and execution environment
  (what is claimed, and does the hardware evidence verify). Reporting only the
  signature result is how "all 106 credentials verify" ends up printed
  next to falsified numbers. Attestation has two layers of its own: whether
  an environment is *claimed*, and whether its evidence *verifies* — and
  even a verified report does not say what code ran. Don't stop at structural
  observations (same `registeredBy` DID throughout, `proof.type` is
  `Ed25519Signature2018`) and call that "verification" — those only tell you
  the shape looks right. Report the block's real results, including a failed
  or *not checked* one, and never round either up to "looks fine."

Avoid presenting raw `urn:cid:...` strings or base64 blobs as the headline
of an answer — they're addressing/integrity machinery, not something a
human user is asking about unless they explicitly want to inspect content
addressing itself.

## Reference files

Open these when a question needs the detail. They are part of this skill,
beside this file.

| File | Read it when |
|---|---|
| `references/manifest-format.md` | you need the statement types and their fields, how blobs reference other blobs (iroh collections, HashSeqs), blob content kinds, or how to tell what kind of run a manifest records |
| `references/verification.md` | a question goes beyond the summary block: how signatures, Sigstore bundles, hardware evidence, execution environments and content addresses are checked, and exactly what each result does and does not prove |
| `roots/PROVENANCE.md` | someone asks where the pinned vendor root certificates came from |
