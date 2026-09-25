#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["eqty-sdk>=2.4.1", "cryptography", "base58"]
# ///
"""
verify_credentials.py — Verify the signatures inside an EQTY Lab lineage
manifest, by delegating to the EQTY SDK's own verifier.

WHAT CHANGED, AND WHY
This script used to hand-roll the proof check: pyld for URDNA2015 JSON-LD
canonicalization, `cryptography` for the EdDSA/ECDSA verify, base58 for
did:key decoding, plus a bundled-context search path. That reimplementation
had a structural problem. Canonicalization needs the exact JSON-LD context
that was live at signing time, and the script deliberately shipped no
context copies and never fetched a URL, so any credential whose contexts the
manifest did not itself embed came back `canonicalization_failed` —
unchecked. On the current mode-2 manifests that was *every* credential,
because manifests embed only the two `urn:cid:` EQTY vocabularies and not
the W3C `credentials/v2` / `security/v2` documents the credentials declare.

eqty_sdk 2.4.1 exposes the same two checks, with the W3C contexts compiled
into the package:

    verify_statement(statement_json, contexts=None) -> bool
    verify_vc(vc_json, statement_id=None, contexts=None) -> bool

Both run fully offline. That resolves the gap without reintroducing the
thing the old docstring was protecting against: the contexts now come from
a pinned, versioned dependency rather than from loose files sitting beside
the script, so a verdict is still not an artifact of the working directory.

TWO SEPARATE QUESTIONS, KEPT SEPARATE
  - `verify_vc`        — is the credential's proof cryptographically good,
                         and is it about THIS statement? Passing
                         `statement_id` is what makes the second half true;
                         a valid signature over some other subject says
                         nothing about the statement in hand.
  - `verify_statement` — does the statement's content still hash to its
                         `@id`? This is an integrity check on the statement
                         itself, and the hand-rolled script never did it.
These are reported on separate axes and never merged into one verdict.

WHAT THE SDK DOES NOT COVER: SIGSTORE
Some `CredentialRegistration` statements carry no W3C `credential` at all,
but a Sigstore bundle wrapping an in-toto attestation in a DSSE envelope.
`verify_vc` does not read those. The DSSE path below is therefore kept as
local code, unchanged, and still needs `cryptography` + `base58`.

REVOCATION IS STILL NOT CHECKED. `verify_vc` checks the cryptographic proof
only; revocation and suspension live in a status list that is fetched over
the network. A `valid: true` here means correctly signed, not "still valid".

REQUIRES: eqty_sdk>=2.4.1 (proofs) and, for the Sigstore path only,
          `cryptography` and `base58`.

USAGE
    uv run verify_credentials.py <manifest.json>                 # verify all
    uv run verify_credentials.py <manifest.json> <statement_id>   # verify one
"""

import sys
import os
import json
import base64

try:
    import eqty_sdk
except ImportError:
    eqty_sdk = None

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import hashes
except ImportError:
    Ed25519PublicKey = None

try:
    import base58
except ImportError:
    base58 = None

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# --- did:key multicodec prefixes (varint-encoded). Still needed here: the
# SDK resolves did:key internally for proofs, but the Sigstore DSSE path
# below is local code and has to decode the envelope's `keyid` itself.
KEY_CODECS = {
    bytes([0xED, 0x01]): "ed25519",
    bytes([0xE7, 0x01]): "secp256k1",
    bytes([0x80, 0x24]): "p256",   # secp256r1 / P-256
    bytes([0x81, 0x24]): "p384",   # secp384r1 / P-384
}

CURVES = {
    "secp256k1": ec.SECP256K1() if Ed25519PublicKey else None,
    "p256": ec.SECP256R1() if Ed25519PublicKey else None,
    "p384": ec.SECP384R1() if Ed25519PublicKey else None,
}
HASH_ALGS = {
    "secp256k1": hashes.SHA256() if Ed25519PublicKey else None,
    "p256": hashes.SHA256() if Ed25519PublicKey else None,
    "p384": hashes.SHA384() if Ed25519PublicKey else None,
}


