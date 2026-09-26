# Verification in detail

Part of the `eqty-manifest` skill. For any trust question, run `summary.py`
and quote its block first (`SKILL.md`, "Trust questions"). This file explains
what each check behind that block does, and what its results do and do not prove.

## Execution environments and hardware attestation

Beyond *what* ran, a manifest can assert *where* it ran — which physical or
virtual environment executed a step, backed by a TEE/enclave measurement.
This is what answers "is any step not hardware-attested," and it lives in
fields none of the statement types in `references/manifest-format.md` mention:

| field | on | meaning |
|---|---|---|
| `executedOn` | `ComputationRegistration` | the `did:key` of the environment this step ran in |
| `vcomp` | `DidRegistration` | the environment descriptor for that DID — `@type` is one of `EqtyVCompDockerV1`, `EqtyVCompAmdSevV1`, `EqtyVCompIntelTdxV0`, `EqtyVCompAzureV1`, `EqtyVCompNvidiaCcV0` |
| `evidence` | a `CredentialRegistration`'s credential | the raw attestation material — certificate chains, SEV measurements, TDX quotes, NVIDIA CC reports — usually as CIDs pointing at blobs |
| `sigstoreBundle` | `CredentialRegistration` | a Sigstore DSSE envelope over the attestation |

**There are two different ways an environment gets attached, and a reader
that knows only one will under-report attestation:**

1. `executedOn` → a `DidRegistration` carrying `vcomp` (seen in
   `gnn-train`, `multiple-attestation`)
2. `executedOn` → a DID that is the `credentialSubject` of an *identity
   credential* carrying `identity` + `evidence`, with no `DidRegistration`
   anywhere (seen in `model-dev-inf`)

`parse_manifest.py attestation` resolves both and reports per step:

```bash
uv run <skill-dir>/parse_manifest.py manifest.json attestation
```

**Read its output precisely — there are two layers here, and they are
reported separately.**

*Per step*, `steps_with_claimed_environment` counts what the manifest
*asserts*, and every step entry carries `evidence_verified: false` with
`evidence_verification: "not_checked_here"`. Those fields describe the
**graph traversal**, which verifies nothing: they mean "not verified by this
lookup," not "cannot be verified."

*Per manifest*, the same command's top-level `evidence_verification` key
holds the **real** result — `verify_attestation.py` parses TDX quotes,
SEV-SNP reports and NVIDIA CC responses and checks their signature chains
against vendor roots pinned in `roots/`. Read that key, not the per-step
booleans, when the user asks whether attestation actually holds up. When
`cryptography` is missing its status is `unavailable`, never a pass.

So, in increasing strength — say only as much as you have:

- "this step claims it ran in an AMD SEV enclave" — supported by a claimed
  environment alone, report it
- "its attestation report was genuinely signed by AMD/Intel silicon and is
  intact" — supported **only** when `evidence_verification` verified that
  step's evidence
- "this step provably ran the code you expect" — **never** supported.
  Reference measurements (Intel TCB info, NVIDIA RIM, expected OVMF/kernel
  values) are not checked, so even fully verified evidence says the hardware
  is authentic, not what ran on it.

A step with no `executedOn` at all is a third state, distinct from both:
the manifest makes *no claim* about where it ran. That is not "untrusted"
and definitely not "attested" — say plainly that no environment was
asserted. (`FLARE_docker.json` is exactly this: 8 steps, zero environment
claims, despite the filename.)

Attestation evidence blobs are referenced from *inside* credential
`evidence` and `vcomp` objects rather than from a top-level statement
field, so they are followed by `content_referenced_cids()` explicitly — the
same "reachable only through another document" trap as HashSeqs
(`references/manifest-format.md`).

## Verifying signatures cryptographically

`parse_manifest.py` is structural: it decodes and
resolves content but never checks whether a `CredentialRegistration`'s
signature is actually valid. For that, use the bundled
**`<skill-dir>/verify_credentials.py`** — a deliberately separate script, not a
function added to `parse_manifest.py`, because it is a different concern
(cryptographic proof checking vs. content decoding), so a missing dependency
or a verification failure shouldn't block the much more commonly-needed job
of just reading the manifest.

**It delegates to the SDK; it no longer reimplements the proof check.**
`eqty_sdk` 2.4.1 exposes the same two checks with the W3C contexts compiled
into the package, both fully offline:

