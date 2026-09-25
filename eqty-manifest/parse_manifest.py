#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["eqty-sdk>=2.4.1", "cryptography", "base58"]
# ///
"""
parse_manifest.py — Decode and reconstruct an EQTY Lab lineage manifest.

An EQTY manifest is a JSON-LD document with two top-level payloads:
  - "statements": a flat dict of {statement_id: signed statement}, each
    statement being one of a fixed set of @type's (see SKILL.md).
  - "blobs": a flat dict of {content_id (no "urn:cid:" prefix): base64 string}.
    Decoding a blob gives you the actual payload that a DataRegistration or
    ComputationRegistration input/output/computation refers to.

Blob content comes in several shapes (JSON, plain text, opaque binary,
invalid/corrupt base64) — see decode_blob(). One binary shape deserves
special handling: an **iroh HashSeq**. Iroh collections are actually TWO
blobs: a small "CollectionV0." text blob listing filenames, and a separate
binary blob that is just N concatenated 32-byte BLAKE3 digests — one per
line of the manifest's blob keys (which are themselves multibase/CIDv1-
encoded digests). By convention, chunk 0 of the HashSeq is the digest of
the CollectionV0 names blob itself, and chunks 1..N are the digests of the
actual per-file content blobs, in the same order as the names.

**This means a blob can be a real, load-bearing part of the run without
ever being mentioned in any statement's `data`/`input`/`output`/
`computation`/`metadata` field — it can be reachable only by decoding
another blob's raw bytes as a HashSeq and matching 32-byte chunks against
other blobs' own digests.** Treating "not referenced by any statement
field" as "orphaned"/"unused" is a real mistake — resolve HashSeqs first
(see Manifest.resolve()) and only call something orphaned if it's
unreachable *after* that expansion.

This script makes NO assumptions about which fields are single CIDs vs.
lists of CIDs, or about which agent framework produced the manifest — it
only relies on the generic EQTY statement shapes and the general iroh
blob/collection conventions above.

Usage:
    uv run parse_manifest.py <manifest.json> summary
    uv run parse_manifest.py <manifest.json> timeline
    uv run parse_manifest.py <manifest.json> orphans        # blobs unreachable even after HashSeq expansion
    uv run parse_manifest.py <manifest.json> attestation   # where each step ran
    uv run parse_manifest.py <manifest.json> show <cid-or-statement-id-or-did>
    uv run parse_manifest.py <manifest.json> search <keyword>
    uv run parse_manifest.py <manifest.json> export <out.json>   # full resolved dump

If no subcommand is given, "summary" then "timeline" are printed.
"""
import sys
import json
import base64
import binascii
import atexit
import shutil
import tempfile
from collections import defaultdict, Counter

# The SDK is OPTIONAL on purpose: everything else in this file is stdlib-only,
# so reading a manifest never requires an install. It is needed for one thing
# — checking that a blob's bytes actually hash to the CID that names it, using
# the same `get_cid_for_bytes` that produced the CID. When it's absent we say
# so explicitly (see `content_verification` in summary()); we never report
# "no problems" for a check we couldn't run.
_hasher = None


def _cid_hasher():
    """`raw bytes -> digest` via eqty_sdk, or None when the SDK is missing.

    `get_cid_for_bytes` panics until `init()` has run, and `init()` creates a
    store directory. Point it at a throwaway temp dir so verifying a manifest
    writes nothing beside it. Compares digests, not CID strings: a blob key's
    codec can differ from the raw codec the SDK stamps on its answer."""
    global _hasher
    if _hasher is None:
        try:
            import eqty_sdk
        except ImportError:
            return None
        store = tempfile.mkdtemp(prefix="eqty-manifest-")
        atexit.register(shutil.rmtree, store, ignore_errors=True)
        try:
            eqty_sdk.init(custom_dir=store)
        except Exception:  # noqa: BLE001 - already initialised by the host process
            pass
        _hasher = lambda raw: cid_digest(strip_urn(eqty_sdk.get_cid_for_bytes(raw, False).cid))
    return _hasher

COLLECTION_MAGIC = "CollectionV0."
DIGEST_LEN = 32  # BLAKE3 digest size used by iroh