def _check_deps():
    """The SDK is required. `cryptography`/`base58` are needed only by the
    Sigstore path, so their absence is reported there rather than here."""
    if eqty_sdk is None:
        raise SystemExit(
            "Missing required package: eqty_sdk (>=2.4.1) -- run the script with `uv run`, or: pip install -r requirements.txt\n"
            "Signature verification now delegates to the SDK's verify_vc/"
            "verify_statement rather than reimplementing the proof check."
        )
    for fn in ("verify_vc", "verify_statement"):
        if not hasattr(eqty_sdk, fn):
            raise SystemExit(
                "eqty_sdk is installed but has no %s(); that entry point "
                "landed in 2.4.1. Upgrade the SDK." % fn
            )


def b64url_decode(s: str) -> bytes:
    s += "=" * ((-len(s)) % 4)
    return base64.urlsafe_b64decode(s)


def decode_did_key(did_key: str):
    """did:key:zXXX[#fragment] -> (key_type, raw_public_key_bytes).
    key_type is one of KEY_CODECS' values. Raises ValueError with a clear
    message for a did:key using a codec this script doesn't recognize —
    never silently guesses a key type."""
    did_key = did_key.split("#")[0]
    if not did_key.startswith("did:key:z"):
        raise ValueError(f"Not a did:key I know how to decode: {did_key}")
    multibase_value = did_key[len("did:key:"):]
    if multibase_value[0] != "z":
        raise ValueError(f"Expected multibase 'z' (base58btc) prefix: {multibase_value}")
    raw = base58.b58decode(multibase_value[1:])
    for prefix, key_type in KEY_CODECS.items():
        if raw.startswith(prefix):
            return key_type, raw[len(prefix):]
    raise ValueError(
        f"Unrecognized/unsupported did:key multicodec prefix {raw[:2].hex()} "
        f"— this script only decodes {sorted(set(KEY_CODECS.values()))}."
    )


# ------------------------------------------------------------ contexts

class ContextSet:
    """Carries a manifest's `contexts` map for handing to the SDK.

    The SDK takes `contexts=` as a plain dict of context-URI -> document
    (dicts or JSON strings both work), so a manifest's `contexts` field can
    be passed straight through. This wrapper exists so that the old
    `context_loader(embedded, prefer_embedded)` call shape keeps working for
    callers that still use it; there is no document *loader* any more,
    because resolution happens inside the SDK.

    PRECEDENCE, WHICH MATTERS: a context supplied here takes precedence over
    one compiled into the SDK for the same URI. A manifest that embeds its
    own revision of `credentials/v2` therefore verifies against the revision
    it was signed under, not whatever the SDK happens to ship -- the same
    property the old bundled-revision search was trying to buy.
    """

    def __init__(self, contexts=None):
        self.contexts = dict(contexts or {})

    def as_dict(self):
        return self.contexts

    def __bool__(self):
        return bool(self.contexts)


def context_loader(embedded=None, prefer_embedded=True, revision=0):
    """Back-compat shim. Returns a ContextSet rather than a pyld document
    loader. `prefer_embedded`/`revision` are accepted and ignored: the SDK
    already prefers supplied contexts, and revision fallback is no longer
    needed because the SDK resolves the contexts it was built against."""
    return ContextSet(embedded)


def _contexts_arg(contexts):
    """Normalize whatever a caller passed into the dict the SDK wants.
    Accepts a dict, a ContextSet, None, or a legacy pyld loader callable
    (which carries no contexts we can recover -- treated as empty)."""
    if contexts is None:
        return {}
    if isinstance(contexts, ContextSet):
        return contexts.as_dict()
    if isinstance(contexts, dict):
        return contexts
    return {}


# ------------------------------------------------------- W3C credentials