```python
verify_statement(statement_json, contexts=None)        # does it still hash to its @id?
verify_vc(vc_json, statement_id=None, contexts=None)   # is the proof good, and about THIS statement?
```

This replaced a hand-rolled `pyld` + `cryptography` implementation that
shipped no contexts and never fetched one. That design was sound in intent —
a verdict shouldn't depend on files sitting beside the script — but it meant
any credential whose contexts the manifest didn't itself embed came back
`canonicalization_failed`, i.e. unchecked. On the current mode-2 manifests
that was **every** credential, because manifests embed only the two
`urn:cid:` EQTY vocabularies, not the W3C `credentials/v2` / `security/v2`
documents their credentials declare.

The original property survives the move: contexts now come from a pinned,
versioned dependency that resolves them offline, not from a `contexts/`
folder anyone can drop a file into. The skill still ships no loose context
files. A manifest's own `contexts` are still passed through and still take
**precedence**, which is what keeps a credential signed under an older
revision of `credentials/v2` verifying.

**`did:key` isn't always Ed25519.** The SDK handles whichever key type a
proof actually uses; the script still decodes the multicodec prefix itself to
*report* `keyType`, and to verify the Sigstore DSSE path, which is local
code. The types seen across the **development corpus** — the five intact example
manifests (plus one deliberately broken copy) this skill was developed against.
They live in the skill's source repository, not in the skill, so every count
quoted from them below is background, not something to look for on disk:

| key type | multicodec | proof type and JWS `alg` seen verifying through the SDK |
|---|---|---|
| ed25519 | `0xed` | `Ed25519Signature2018`, alg `EdDSA` (852 credentials) |
| P-256 (secp256r1) | `0x1200` | `EcdsaSecp256r1Signature2019`, alg `ES256` (159 credentials) |
| secp256k1 | `0xe7` | none in the corpus — the key is decoded, but no proof type is known to verify |
| P-384 (secp384r1) | `0x1201` | none in the corpus — as above |

`verify_vc` returns a **bare boolean**: a `false` folds together a bad proof,
a subject mismatch, and a proof the SDK simply cannot check. So when it says
`false`, the script looks at the credential's fields (no cryptography) to
recover the last case, and reports it as **not checked** (`valid: null`), never
as failed:

| reason | when | `found` |
|---|---|---|
| `unsupported_proof_type` | the proof type is not one the SDK is known to verify (table above) | the proof type |
| `alg_mismatch` | the JWS header's `alg` disagrees with the proof type | the header's `alg` |
| `key_type_mismatch` | the signing `did:key` is a different key type than the proof type uses | the key type |
| `unrecognized_context` | an `@context` URL is neither in the manifest's `contexts` nor one the SDK compiles in (`credentials/v2`, `security/v2`, `security/v1`) | the URL |

Otherwise the result is `signature_mismatch`: **not verified**, but still not
proof of forgery. Two limits, by design:

- **It never makes anything verified.** It only runs after the SDK said
  `false`, so the worst a tamperer gains by also editing these fields is *not
  checked* instead of *failed*.
- **The known lists are observations.** integrity-py does not publish which
  proof types and contexts it supports, so these are the ones seen verifying.
  If the SDK adds one, it returns `true` and the lists are never consulted; a
  stale list can only turn a *failed* into *not checked*. The real fix is
  upstream: `verify_vc` returning a reason instead of a boolean.

```bash
uv run <skill-dir>/verify_credentials.py manifest.json                  # verify every credential
uv run <skill-dir>/verify_credentials.py manifest.json <statement_id>    # verify just one
```

Output carries **two independent axes** — `signatures` and
`statementIntegrity` — plus a `summary` tallying each. Do not merge them.

Each result is `{"valid": true, "keyType": ...}`,
`{"valid": false, "reason": ..., "detail": ...}` (`signature_mismatch`,
`subject_mismatch`, or `malformed_credential` — a credential that does not
parse is a defect in the file, not an unchecked one), or
`{"valid": null, "reason": ..., "detail": ...}` for something that couldn't
be checked at all (`unsupported_proof_type`, `alg_mismatch`,
`key_type_mismatch`, `unrecognized_context` — see above —
`unresolvable_signing_key` — a DID method needing the network — `no_proof`,
`no_signature`, `verifier_error`).
`null` is not "passed," treat it as "unverified," not as "trusted."

