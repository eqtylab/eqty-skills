# Manifest format

Part of the `eqty-manifest` skill. Read `SKILL.md` first: its contract
(the manifest is the only source; the report is the only file) applies to
everything here.

## Top-level shape

```json
{
  "version": "3",
  "contexts": { ... JSON-LD @context definitions, safe to ignore ... },
  "statements": { "<statement_id>": { "@type": "...", ... }, ... },
  "blobs": { "<content_id_no_prefix>": "<base64>", ... }
}
```

`contexts` is schema boilerplate (defines the vocabulary — you never need to
read it to answer a user's question). All the actual content lives in
`statements` and `blobs`.

## Statement types

Every entry in `statements` is a signed Verifiable Credential wrapper with a
common shell:

```json
{
  "@type": "<one of the types below>",
  "registeredBy": "did:key:...",
  "timestamp": "2026-07-21T20:46:00Z",
  "credential": { ... a signed proof, only on CredentialRegistration ... },
  ... type-specific fields ...
}
```

The types you'll actually encounter, and what to do with each:

| @type | Purpose | Key fields | What to extract |
|---|---|---|---|
| `DataRegistration` | Registers one piece of content by CID | `data` (the content CID) | Look up `data` in `blobs` to get the actual payload (a prompt, a message list, a tool schema, a tool result, a raw file, etc.) |
| `ComputationRegistration` | Records one step of computation | `input`, `output` (each **either a single CID string or a list of CIDs** — both conventions appear across manifests, always normalize), optional `computation` (a CID identifying the tool/script/model definition that was run), `operatedBy` | **This is the spine of the run.** Each one is a node in the execution DAG: input(s) produced output(s). Sort by `timestamp` for chronological order. |
| `MetadataRegistration` | Attaches human-readable context to a CID, another statement, **or an actor** | `subject`, `metadata` (CID of the description) | Decode `metadata` to learn *what a step/piece of data/actor actually was*. `subject` can be a data CID, a statement id, **or a `did:key:...`** — the last case is metadata *about an actor* (e.g. contact info for whoever signed things), not about a piece of data. Don't assume subject is always content. |
| `CredentialRegistration` | The signed W3C Verifiable Credential proving who registered what, when | `credential.credentialSubject.id` (the statement being certified), `credential.proof` (Ed25519 signature), `issuer` | Mostly integrity/audit plumbing — you don't need to touch this to answer most questions. If the user specifically asks about trust, tampering, or signature validity, run `<skill-dir>/summary.py` and quote its block (`SKILL.md`, "Trust questions"), rather than eyeballing the JSON; a well-formed `proof`/`jws` is not the same as a verified one. |
| `AssociationRegistration`, `EntityRegistration`, `DidRegistration`, `GovernanceRegistration`, `StorageRegistration` | Less common; register relationships, entities, DIDs, policy documents, or storage locations | varies | Handle generically: these just assert a fact about a subject/entity. Rarely load-bearing for a user's question — mention only if directly relevant. |

**Don't hard-code field shapes.** Two manifests can both be valid and still
disagree on whether `ComputationRegistration.input`/`output` is a scalar CID
or a list of CIDs, and on whether a `computation` field is present at all.
Always normalize with something like `as_list()` in the bundled script
rather than assuming `for cid in statement["input"]` is safe — iterating a
string CID character-by-character is a real, silent failure mode, not a
theoretical one.

## A blob can be referenced from inside another blob, not just from a statement

**The statement graph (`data`/`input`/`output`/`computation`/`metadata`
fields) is not the only way a blob gets used.** Iroh collections are
actually *two* blobs working together:

1. A small text blob in the `CollectionV0.` format — a count byte followed
   by length-prefixed filenames (e.g. decodes to `CollectionV0.\x03\x0binput-1.txt\x0binput-2.txt\x0binput-3.txt`,
   a 3-file collection). This only gives you **names**.
2. A separate **binary blob that is nothing but N concatenated 32-byte
   BLAKE3 digests** — one per blob key in `blobs` (blob keys are
   themselves multibase/CIDv1-encoded digests, so this is directly
   checkable: base32-decode the key, skip the CID/multihash header
   varints, and what's left is the raw digest). By convention, chunk 0 of
   this "HashSeq" is the digest of the CollectionV0 names blob above, and
   chunks 1..N are the digests of the actual per-file content blobs, in
   the same order as the names list.

So a `DataRegistration`/`ComputationRegistration` field can point at a
128-byte (or 64-byte, or any multiple-of-32) blob that looks like
meaningless binary — and *is* meaningless until you split it into 32-byte
chunks and match each chunk against every other blob's own digest. Once
resolved, `input-1.txt`, `input-2.txt`, etc. turn out to be ordinary
JSON/text blobs elsewhere in the same file, each one legitimately part of
the run, just never mentioned by name in any statement.

**This means "not referenced by any statement field" is not the same as
"unused."** A blob reachable only by decoding another blob's raw bytes as
a HashSeq is just as load-bearing as one referenced directly. Don't call
something orphaned until you've expanded through this resolution first —
the bundled script's `Manifest.resolve()` does this automatically
(recursively, so a HashSeq entry can itself be another HashSeq), and its
`orphaned_blobs()` only reports blobs unreachable *after* that expansion.
Always call `resolve()` (or the `content`/`content_preview` wrappers built
on it) rather than the flat single-blob `decoded()`/`decode_blob()` when
you want the true content behind a CID.

One practical payoff: this resolution can also tell you *which named file*
is corrupted. In one real manifest, a HashSeq's second chunk pointed at a
blob that failed to base64-decode at all — meaning it wasn't just "some
corrupt blob," it was specifically `input-2.txt`'s content that was
corrupted, while `input-1.txt` and `input-3.txt` were intact and (per the
accompanying script and matching output values) had clearly been processed
correctly. Resolving the collection is what makes that distinction
possible — treat any `invalid_base64` entry as call for checking whether
it's referenced from within a HashSeq, and name the file if so.

## Blob content isn't always JSON text

Don't assume every decoded blob is a UTF-8 JSON string. In practice you'll
run into all of these, and treating them uniformly (or blindly
`.decode("utf-8", errors="replace")`-ing) will either crash or silently
corrupt the data you show the user:

- **Plain JSON or text** — the common case, covered above.
- **Opaque binary** — raw file bytes, hash digests, or other non-text
  content. This is legitimate, not an error: report it as
  "binary blob, N bytes" (optionally with a hex preview), never as mangled
  replacement-character text.
- **iroh collections** — some manifests reference [iroh](https://www.iroh.computer/)
  content-addressed file collections. A simple text-based collection blob
  looks like `CollectionV0.` + a count byte + length-prefixed filenames
  (e.g. decodes to `CollectionV0.\x03\x0binput-1.txt\x0binput-2.txt\x0binput-3.txt`
  — a 3-file collection). This only gives you the **file names**, not their
  content — the actual per-file bytes live in the producing system's iroh
  node/blob store and are usually *not* embedded in the manifest itself. Say
  so plainly rather than implying the file contents are recoverable from
  the manifest alone.
- **Invalid / corrupted base64** — a blob value that fails to base64-decode
  at all. This can mean truncation, a bad export, or tampering. Never let
  this crash your parsing of the rest of the manifest — flag it as a
  specific, named problem (which CID, what error) and keep going; don't
  guess at what the content "would have been."

The bundled script's `decode_blob()` already classifies every blob into one
of `json` / `text` / `binary` / `iroh_collection` / `invalid_base64` — use
that classification rather than re-implementing decoding by hand, and
surface `invalid_base64` / missing-blob findings to the user when they ask
about integrity, completeness, or "does anything look wrong with this file."

**Watch for PII.** Actor/entity metadata (`MetadataRegistration` where
`subject` is a `did:key:...`) can contain real names, emails, and phone
numbers. Treat it the way you would any personal data surfaced from a
document: fine to reference if the user is asking about who's behind a
signature, but don't gratuitously repeat it back or feature it in a summary
they didn't ask for.

## Recognizing what kind of run this is

Most manifests you'll see come from LangChain/LangGraph-instrumented agents
(look for `metadata` blobs containing `ls_integration`, `langgraph_node`,
`langgraph_step`, or asset descriptions like "LangGraph state entering
'agent'"). In that case:

- Each LangGraph **node** (e.g. `agent`, `tools`, `summarize`) shows up as
  its own `ComputationRegistration`, tagged via metadata with
  `computation_type: graph_node` and a `name`.
- Individual **tool calls** show up as `computation_type: tool` steps, with
  the tool's arguments and result as separate `DataRegistration`s.
- The underlying **LLM call** shows up as `computation_type: chat_model`,
  with the model name/provider in its metadata and the full message list as
  its input.
- The **first** `ComputationRegistration` by timestamp is usually the
  initial user question entering the graph; the **last** is usually the
  final answer/summary node.

Not every manifest will be a LangGraph run — the schema also supports
generic computations and (unused in most files you'll see) hardware
attestation evidence for confidential computing (look for `EqtyVCompAmdSevV1`,
`EqtyVCompIntelTdxV0`, `EqtyVCompAzureV1`, `EqtyVCompNvidiaCcV0` context
terms — these describe TEE/enclave measurement reports, not LLM behavior).
If you see these, the manifest is also asserting the computation ran inside
a specific attested secure enclave; only dig into the certificate chains and
measurement fields if the user specifically asks about attestation.

**Another common pattern: a data-processing pipeline over iroh file
collections.** Here the manifest is much smaller (often a single
`ComputationRegistration`), the `input`/`output` are iroh collection CIDs
(named file listings, not message histories), and an optional `computation`
field points to the script/tool that was run (its own filename collection,
e.g. `CollectionV0.` + `compute.py`, plus a separate blob holding the
script's actual source). `MetadataRegistration`s here typically describe
the input/output *datasets* (`assetType: "Dataset"`/`"Database"`,
`description`, `contactInformation`) rather than graph nodes — read them for
what the pipeline's inputs/outputs *mean*, since the raw collections only
give you filenames. Look at the `namespace` field inside a `computation`
step's metadata (e.g. `"simple-iroh-collections"`) as a hint at the
producing project.

In general: **infer the domain from the metadata and field shapes present,
don't assume every manifest is an LLM agent trace.** The statement/blob
mechanics are the same either way; only the semantic labels differ.