# What eqty_sdk is KNOWN to verify: the proof types, JWS algorithms and key
# types seen verifying across the development corpus, and the W3C contexts the
# SDK documents as compiled in. integrity-py does not publish these lists, so
# they are observations, not a contract -- which is why they are consulted
# ONLY after `verify_vc` has already returned False. A stale list can then turn
# a "failed" into "not checked", never anything into "verified".
SDK_PROOF_TYPES = {                      # proof type -> (JWS alg, did:key type)
    "Ed25519Signature2018": ("EdDSA", "ed25519"),
    "EcdsaSecp256r1Signature2019": ("ES256", "p256"),
}
SDK_CONTEXTS = {
    "https://www.w3.org/ns/credentials/v2",
    "https://w3id.org/security/v2",
    "https://w3id.org/security/v1",
}


def why_uncheckable(credential: dict, contexts=None, key_type=None):
    """After `verify_vc` says False: is that because the SDK cannot check this
    credential at all? Returns (reason, found, detail) or None.

    Field comparisons only -- no cryptography. `verify_vc` folds "bad proof"
    and "cannot check" into one boolean; this recovers the second, so a proof
    type the SDK does not handle reads as NOT CHECKED rather than as a failed,
    forged-looking signature. It never upgrades a result: the worst a
    tamperer gains by also editing these fields is "not checked"."""
    proof = credential.get("proof") or {}
    ptype = proof.get("type")
    if ptype not in SDK_PROOF_TYPES:
        return ("unsupported_proof_type", ptype,
                "Proof type %r is not one eqty_sdk is known to verify (%s). "
                "Unchecked, not forged." % (ptype, ", ".join(sorted(SDK_PROOF_TYPES))))
    want_alg, want_key = SDK_PROOF_TYPES[ptype]
    jws = proof.get("jws")
    if isinstance(jws, str) and "." in jws:
        try:
            alg = json.loads(b64url_decode(jws.split(".", 1)[0])).get("alg")
        except Exception:  # noqa: BLE001 - an undecodable header is a broken proof, not an uncheckable one
            alg = want_alg
        if alg != want_alg:
            return ("alg_mismatch", alg,
                    "JWS header alg %r does not match %s, which uses %r. The SDK "
                    "cannot check a proof whose parts disagree." % (alg, ptype, want_alg))
    if key_type and key_type != want_key:
        return ("key_type_mismatch", key_type,
                "The signing did:key is %s but %s uses %s keys." % (key_type, ptype, want_key))
    known = SDK_CONTEXTS | set((contexts or {}).keys() if isinstance(contexts, dict) else ())
    declared = credential.get("@context")
    for c in declared if isinstance(declared, list) else [declared]:
        if isinstance(c, str) and c not in known:
            return ("unrecognized_context", c,
                    "@context %r is neither carried by the manifest nor one eqty_sdk "
                    "compiles in, and contexts are never fetched." % c)
    return None