**Two axes, and neither implies the other.** `verify_statement` asks whether
a statement still hashes to its `@id`; `verify_vc` asks whether a proof is
good. A credential lifted verbatim into a different `CredentialRegistration`
keeps a **valid signature** — it still truthfully attests its original
subject — and is caught *only* by statement integrity. Conversely a statement
can hash correctly and carry a proof that does not verify. Report both.

A `false` on statement integrity (`id_mismatch`) is also not automatically
tampering: three older manifests in the development corpus have `CredentialRegistration`
statements that no longer re-hash while their credentials verify perfectly —
canonicalization drift from the emitter that wrote them. Manifests emitted by
SDK 2.4.1 hash clean throughout.

**All 390 signed statements in the development corpus now verify** (379 W3C
credentials + 11 Sigstore bundles). Before the move to the SDK only 80 could
be checked at all; the other 310 came back `canonicalization_failed` purely
because their manifests omitted the W3C contexts.

**Self-containment is still a separate, still-open question about the
emitter.** Only 2 of its 5 intact manifests (`model-dev-inf`,
`multiple-attestation`) embed every JSON-LD context their credentials
reference. The other 3 verify here because the *SDK* supplies those contexts,
not because the files carry them — hand one to a party without the SDK and it
is not checkable. The test suite (`tests/eqty-manifest/run_tests.py` in the source
repository, beside those manifests; it does not ship with the skill) measures that property directly rather than
through the verifier, precisely so the SDK cannot paper over it.

A manifest may list **several revisions** of the same context URL as an
array. That is not exotic: a manifest aggregates credentials from more than
one issuer — the emitter, plus self-issued hardware identity credentials —
and those issuers need not have signed against the same revision of a W3C
context. `model-dev-inf.json` is exactly this case: 44 credentials signed
under the older `credentials/v2` and 2 self-issued identity attestations
under the current one. The SDK resolves this internally now; the script
reports `contextSource` as `manifest+sdk-embedded` when the manifest
supplied contexts of its own, or `sdk-embedded` when it did not.

**Contexts come from the manifest first.** A manifest may embed its own
`contexts` map — treat it as load-bearing for verification, not as the
boilerplate the "Top-level shape" section of `references/manifest-format.md`
calls it. The W3C
`credentials/v2` context was revised (it lost the `@vocab` that made
otherwise-undefined terms like `issuanceDate` expand), so a credential
signed under the older revision will NOT verify against a current copy even
though nothing was tampered with. `verify_manifest` therefore passes the manifest's own `contexts`
straight through to the SDK, where a **supplied context takes precedence**
over the SDK's compiled-in copy of the same URI. A manifest that embeds the
revision its credentials were signed under is verified against that revision;
one that embeds nothing falls back to the SDK's copies. `contextSource`
records which case applied (`manifest+sdk-embedded` or `sdk-embedded`).

Across the development corpus **all 390 signed statements now verify** — 379
W3C credentials plus 11 Sigstore bundles. Before the SDK only 80 were
checkable, the other 310 reporting `canonicalization_failed` purely because
their manifests omitted the W3C contexts. That reason code no longer occurs;
`verify_vc` returns a bare boolean and never reports it.

The older caution still applies in a narrower form: a `null` is **unchecked,
not failing**, and must never be described as invalid or forged.

This is validated, not just plausible-looking: it correctly verifies all 62
genuine Ed25519 credentials in a real agent-trace manifest, and correctly
flips to `valid: false` when a credential's content is tampered with
afterward. The secp256k1/P-256/P-384 paths were validated the same way
using synthetically-generated keys and signatures built by hand from the
same spec (no real-world example manifest using those key types was
available to test against) — each one round-trips correctly (verifies when
genuinely signed, flips to `false` when tampered with).

**A credential can be well-formed (right shape, right fields, decodable
JWS) and still fail cryptographic verification.** Don't equate "this parses
as a CredentialRegistration with a jws field" with "this is authentic" —
those are different questions, and only `verify_credentials.py` answers the
second one. If a user asks "was this tampered with" or "can I trust this,"
run the verifier and report exactly what it says (including `null`/
inconclusive results and why), rather than inferring trust from structure
alone.

## Two signing mechanisms, not one

A `CredentialRegistration` statement carries **either** a W3C `credential`
**or** a `sigstoreBundle` — and a reader that only knows the first will
report the second as empty. Across the development corpus: 379 statements
carry a W3C credential and 11 carry a Sigstore bundle instead (7 in
`gnn-train`, 3 in `model-dev-inf`, 1 in `multiple-attestation`; none in
`FLARE_docker` or `travel-assistant`).