def _collect_cids(obj, into):
    """Recursively gather every `urn:cid:`-prefixed string inside an
    arbitrary nested structure. Used for places where CIDs are embedded in
    free-form sub-documents (credential evidence, vcomp descriptors) rather
    than in a known top-level field."""
    if isinstance(obj, str):
        if obj.startswith("urn:cid:"):
            into.add(strip_urn(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_cids(v, into)
    elif isinstance(obj, list):
        for v in obj:
            _collect_cids(v, into)


def strip_urn(cid: str) -> str:
    return cid.replace("urn:cid:", "") if isinstance(cid, str) else cid


def as_list(value):
    """Normalize a field that may be a single CID (str) or a list of CIDs
    (some manifests use one convention, some the other) into a list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def load(path):
    with open(path) as f:
        return json.load(f)


def read_varint(buf: bytes, i: int):
    result, shift = 0, 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, i


def cid_digest(key: str):
    """Best-effort extraction of the raw hash digest bytes from a
    multibase-base32 CIDv1 blob key (e.g. 'bafkr4i...', 'baga6yaq...').
    These keys are multibase 'b' (base32, lowercase, no padding) encodings
    of: cid-version varint, codec varint, multihash-code varint,
    multihash-length varint, then the digest bytes. Returns None if the
    key doesn't decode as expected (e.g. it isn't multibase-base32 CID —
    a did:key or statement id passed in here should just fail gracefully)."""
    try:
        if not key or key[0] != "b":
            return None
        body = key[1:].upper()
        body += "=" * ((-len(body)) % 8)
        raw = base64.b32decode(body)
        i = 0
        _version, i = read_varint(raw, i)
        _codec, i = read_varint(raw, i)
        _mh_code, i = read_varint(raw, i)
        mh_len, i = read_varint(raw, i)
        return raw[i : i + mh_len]
    except Exception:
        return None


def try_parse_iroh_collection(raw: bytes):
    """Iroh's simple CollectionV0 text format is: the literal prefix
    "CollectionV0.", a count byte, then for each entry a length byte
    followed by that many bytes of filename. Returns a list of filenames,
    or None if this isn't that format."""
    if not raw.startswith(COLLECTION_MAGIC.encode()):
        return None
    try:
        rest = raw[len(COLLECTION_MAGIC):]
        count = rest[0]
        pos = 1
        names = []
        for _ in range(count):
            n = rest[pos]
            pos += 1
            names.append(rest[pos : pos + n].decode("utf-8", errors="replace"))
            pos += n
        return names
    except Exception:
        return None


def decode_blob(b64: str):
    """Decode one base64 blob value into a structured description.

    Returns a dict with one of these shapes:
      {"kind": "invalid_base64", "error": "..."}
      {"kind": "json", "value": <parsed JSON>}
      {"kind": "text", "value": "<utf-8 string>"}
      {"kind": "iroh_collection", "files": ["a.txt", "b.txt", ...]}
      {"kind": "binary", "length": <int>, "hex_preview": "<first 40 bytes as hex>"}

    This is the FLAT, single-blob decode — it does not attempt to resolve
    a binary blob as an iroh HashSeq pointing at other blobs. For that, use
    Manifest.resolve(), which has access to the whole blob map and can
    match 32-byte chunks against sibling blobs' own digests.

    Never silently mangles binary data into replacement-character text,
    and never crashes on malformed/corrupted base64 (a real possibility —
    truncation or tampering — that should be surfaced, not raised).
    """
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError) as e:
        return {"kind": "invalid_base64", "error": str(e)}

    collection = try_parse_iroh_collection(raw)
    if collection is not None:
        return {"kind": "iroh_collection", "files": collection}

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"kind": "binary", "length": len(raw), "hex_preview": raw[:40].hex()}

    try:
        return {"kind": "json", "value": json.loads(text)}
    except (json.JSONDecodeError, ValueError):
        return {"kind": "text", "value": text}


def describe(decoded: dict, n=200) -> str:
    """One-line human-readable summary of any resolve()/decode_blob() result."""
    kind = decoded["kind"]
    if kind == "invalid_base64":
        return f"[INVALID BASE64 — possibly corrupt or tampered: {decoded['error']}]"
    if kind == "binary":
        return f"[binary blob, {decoded['length']} bytes, starts {decoded['hex_preview']}...]"
    if kind == "iroh_collection":
        return f"[iroh file collection (names only): {', '.join(decoded['files'])}]"
    if kind == "iroh_collection_resolved":
        parts = [f"{f['name']}={describe(f['content'], 60)}" for f in decoded["files"]]
        return f"[iroh collection: {', '.join(parts)}]"
    if kind == "iroh_hashseq":
        return f"[iroh hash-sequence, {len(decoded['entries'])} entries, unnamed]"
    if kind == "json":
        s = json.dumps(decoded["value"])
    else:
        s = decoded["value"]
    return s if len(s) <= n else s[: n - 3] + "..."