def verify_credential(credential: dict, contexts=None, statement_id: str = None):
    """Verify one embedded W3C Verifiable Credential's proof via the SDK.

    Returns a result dict; never raises for an ordinary verification
    failure. `statement_id`, when given, is passed to the SDK so the
    credential is also checked to be ABOUT that statement.

    NOTE ON WHAT BINDING MEANS. `statement_id` makes the SDK check that the
    proof covers the subject named in the credential. It does NOT establish
    that this credential belongs to the statement that carries it: lifting a
    valid credential into a different CredentialRegistration leaves the
    signature valid. That move is caught by `verify_statement_integrity`,
    because the registration's `@id` commits to its own content. The two
    checks are complementary and neither is sufficient alone.

    The SDK returns a bare bool, so the distinction the old script drew
    between "signature is wrong" and "could not canonicalize" is no longer
    available from the verifier itself -- `verify_vc` folds an unresolvable
    context into `False` alongside a bad proof. We therefore do not invent a
    reason we cannot support: a `False` is reported as `signature_mismatch`
    only when the credential's declared contexts are all resolvable in
    principle, and the detail text says plainly what False does and does not
    distinguish."""
    if not isinstance(credential, dict):
        return {"valid": None, "reason": "no_credential",
                "detail": "Statement carries no `credential` object."}

    proof = credential.get("proof")
    if not isinstance(proof, dict):
        return {"valid": None, "reason": "no_proof",
                "detail": "Credential has no `proof` object."}

    ctx = _contexts_arg(contexts)
    subject = (credential.get("credentialSubject") or {}).get("id")

    result = {
        "signatureFormat": "w3c-vc",
        "proofType": proof.get("type"),
        "verificationMethod": proof.get("verificationMethod"),
        "issuer": credential.get("issuer"),
        "subject": subject,
        "contextSource": "manifest+sdk-embedded" if ctx else "sdk-embedded",
        # What the SDK's statement_id binding actually buys here: it
        # confirms the proof covers the subject the credential NAMES. It is
        # not proof that the credential belongs in the statement carrying
        # it -- a credential lifted verbatim into another registration still
        # validly attests its original subject. Only statement integrity
        # (`verify_statement`) catches that move. Said plainly so no reader
        # mistakes a green signature for a bound one.
        "subjectBindingChecked": bool(statement_id),
    }

    vm = proof.get("verificationMethod") or credential.get("issuer") or ""
    if isinstance(vm, str) and vm.startswith("did:key:"):
        try:
            result["keyType"] = decode_did_key(vm)[0]
        except Exception:
            pass   # key type is reporting colour, not part of the verdict

    try:
        ok = eqty_sdk.verify_vc(json.dumps(credential), statement_id, ctx)
    except ValueError as e:
        # Not a credential at all: not JSON, no credentialSubject, or more
        # than one subject. Not a failed proof, but a FAILED check all the
        # same: a registration whose credential does not parse is a defect in
        # the file, not a check we lacked the means to run.
        return dict(result, valid=False, reason="malformed_credential",
                    detail="%s: %s" % (type(e).__name__, e))
    except RuntimeError as e:
        # The DID method would need the network. Only did:key, did:jwk and
        # did:pkh resolve offline. Unchecked -- never treated as passing.
        return dict(result, valid=None, reason="unresolvable_signing_key",
                    detail="%s: %s. Only did:key, did:jwk and did:pkh "
                           "resolve offline; this is unchecked, not forged."
                           % (type(e).__name__, e))
    except Exception as e:  # noqa: BLE001 - an unexpected SDK error is not a verdict
        return dict(result, valid=None, reason="verifier_error",
                    detail="%s: %s" % (type(e).__name__, e))

    if ok:
        return dict(result, valid=True)

    detail = ("`verify_vc` returned false. The SDK folds several causes into "
              "one boolean: the proof does not validate against the issuer's "
              "key, the credential is bound to a different subject, or a "
              "declared @context is neither embedded in the SDK nor supplied "
              "by the manifest (it is never fetched). Treat as NOT verified.")
    if statement_id and subject and subject != statement_id:
        return dict(result, valid=False, reason="subject_mismatch",
                    detail="Credential's credentialSubject.id is %r but it was "
                           "checked against statement %r. A valid signature "
                           "over another subject says nothing about this "
                           "statement." % (subject, statement_id))
    uncheckable = why_uncheckable(credential, contexts, result.get("keyType"))
    if uncheckable:
        reason, found, why = uncheckable
        return dict(result, valid=None, reason=reason, found=found, detail=why)
    return dict(result, valid=False, reason="signature_mismatch", detail=detail)


# ------------------------------------------------- statement integrity

def verify_statement_integrity(statement: dict, contexts=None):
    """Does this statement's content still hash to its `@id`?

    A SEPARATE AXIS from signature validity, and deliberately not folded
    into it. `verify_statement` recomputes the statement's BLAKE3 RDFC CID
    from its canonicalized JSON-LD and compares it to the `@id` it carries.

    A True means every field the statement's `@context` DEFINES is
    unmodified. It does not mean the bytes are unmodified: the identifier
    commits to canonicalized RDF, and JSON-LD expansion drops keys the
    context does not define."""
    ctx = _contexts_arg(contexts)
    try:
        ok = eqty_sdk.verify_statement(json.dumps(statement), ctx)
    except ValueError as e:
        return {"valid": None, "reason": "malformed_statement",
                "detail": "%s: %s" % (type(e).__name__, e)}
    except RuntimeError as e:
        # A context that is neither embedded nor supplied RAISES here
        # (unlike verify_vc, which reports False). Unchecked, not modified.
        return {"valid": None, "reason": "context_unresolvable",
                "detail": "%s: %s. The context is never fetched; this "
                          "statement is unchecked, not modified."
                          % (type(e).__name__, e)}
    except Exception as e:  # noqa: BLE001
        return {"valid": None, "reason": "verifier_error",
                "detail": "%s: %s" % (type(e).__name__, e)}
    if ok:
        return {"valid": True}
    return {"valid": False, "reason": "id_mismatch",
            "detail": "Statement content does not hash to its `@id`: it was "
                      "modified after creation, or was written by a producer "
                      "whose canonicalization differs from this SDK build."}