A Sigstore statement has only `registeredBy`, `subject`, `timestamp` and
`sigstoreBundle`. The bundle wraps an **in-toto v1 attestation** in a DSSE
envelope (`application/vnd.in-toto+json`) — supply-chain provenance, e.g.
binding a model artifact to its SHA-256. `verify_manifest` verifies these:
it rebuilds the DSSE pre-authentication encoding
(`DSSEv1 <len> <type> <len> <payload>`) and checks the signature against the
`did:key` named in the envelope's `keyid`. Results carry
`signatureFormat: "sigstore-dsse"`.

**Never describe these as "no credential" or "nothing to check."** There is
a real signature present; calling it absent implies the opposite of the
truth. The reason code for a genuinely unsigned statement is `no_signature`,
and none of the development manifests have any.

**What is NOT checked:** Sigstore's transparency log. These bundles carry an
empty `tlogEntries` and only a key `hint` rather than a Fulcio certificate,
so there is no Rekor inclusion proof to validate and no identity to bind. A
valid result means the payload was signed by that DID — not that it was
publicly logged. Results say so via `transparencyLogChecked: false`.

## Verifying hardware attestation evidence

`<skill-dir>/verify_attestation.py` checks whether the TEE evidence a manifest
carries was genuinely produced by vendor silicon. This is a **third,
independent** check — a manifest can have valid signatures, intact content,
and still carry attestation evidence that fails, or vice versa.

```bash
uv run <skill-dir>/verify_attestation.py manifest.json
```

It handles Intel TDX quotes, AMD SEV-SNP reports (both the older
`EqtyVCompAmdSevV1Evidence`, chain embedded after the report, and
`VCompAmdSevEvidenceV1`, chain as its own `certificateChain` blob), NVIDIA CC
(SPDM) reports and TPM 2.0 quotes, verifying the certificate chain, the report
signature, and — for TDX — that the attestation key is bound to the QE report.