class Manifest:
    def __init__(self, data):
        self.data = data
        self.statements = data.get("statements", {})
        self.blobs = data.get("blobs", {})

        self.by_type = defaultdict(dict)
        for sid, s in self.statements.items():
            self.by_type[s.get("@type", "Unknown")][sid] = s

        self.data_cid_to_stmt = {}
        for sid, s in self.by_type.get("DataRegistration", {}).items():
            self.data_cid_to_stmt[s["data"]] = sid

        # subject -> metadata content CID.
        # NOTE: subject is NOT always a data CID. It can be:
        #   - a data CID              (metadata *about a piece of content*)
        #   - a statement id           (metadata *about a computation step*)
        #   - a did:key:...            (metadata *about an actor/entity* —
        #                                e.g. contact info for whoever signs)
        self.subject_to_meta_cid = {}
        for sid, s in self.by_type.get("MetadataRegistration", {}).items():
            self.subject_to_meta_cid[s["subject"]] = s["metadata"]

        self.credential_by_subject = {}
        for sid, s in self.by_type.get("CredentialRegistration", {}).items():
            try:
                subj = s["credential"]["credentialSubject"]["id"]
                self.credential_by_subject[subj] = sid
            except (KeyError, TypeError):
                pass

        # did -> its execution-environment attestation (`vcomp`), declared by
        # DidRegistration. A ComputationRegistration links to one of these via
        # `executedOn`, which is how a step is tied to the environment it ran
        # in (Docker image, AMD SEV / Intel TDX / Azure / NVIDIA CC enclave).
        # There are TWO ways a manifest attests an environment, and a report
        # that knows only one will silently under-report attestation:
        #   (a) DidRegistration.vcomp                — gnn-train, multiple-attestation
        #   (b) an identity CredentialRegistration whose credentialSubject IS
        #       the did, carrying `identity` + `evidence`  — model-dev-inf
        # Both are indexed here, tagged with `source` so callers can tell
        # which kind of claim they are looking at.
        self.env_by_did = {}
        for sid, st in self.by_type.get("DidRegistration", {}).items():
            did = st.get("did") or st.get("subject")
            if did and "vcomp" in st:
                self.env_by_did[did] = {"statement_id": sid, "source": "DidRegistration.vcomp",
                                        "vcomp": st["vcomp"]}
        for sid, st in self.by_type.get("CredentialRegistration", {}).items():
            cred = st.get("credential")
            if not isinstance(cred, dict):
                continue
            subj = cred.get("credentialSubject")
            if not isinstance(subj, dict):
                continue
            did = subj.get("id")
            if not did or not str(did).startswith("did:"):
                continue
            identity = subj.get("identity")
            # need at least one of: an identity block, or evidence
            if "evidence" not in cred and not isinstance(identity, dict):
                continue
            # don't let a weaker credential claim overwrite an explicit vcomp
            if did in self.env_by_did:
                continue
            vcomp = dict(identity) if isinstance(identity, dict) else {}
            if "type" in vcomp and "@type" not in vcomp:
                vcomp["@type"] = vcomp["type"]
            if "evidence" in cred:
                vcomp["evidence"] = cred["evidence"]
            if vcomp:
                self.env_by_did[did] = {"statement_id": sid,
                                        "source": "CredentialRegistration.evidence",
                                        "vcomp": vcomp}

        # digest (raw bytes, decoded from the CID key itself) -> blob key.
        # This is what lets us match a 32-byte chunk inside one blob's raw
        # bytes against another blob's own identity, independent of
        # anything in `statements`.
        self.digest_to_key = {}
        for key in self.blobs:
            d = cid_digest(key)
            if d is not None:
                self.digest_to_key[d] = key

    # ---- raw bytes / flat decoding --------------------------------------
    def raw_bytes(self, key: str):
        """Raw decoded bytes for a blob key (no urn:cid: stripping — pass
        the bare key as stored in `blobs`), or None if missing/invalid."""
        b64 = self.blobs.get(key)
        if b64 is None:
            return None
        try:
            return base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError):
            return None

    def decoded(self, cid: str):
        """Flat (non-recursive) decode — does not resolve HashSeqs. Prefer
        resolve() for anything user-facing."""
        c = strip_urn(cid)
        b64 = self.blobs.get(c)
        if b64 is None:
            return None
        return decode_blob(b64)

    # ---- HashSeq / collection resolution --------------------------------
    def try_resolve_hashseq(self, key: str):
        """If the blob at `key` is exactly a concatenation of 32-byte
        chunks that ALL match some other blob's own digest, return the
        ordered list of matched keys. Otherwise return None. Requires
        every chunk to resolve — a partial match is far more likely to be
        coincidental opaque binary than a real (partially corrupt)
        HashSeq, since digest collisions are effectively impossible."""
        raw = self.raw_bytes(key)
        if not raw or len(raw) == 0 or len(raw) % DIGEST_LEN != 0:
            return None
        chunks = [raw[i : i + DIGEST_LEN] for i in range(0, len(raw), DIGEST_LEN)]
        keys = []
        for c in chunks:
            k = self.digest_to_key.get(c)
            if k is None:
                return None
            keys.append(k)
        return keys

    def resolve(self, cid: str, _depth=0):
        """The main entry point for reading a blob's content. Like
        decoded(), but if the blob turns out to be an iroh HashSeq, this
        follows it: resolves each referenced blob in turn (recursively,
        depth-limited), and if the first entry is itself a CollectionV0
        names listing whose file count matches the remaining entries,
        zips names to contents and returns a fully-resolved,
        human-readable collection instead of raw binary.

        Returns one of decode_blob()'s shapes, plus two additional kinds:
          {"kind": "iroh_collection_resolved",
           "meta_cid": "<key of the names blob>",
           "files": [{"name": ..., "cid": ..., "content": <resolve(...)>}]}
          {"kind": "iroh_hashseq",
           "entries": [{"cid": ..., "content": <resolve(...)>}]}
          (the latter is used when chunk 0 doesn't look like a names
          listing with a matching count — still fully resolved, just
          without inferred filenames)
        """
        key = strip_urn(cid)
        if key not in self.blobs:
            return None
        flat = decode_blob(self.blobs[key])
        if flat["kind"] != "binary" or _depth > 6:
            return flat

        keys = self.try_resolve_hashseq(key)
        if keys is None:
            return flat  # genuinely opaque binary, not a resolvable HashSeq

        first_flat = decode_blob(self.blobs[keys[0]])
        if first_flat["kind"] == "iroh_collection" and len(first_flat["files"]) == len(keys) - 1:
            files = [
                {"name": name, "cid": k, "content": self.resolve(k, _depth + 1)}
                for name, k in zip(first_flat["files"], keys[1:])
            ]
            return {"kind": "iroh_collection_resolved", "meta_cid": keys[0], "files": files}

        return {
            "kind": "iroh_hashseq",
            "entries": [{"cid": k, "content": self.resolve(k, _depth + 1)} for k in keys],
        }

    def content(self, cid: str):
        """Fully-resolved content for a CID: JSON/text/collection value, or
        a bracketed description for binary/invalid/hashseq blobs."""
        d = self.resolve(cid)
        if d is None:
            return None
        if d["kind"] == "json":
            return d["value"]
        if d["kind"] == "text":
            return d["value"]
        return describe(d)

    def content_preview(self, cid: str, n=200):
        d = self.resolve(cid)
        if d is None:
            return None
        return describe(d, n=n)

    # ---- metadata / lineage lookups ------------------------------------
    def metadata_for(self, subject: str):
        """Resolve human-readable metadata attached to any CID, statement
        id, or did:key (entity metadata)."""
        meta_cid = self.subject_to_meta_cid.get(subject)
        if meta_cid is None:
            return None
        return self.content(meta_cid)

    def entity_metadata(self):
        """All MetadataRegistrations whose subject is a DID rather than a
        CID/statement id — i.e. metadata describing an actor/signer rather
        than a piece of data. May contain PII (name/email/phone) about a
        human or organization behind a did:key; handle accordingly when
        surfacing to a user (don't repeat it back unless asked, don't
        assume it's public just because it's in the file)."""
        out = {}
        for subject, meta_cid in self.subject_to_meta_cid.items():
            if isinstance(subject, str) and subject.startswith("did:"):
                out[subject] = self.content(meta_cid)
        return out

    # The emitter's declaration that an asset is committed by CID only, its
    # bytes deliberately left out (weights, a large dataset). Written by
    # eqty-instrument as metadata on the asset itself: storage="by-reference",
    # plus storage_reason and, optionally, obtain_from.
    BY_REFERENCE = "by-reference"

    def by_reference_declaration(self, key: str):
        """The declaration for a CID that is referenced but not embedded, or
        None. It is the signer's claim, not a check: the asset's bytes still
        cannot be verified from this manifest. It only counts when the
        metadata blob carrying it is itself embedded and hashes to its CID, so
        an altered or absent declaration falls back to plain `missing`."""
        key = strip_urn(key)
        for s in self.by_type.get("MetadataRegistration", {}).values():
            if strip_urn(s.get("subject") or "") != key:
                continue
            meta_key = strip_urn(s.get("metadata") or "")
            if (self.blob_integrity(meta_key) or {}).get("status") != "verified":
                continue
            meta = self.content(meta_key)
            if isinstance(meta, dict) and meta.get("storage") == self.BY_REFERENCE:
                return {"name": meta.get("name"), "reason": meta.get("storage_reason"),
                        "obtain_from": meta.get("obtain_from")}
        return None

    def registration_info(self, cid: str):
        sid = self.data_cid_to_stmt.get(cid)
        if sid is None:
            return None
        s = self.statements[sid]
        return {"statement_id": sid, "registeredBy": s.get("registeredBy"), "timestamp": s.get("timestamp")}

    # ---- referenced vs. orphaned blobs ----------------------------------
    CONTENT_FIELDS = ("data", "input", "output", "computation", "metadata")

    def content_referenced_cids(self):
        """CIDs referenced by fields that are guaranteed to point at a blob
        (as opposed to `subject`/`credentialSubject.id`, which may instead
        point at a statement id or a did:key — not a blob at all). Use this
        set, not `referenced_cids()`, when checking for missing blobs."""
        referenced = set()
        for s in self.statements.values():
            for field in self.CONTENT_FIELDS:
                if field in s:
                    for v in as_list(s[field]):
                        if isinstance(v, str):
                            referenced.add(strip_urn(v))

            # A credential body can also point at blobs — notably hardware
            # attestation evidence (`evidence.certificateChain`,
            # `evidence.nvidiaCcReport`, TDX quotes, …) and `vcomp` fields
            # such as a Docker `compose` CID. These are real content
            # references, so a blob reachable only this way is NOT unused.
            # Missing them made certificate chains and attestation reports
            # show up as "orphaned", which is exactly backwards: they are
            # the evidence the manifest exists to carry.
            # Scope this narrowly to the evidence/vcomp subtrees. Walking a
            # whole credential would also sweep up `credentialSubject.id`
            # (a STATEMENT id) and `@context` CIDs, neither of which is a
            # blob — and every one of those would then be misreported as a
            # missing blob.
            cred = s.get("credential")
            if isinstance(cred, dict) and "evidence" in cred:
                _collect_cids(cred["evidence"], referenced)
            if isinstance(s.get("vcomp"), (dict, list)):
                _collect_cids(s["vcomp"], referenced)
        return referenced

    def referenced_cids(self):
        """Every identifier (stripped of the urn:cid: prefix) that some
        statement points to via ANY of its CID-bearing fields, including
        `subject` and `credentialSubject.id`. Broad by design."""
        referenced = self.content_referenced_cids()
        for s in self.statements.values():
            if "subject" in s and isinstance(s["subject"], str):
                referenced.add(strip_urn(s["subject"]))
            cred = s.get("credential")
            if isinstance(cred, dict):
                try:
                    referenced.add(strip_urn(cred["credentialSubject"]["id"]))
                except (KeyError, TypeError):
                    pass
        return referenced

    def reachable_blob_closure(self):
        """Every blob key reachable starting from `referenced_cids()` and
        expanding through HashSeq resolution (a blob referenced by a
        statement may itself be a HashSeq pointing at further blobs, which
        may themselves be HashSeqs, and so on). This — NOT
        `referenced_cids()` alone — is the correct set to use for deciding
        whether a blob is truly unused, because a blob can be fully
        load-bearing while being invisible to every statement field: it's
        only reachable by decoding another blob's raw bytes."""
        seen = set()
        frontier = set(k for k in self.referenced_cids() if k in self.blobs)
        seen |= frontier
        while frontier:
            next_frontier = set()
            for key in frontier:
                keys = self.try_resolve_hashseq(key)
                if keys:
                    for k in keys:
                        if k not in seen:
                            seen.add(k)
                            next_frontier.add(k)
            frontier = next_frontier
        return seen

    def orphaned_blobs(self):
        """Blobs that exist in the manifest's `blobs` map but are
        unreachable even after expanding through HashSeq resolution (see
        `reachable_blob_closure`). This is the correct notion of "unused
        content" — checking only direct statement references
        (`referenced_cids()`) produces false positives whenever a
        legitimately-used blob (a file's actual content, a script, a
        parameter) is only reachable via another blob's binary HashSeq
        rather than a JSON field."""
        reachable = self.reachable_blob_closure()
        out = []
        for cid_key in self.blobs:
            if cid_key not in reachable:
                d = decode_blob(self.blobs[cid_key])
                out.append({"cid": cid_key, "decoded": d, "preview": describe(d)})
        return out

    # ---- high level views ----------------------------------------------
    def computations_by_time(self):
        comps = self.by_type.get("ComputationRegistration", {})
        return sorted(comps.items(), key=lambda kv: kv[1].get("timestamp", ""))

    def content_integrity_check(self):
        """Verify that each blob's bytes actually hash to the CID that names
        it. This is the defining property of a content-addressed manifest,
        and it is NOT covered by credential signature verification: the
        credentials sign statement ids, and statements reference blobs by
        CID, so an edited payload leaves every signature intact while the
        content silently no longer matches its address.

        Blob keys are multibase-base32 CIDv1 whose multihash code is 0x1e
        (BLAKE3-256) in every manifest observed. Returns a dict with the
        counts plus any mismatches; `status` is 'verified' when the check
        actually ran, 'unavailable' when eqty_sdk is missing. Callers must
        treat 'unavailable' as UNKNOWN, never as clean.

        `missing` lists CIDs a statement references but `blobs` does not
        hold, and `total` counts present + missing. Without it the counts
        only cover blobs that happen to be in the file: deleting a blob
        shrinks the denominator with it, so N of M would read as N/N.

        `by_reference` maps the absent CIDs the signer declared as committed
        by CID only (see `by_reference_declaration`) to that declaration.
        They are not in `missing` and not a gap in the record, but they are
        still unverifiable here, and `total` still counts them."""
        absent = sorted(c for c in self.content_referenced_cids() if c not in self.blobs)
        by_reference = {c: d for c in absent for d in [self.by_reference_declaration(c)] if d}
        missing = [c for c in absent if c not in by_reference]
        total = len(self.blobs) + len(absent)
        hasher = _cid_hasher()
        if hasher is None:
            return {"status": "unavailable", "matched": 0, "mismatched": [],
                    "unverifiable": len(self.blobs), "missing": missing,
                    "by_reference": by_reference, "total": total,
                    "detail": "eqty_sdk not installed; run the script with `uv run`, or: pip install -r requirements.txt"}
        matched, mismatched, unverifiable, invalid_base64 = 0, [], 0, []
        for key, b64 in self.blobs.items():
            expected = cid_digest(key)
            if expected is None:
                unverifiable += 1
                continue
            try:
                raw = base64.b64decode(b64, validate=True)
            except (binascii.Error, ValueError):
                # already reported as invalid_base64 by integrity_check()
                unverifiable += 1
                invalid_base64.append(key)
                continue
            if hasher(raw) == expected:
                matched += 1
            else:
                mismatched.append(key)
        return {"status": "verified", "matched": matched,
                "mismatched": mismatched, "unverifiable": unverifiable,
                "invalid_base64": invalid_base64, "missing": missing,
                "by_reference": by_reference, "total": total}

    def integrity_check(self):
        """Flag anything that looks structurally wrong: invalid base64,
        CIDs referenced by a statement but absent from `blobs` entirely.
        This is a cheap sanity pass, NOT cryptographic signature
        verification (see SKILL.md for that distinction). Note a blob can
        be `invalid_base64` and STILL be meaningfully "referenced" (e.g.
        as one entry inside an otherwise-valid HashSeq) — that's a strong
        signal of a corrupted specific file within an otherwise-intact
        run, worth calling out by name (which filename, if resolvable)
        rather than just listing a bare CID."""
        problems = []
        for cid_key in self.blobs:
            d = decode_blob(self.blobs[cid_key])
            if d["kind"] == "invalid_base64":
                problems.append({"cid": cid_key, "issue": "invalid_base64", "detail": d["error"]})

        referenced = self.content_referenced_cids()
        for cid_key in referenced:
            if cid_key not in self.blobs:
                declared = self.by_reference_declaration(cid_key)
                if declared:
                    problems.append({"cid": cid_key, "issue": "by_reference",
                                     "detail": "declared by the signer as committed by CID only (%s); "
                                               "its bytes are not embedded, so they cannot be checked here"
                                               % (declared.get("reason") or "no reason given")})
                else:
                    problems.append({"cid": cid_key, "issue": "missing_blob",
                                     "detail": "referenced by a statement but no blob present"})

        # Content-address verification. A mismatch here means the payload was
        # altered after the manifest was built — the single most important
        # tampering signal in the file, and invisible to signature checking.
        content = self.content_integrity_check()
        for cid_key in content["mismatched"]:
            problems.append({"cid": cid_key, "issue": "cid_mismatch",
                              "detail": "blob content does NOT hash to the CID naming it — "
                                        "this content was altered after registration; treat it as untrusted"})
        if content["status"] == "unavailable":
            problems.append({"cid": None, "issue": "content_verification_unavailable",
                              "detail": content["detail"]})
        return problems

    def summary(self):
        type_counts = Counter(s.get("@type", "Unknown") for s in self.statements.values())
        signers = Counter(s.get("registeredBy") for s in self.statements.values())
        times = sorted(s.get("timestamp", "") for s in self.statements.values() if s.get("timestamp"))
        orphans = self.orphaned_blobs()
        out = {
            "total_statements": len(self.statements),
            "total_blobs": len(self.blobs),
            "statement_type_counts": dict(type_counts),
            "signers": dict(signers),
            "time_range": [times[0], times[-1]] if times else None,
            "entity_metadata": self.entity_metadata(),
            "integrity_problems": self.integrity_check(),
            "content_verification": self.content_integrity_check(),
            "orphaned_blob_count": len(orphans),
            "orphaned_blobs": orphans,
        }
        return out

    def environment_for(self, statement_id: str):
        """Resolve the execution environment a computation ran in, by
        following `ComputationRegistration.executedOn` to the matching
        `DidRegistration.vcomp`.

        Returns None when the statement declares no `executedOn` at all —
        which is the important case to report honestly: it means the
        manifest asserts NOTHING about where that step ran. That is not the
        same as "ran somewhere untrusted", and it is very much not the same
        as attested. A manifest can be fully signed and still make zero
        claims about its execution environment."""
        st = self.statements.get(statement_id, {})
        did = st.get("executedOn")
        if not did:
            return None
        env = self.env_by_did.get(did)
        if env is None:
            return {"did": did, "environment_claimed": False, "type": None,
                    "evidence_verified": False, "evidence_verification": "no_environment_record",
                    "detail": "executedOn names a DID with no DidRegistration/vcomp and no "
                              "identity credential in this manifest"}
        vcomp = env["vcomp"] or {}
        return {
            "did": did,
            # NAMING IS DELIBERATE. `environment_claimed` means the manifest
            # ASSERTS this step ran in this environment. It does NOT mean the
            # attestation evidence (SEV measurement, TDX quote, NVIDIA CC
            # report) has been cryptographically checked.
            #
            # This traversal is pure JSON-LD graph following and verifies
            # nothing, so it reports `evidence_verified: False` — meaning
            # "not verified HERE", not "cannot be verified". The evidence
            # checks do exist: verify_attestation.py verifies TDX, SEV-SNP
            # and NVIDIA CC reports against pinned vendor roots, and
            # attestation_report() surfaces the real result under its own
            # `evidence_verification` key. Never present a claimed
            # environment to a user as a verified one.
            "environment_claimed": True,
            "evidence_verified": False,
            "evidence_verification": "not_checked_here",
            "evidence_verification_detail":
                "This field reports graph traversal only. For the real check run "
                "`verify_attestation.py <manifest>`, or read the "
                "`evidence_verification` key of `attestation_report()`. Even when "
                "evidence verifies, that establishes authentic vendor hardware and "
                "an intact report — not which code ran.",
            "type": vcomp.get("@type"),
            "source": env.get("source"),
            "statement_id": env["statement_id"],
            "vcomp": vcomp,
        }

    def _verify_evidence(self):
        """Run hardware-attestation evidence verification if available.

        Lives in verify_attestation.py because it needs `cryptography` and
        bundled vendor roots; imported lazily so reading a manifest stays
        stdlib-only. When it cannot run we report UNVERIFIED -- never that
        the evidence passed."""
        try:
            import verify_attestation as _VA
        except ImportError:
            return {"status": "unavailable",
                    "detail": "verify_attestation.py needs `cryptography`; "
                              "evidence is UNVERIFIED, not passed"}
        try:
            results = _VA.verify_manifest_attestations(self.data)
        except Exception as e:
            return {"status": "error", "detail": "%s: %s" % (type(e).__name__, e)}
        vals = [r.get("valid") for r in results.values()]
        return {"status": "run",
                "verified": sum(1 for v in vals if v is True),
                "failed": sum(1 for v in vals if v is False),
                "inconclusive": sum(1 for v in vals if v is None),
                "results": results}

    def attestation_report(self):
        """Per-step execution-environment summary — the data behind
        'which steps ran where, and is any step not hardware-attested'."""
        steps, attested, unattested = [], 0, 0
        for sid, st in self.computations_by_time():
            env = self.environment_for(sid)
            meta = self.metadata_for(sid) or {}
            name = meta.get("name") if isinstance(meta, dict) else None
            if env and env.get("environment_claimed"):
                attested += 1
            else:
                unattested += 1
            steps.append({
                "statement_id": sid,
                "timestamp": st.get("timestamp"),
                "name": name,
                "operatedBy": st.get("operatedBy"),
                "environment": env,
            })
        return {
            "total_steps": len(steps),
            "steps_with_claimed_environment": attested,
            "steps_without_environment_claim": unattested,
            "evidence_verification": self._verify_evidence(),
            "note": "Step counts reflect what the manifest CLAIMS about execution "
                    "environments \u2014 a claimed enclave is not a proven one. "
                    "`evidence_verification` reports whether the underlying TEE reports "
                    "were cryptographically checked against pinned vendor roots; even "
                    "when they verify, reference measurements are NOT checked, so it "
                    "does not establish what code ran.",
            "environments": {d: e["vcomp"].get("@type") for d, e in self.env_by_did.items()},
            "steps": steps,
        }

    def timeline(self):
        """Reconstruct the computation DAG in chronological order. Each
        ComputationRegistration may carry `input`/`output` as either a
        single CID or a list, and may optionally carry a `computation`
        CID identifying the tool/script/model that was run. All three are
        normalized here AND fully resolved through any HashSeq/collection
        indirection via content_preview()."""
        events = []
        for sid, s in self.computations_by_time():
            meta = self.metadata_for(sid)
            entry = {
                "statement_id": sid,
                "timestamp": s.get("timestamp"),
                "operatedBy": s.get("operatedBy"),
                "executedOn": s.get("executedOn"),
                "environment": self.environment_for(sid),
                "metadata": meta,
                "computation_definition": (
                    {"cid": s["computation"], "preview": self.content_preview(s["computation"])}
                    if "computation" in s
                    else None
                ),
                "inputs": [
                    {"cid": i, "preview": self.content_preview(i)} for i in as_list(s.get("input"))
                ],
                "outputs": [
                    {"cid": o, "preview": self.content_preview(o)} for o in as_list(s.get("output"))
                ],
            }
            events.append(entry)
        return events

    def search(self, keyword: str):
        """Full-text search across every blob's fully-resolved content
        (so it finds text hiding behind a HashSeq/collection too) and
        every raw statement."""
        keyword_low = keyword.lower()
        hits = []
        for cid in self.blobs:
            d = self.resolve(cid)
            text = self._searchable_text(d)
            if text and keyword_low in text.lower():
                idx = text.lower().find(keyword_low)
                start = max(0, idx - 60)
                hits.append({"location": "blob", "cid": cid, "snippet": text[start : idx + 60]})
        for sid, s in self.statements.items():
            blob_text = json.dumps(s)
            if keyword_low in blob_text.lower():
                idx = blob_text.lower().find(keyword_low)
                start = max(0, idx - 60)
                hits.append({"location": "statement", "cid": sid, "snippet": blob_text[start : idx + 60]})
        return hits

    def _searchable_text(self, decoded):
        if decoded is None:
            return None
        kind = decoded["kind"]
        if kind == "json":
            return json.dumps(decoded["value"])
        if kind == "text":
            return decoded["value"]
        if kind == "iroh_collection":
            return " ".join(decoded["files"])
        if kind == "iroh_collection_resolved":
            parts = [decoded["meta_cid"]]
            for f in decoded["files"]:
                parts.append(f["name"])
                t = self._searchable_text(f["content"])
                if t:
                    parts.append(t)
            return " ".join(parts)
        if kind == "iroh_hashseq":
            parts = []
            for e in decoded["entries"]:
                t = self._searchable_text(e["content"])
                if t:
                    parts.append(t)
            return " ".join(parts)
        return None

    def show(self, cid_or_id: str):
        result = {}
        if cid_or_id in self.statements:
            result["statement"] = self.statements[cid_or_id]
        d = self.resolve(cid_or_id)
        if d is not None:
            result["resolved_content"] = d
        reg = self.registration_info(cid_or_id)
        if reg:
            result["registration"] = reg
        meta = self.metadata_for(cid_or_id)
        if meta:
            result["metadata"] = meta
        if cid_or_id not in self.statements:
            integrity = self.blob_integrity(strip_urn(cid_or_id))
            if integrity:
                result["content_integrity"] = integrity
        return result

    def blob_integrity(self, key: str):
        """Does THIS blob hash to its CID? Returned beside `show`'s content so
        altered bytes are never presented without saying so. None when the key
        is not a blob this manifest carries or references (a DID, say).

        Covers this blob's own bytes only: content resolved through it (a
        collection's files) has its own CIDs and its own status."""
        if key not in self.blobs:
            if key in self.content_referenced_cids():
                declared = self.by_reference_declaration(key)
                if declared:
                    return {"status": "by_reference", "declaration": declared,
                            "detail": "declared by the signer as committed by CID only; not embedded, "
                                      "so its content cannot be checked without the original file"}
                return {"status": "missing",
                        "detail": "referenced by a statement but not embedded; its content cannot be checked"}
            return None
        raw = self.raw_bytes(key)
        if raw is None:
            return {"status": "invalid_base64",
                    "detail": "not valid base64, so it cannot be hashed; do not treat any decoding of it as content"}
        expected, hasher = cid_digest(key), _cid_hasher()
        if expected is None:
            return {"status": "not_checked", "detail": "the key is not a CID this check can decode"}
        if hasher is None:
            return {"status": "not_checked",
                    "detail": "eqty_sdk not installed; run the script with `uv run`, or: pip install -r requirements.txt"}
        if hasher(raw) == expected:
            return {"status": "verified", "detail": "the bytes hash to this CID"}
        return {"status": "tampered",
                "detail": "the bytes do NOT hash to this CID: the content above was altered after "
                          "registration; treat it as untrusted"}

    def export_full(self):
        out = {
            "summary": self.summary(),
            "timeline": self.timeline(),
            "entity_metadata": self.entity_metadata(),
            "orphaned_blobs": self.orphaned_blobs(),
            "statements": {},
        }
        for sid, s in self.statements.items():
            entry = dict(s)
            if s.get("@type") == "DataRegistration":
                entry["_resolved_content"] = self.resolve(s["data"])
            if s.get("@type") == "MetadataRegistration":
                entry["_resolved_metadata"] = self.resolve(s["metadata"])
            if s.get("@type") == "ComputationRegistration":
                if "computation" in s:
                    entry["_resolved_computation"] = self.resolve(s["computation"])
                entry["_resolved_inputs"] = [self.resolve(i) for i in as_list(s.get("input"))]
                entry["_resolved_outputs"] = [self.resolve(o) for o in as_list(s.get("output"))]
            meta = self.metadata_for(sid)
            if meta:
                entry["_metadata_about_this_statement"] = meta
            out["statements"][sid] = entry
        return out


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print(__doc__)
        sys.exit(0)
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    path = sys.argv[1]
    cmd = sys.argv[2] if len(sys.argv) > 2 else None
    arg = sys.argv[3] if len(sys.argv) > 3 else None

    m = Manifest(load(path))

    if cmd is None:
        print(json.dumps(m.summary(), indent=2, default=str))
        print()
        print(json.dumps(m.timeline(), indent=2, default=str))
    elif cmd == "summary":
        print(json.dumps(m.summary(), indent=2, default=str))
    elif cmd == "timeline":
        print(json.dumps(m.timeline(), indent=2, default=str))
    elif cmd == "orphans":
        print(json.dumps(m.orphaned_blobs(), indent=2, default=str))
    elif cmd == "attestation":
        print(json.dumps(m.attestation_report(), indent=2, default=str))
    elif cmd == "show" and arg:
        print(json.dumps(m.show(arg), indent=2, default=str))
    elif cmd == "search" and arg:
        print(json.dumps(m.search(arg), indent=2, default=str))
    elif cmd == "export" and arg:
        with open(arg, "w") as f:
            json.dump(m.export_full(), f, indent=2, default=str)
        print(f"Wrote full resolved dump to {arg}")
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