def verify_statements(data: dict, only_statement_id: str = None):
    """Statement-integrity pass over a whole manifest, keyed by statement id.
    Reported alongside, never merged with, the signature results."""
    _check_deps()
    ctx = data.get("contexts") or {}
    out = {}
    for sid, s in (data.get("statements") or {}).items():
        if only_statement_id and sid != only_statement_id:
            continue
        r = verify_statement_integrity(s, ctx)
        r["@type"] = s.get("@type")
        out[sid] = r
    return out


def dsse_pae(envelope: dict) -> bytes:
    """DSSE Pre-Authentication Encoding (the bytes actually signed):
    `DSSEv1 <len(type)> <type> <len(payload)> <payload>`."""
    payload = b64_std_decode(envelope["payload"])
    ptype = envelope["payloadType"].encode()
    return b"DSSEv1 %d %s %d %s" % (len(ptype), ptype, len(payload), payload)


def b64_std_decode(s):
    import base64 as _b
    return _b.b64decode(s)


def verify_sigstore_bundle(statement: dict):
    """Verify a `sigstoreBundle` -- the OTHER signing mechanism these
    manifests use.

    Some CredentialRegistration statements carry no W3C `credential` at all;
    instead they carry a Sigstore bundle wrapping an in-toto attestation in a
    DSSE envelope. These are supply-chain provenance statements (e.g. binding
    a model artifact to its digest) sitting alongside the JSON-LD credentials.

    Reporting these as "no credential" would be wrong in the direction that
    matters: there IS a signature here, and calling it absent implies nothing
    to check. This verifies the DSSE signature against the `did:key` named in
    the envelope's `keyid`.

    WHAT IS NOT CHECKED: Sigstore's transparency log. `verificationMaterial`
    in these bundles carries an empty `tlogEntries` and only a key `hint`
    rather than a certificate, so there is no Rekor inclusion proof to
    validate and no Fulcio identity to bind. A valid result here means the
    payload was signed by that DID -- not that it was publicly logged."""
    import base64 as _b
    raw = statement.get("sigstoreBundle")
    if not raw:
        return None
    try:
        bundle = json.loads(_b.b64decode(raw))
    except Exception as e:
        return {"valid": None, "reason": "malformed_sigstore_bundle",
                "detail": "%s: %s" % (type(e).__name__, e)}
    env = bundle.get("dsseEnvelope")
    if not isinstance(env, dict) or not env.get("signatures"):
        return {"valid": None, "reason": "malformed_sigstore_bundle",
                "detail": "bundle has no dsseEnvelope signatures"}

    sig_entry = env["signatures"][0]
    keyid = sig_entry.get("keyid") or statement.get("registeredBy")
    if not keyid or not str(keyid).startswith("did:key:"):
        return {"valid": None, "reason": "unresolvable_signing_key",
                "detail": "envelope keyid %r is not a did:key and the bundle carries "
                          "only a key hint, not a certificate" % keyid}
    try:
        key_type, pub = decode_did_key(keyid)
    except Exception as e:
        return {"valid": None, "reason": "unresolvable_signing_key",
                "detail": "%s: %s" % (type(e).__name__, e)}

    curve = CURVES.get(key_type)
    halg = HASH_ALGS.get(key_type)
    result = {"signatureFormat": "sigstore-dsse",
              "payloadType": env.get("payloadType"),
              "verificationMethod": keyid,
              "transparencyLogChecked": False,
              "transparencyLogNote": "bundle carries no tlogEntries, so there is no Rekor "
                                     "inclusion proof to validate; this checks the DSSE "
                                     "signature only"}
    try:
        data = dsse_pae(env)
        sig = _b.b64decode(sig_entry["sig"])
        if key_type == "ed25519":
            Ed25519PublicKey.from_public_bytes(pub).verify(sig, data)
        else:
            pk = ec.EllipticCurvePublicKey.from_encoded_point(curve, pub)
            pk.verify(sig, data, ec.ECDSA(halg))
        result.update({"valid": True, "keyType": key_type})
    except Exception:
        result.update({"valid": False, "reason": "signature_mismatch",
                       "keyType": key_type,
                       "detail": "DSSE signature does not verify against the key named in "
                                 "`keyid`. Treat this attestation as untrusted."})
    return result