**TPM 2.0 quotes (`VCompTpmEvidenceV1`, e.g. an Azure confidential VM's vTPM)**
are checked in four parts, each reported separately:

1. **Quote signature** — the quote verifies under the evidence's `AKPublicKey`
   (RSASSA or RSA-PSS over SHA-256/384).
2. **PCR claim** (`bound_pcr_claim_to_quote`) — the PCR values the credential's
   `identity.pcr` claims hash to the digest the TPM signed.
3. **Hardware binding** — the attestation key is trusted *only* through a
   hardware report for the same identity that verified against a pinned root —
   an AMD SEV-SNP report or an Intel TDX quote. That report's signed
   `report_data` must begin with the SHA-256 of the VM's runtime data
   (`identity.userData.source`, zero padding removed), and the runtime data's
   `HCLAkPub` must be this attestation key. `report_data` sits at byte `0x50` of
   the AMD report and at byte 568 of the TDX quote (the TD report's
   `REPORTDATA`, after the 48-byte quote header).
4. **Key binding** (`key_bound_to_did`) — when the credential declares
   `identity.userData = {type: "key", value: <DID>}`, the value must be the
   credential's own subject and the quote's `extraData` (TPM2 qualifying data)
   must be that DID's P-256 public-key **X coordinate** — the compressed key
   without its `02`/`03` prefix. It counts only when parts 1 and 3 verified; the
   hardware report the quote is bound to then reads bound to the DID *through the
   TPM quote*. For any other DID type no check is added and key binding stays
   *not checked*.

**Key binding on Intel TDX and NVIDIA reports** follows the same rule directly,
with no TPM in between. When the credential declares `userData: {type: "key",
value: <its own DID>}`, the DID's X coordinate must sit in a field the hardware
signs:

| Evidence | Where | Signed by |
|---|---|---|
| TPM 2.0 quote | `extraData` | the attestation key (bound as above) |
| Intel TDX quote | `REPORTDATA[32:64]` (quote bytes 600–632); the first half is zero | the TD report, through the quoting enclave |
| NVIDIA CC report | nonce of the opening SPDM `GET_MEASUREMENTS` request (code `0xE0`), report bytes 4–36 | the SPDM signature, which covers request and response |

It counts only when that report's own signature and vendor chain verified. The
rule is observed, not published: it holds in all 16 key-claiming reports seen
across five manifests (TPM, TDX, NVIDIA). Evidence with another `userData` type —
Azure's `sha256` runtime data, or none in the older `EqtyVComp…V0` layout —
stays *not checked* unless a TPM quote binds it.

Which of these links are Azure's: part 3's runtime-data layout (`HCLAkPub`,
`report_data` = SHA-256 of the runtime JSON) is Azure's paravisor; part 4 is
EQTY's convention and plain TPM 2.0.

No TPM root is pinned, so the attestation key's certificate (issued by
Microsoft's vTPM CA) is **not** checked; the hardware binding replaces it. A
quote whose key has no such binding stays *not checked*: a signature by a key
the file itself supplies proves nothing. As with every vendor, the PCR *values*
are not compared with expected ones. Chains are checked
against **pinned vendor roots in `roots/`**, never against the root that
shipped inside the evidence: every evidence blob embeds a self-consistent
chain, so trusting its own root would verify a forgery just as happily.

**What a `true` result means, precisely:** this report was signed by a key
chaining to a genuine Intel/AMD/NVIDIA root and its bytes are intact. It does
**not** mean the enclave ran the code you expect. That needs reference
measurements (Intel TCB info, NVIDIA RIM, expected OVMF/kernel values) which
are not bundled, so every result carries `measurements_checked: false`.
Never collapse "authentic hardware" into "ran the right code" — the gap
between them is exactly where a genuine-but-compromised machine sits.

All three vendors are pinned, so all 9 evidence blobs in the development corpus
verify. A vendor whose root is *not* pinned — an AMD product line other than
Milan, say — returns `valid: null` / `trust_anchor_unavailable`: chain and
signature are verified, but nothing anchors them. That is a real state, not
a bug; see `roots/PROVENANCE.md` for coverage limits.

`parse_manifest.py attestation` runs this automatically when `cryptography`
is installed and reports it under `evidence_verification`; when the
dependency is missing the status is `unavailable`, never a pass.

## Content-address verification (separate from signatures)

Signature verification and content verification answer **different
questions**, and passing one says nothing about the other:

- `verify_credentials.py` proves *a signer attested to a statement*.
- Content verification proves *a blob's bytes are the bytes that CID names*.

Credentials sign statement ids; statements reference blobs by CID. So
editing a blob's payload leaves **every signature valid** while the content
silently no longer matches its address. Before this check existed, altering
a reported metric inside a blob produced "106/106 credentials valid" and no
integrity problem at all.

`parse_manifest.py summary` now reports `content_verification`:

```json
{"status": "verified", "matched": <n>, "total": <present + missing>,
 "mismatched": ["<cid>", ...], "missing": ["<cid>", ...],
 "by_reference": {"<cid>": {"name": ..., "reason": ..., "obtain_from": ...}},
 "invalid_base64": ["<cid>", ...], "unverifiable": <n>}
```

`status: "verified"` means the check **ran**, not that it passed — read the
lists. `total` counts every blob present *or* referenced, so a blob deleted
from `blobs` shows as a gap (N/M), never as a smaller clean total. The three
failure reasons mean different things and are never merged:

| Field / problem | Meaning |
|---|---|
| `mismatched` / `cid_mismatch` | bytes present but do not hash to their CID — content altered after registration |
| `missing` / `missing_blob` | a statement names the CID but `blobs` does not carry it — not embedded, so unverifiable |
| `by_reference` / `by_reference` | not embedded either, but **declared**: the CID's own metadata says `storage="by-reference"` with a `storage_reason`, and that metadata blob is embedded and hashes to its CID. The signer's claim, not a check — still unverifiable, but not a gap in the record, so it does not make `summary.py` exit 2 |
| `invalid_base64` / `invalid_base64` | bytes present but not decodable — cannot be hashed at all |

Each also appears in `integrity_problems` under the issue name shown; a
`cid_mismatch` is the strongest tampering signal in the file. Blob CIDs are BLAKE3-256
(multihash `0x1e`); the check recomputes each one with `eqty_sdk.get_cid_for_bytes`,
the same code that produced them, so it needs the SDK the signature checks already use
(see "Running the scripts" in `SKILL.md`). Without it, `status` is `"unavailable"` and an explicit
`content_verification_unavailable` problem is raised. **Treat that as
UNKNOWN, never as clean** — the same discipline as a `null` from the
signature verifier.