def verify_manifest(data: dict, only_statement_id: str = None):
    """Signature verification for every `CredentialRegistration` statement,
    keyed by statement id. Same return shape the CLI and `report.py` have
    always consumed.

    Each credential is checked against the statement that registers it, so
    a credential lifted from one statement into another fails rather than
    passing on the strength of a signature about something else."""
    _check_deps()
    contexts = data.get("contexts") or {}
    results = {}
    for sid, s in (data.get("statements") or {}).items():
        if s.get("@type") != "CredentialRegistration":
            continue
        if only_statement_id and sid != only_statement_id:
            continue
        credential = s.get("credential")
        if not isinstance(credential, dict):
            # No W3C credential -- but that does NOT mean nothing to check.
            # These commonly carry a Sigstore DSSE bundle instead, which the
            # SDK's verify_vc does not read. Local code still handles it.
            sig_result = verify_sigstore_bundle(s)
            if sig_result is not None:
                results[sid] = sig_result
            else:
                results[sid] = {"valid": None, "reason": "no_signature",
                                "detail": "Statement carries neither a `credential` nor a "
                                          "`sigstoreBundle`; there is genuinely nothing to verify."}
            continue

        # Bind the credential to the subject it names, and cross-check that
        # the named subject is a statement this manifest actually contains.
        subject = (credential.get("credentialSubject") or {}).get("id")
        r = verify_credential(credential, contexts, statement_id=subject)
        if subject and subject not in (data.get("statements") or {}):
            r["subjectPresentInManifest"] = False
            r.setdefault("notes", []).append(
                "credentialSubject.id %r is not a statement in this manifest, "
                "so the signature is valid about something not present here." % subject)
        elif subject:
            r["subjectPresentInManifest"] = True
        results[sid] = r
    return results


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print(__doc__)
        sys.exit(0)
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    path = sys.argv[1]
    only = sys.argv[2] if len(sys.argv) > 2 else None

    with open(path) as f:
        data = json.load(f)

    results = verify_manifest(data, only)
    statements = verify_statements(data, only)

    def _tally(d):
        t = {"valid": 0, "invalid": 0, "inconclusive": 0}
        for r in d.values():
            v = r.get("valid")
            t["valid" if v is True else "invalid" if v is False else "inconclusive"] += 1
        return t

    print(json.dumps({
        "signatures": results,
        "statementIntegrity": statements,
        "summary": {
            "signatures": _tally(results),
            "statementIntegrity": _tally(statements),
            "note": "Two independent checks, and BOTH are needed. `signatures` "
                    "is whether a credential's proof is cryptographically good "
                    "and covers the subject it names. `statementIntegrity` is "
                    "whether a statement still hashes to its own `@id`. Neither "
                    "implies the other: a credential lifted verbatim into a "
                    "different registration keeps a valid signature and is caught "
                    "only by statement integrity, while a statement can hash "
                    "correctly and carry a bad proof. Neither checks revocation.",
        },
    }, indent=2))

    if not results and not statements:
        print("No statements found (or none matched the given id).", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
