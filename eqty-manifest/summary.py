#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["eqty-sdk>=2.4.1", "cryptography", "base58"]
# ///
"""summary.py — every verification check on a manifest, as one text block.

    uv run eqty-manifest/summary.py <manifest.json>          # first 10 per list
    uv run eqty-manifest/summary.py <manifest.json> --full   # every CID

Blocks, in order:

  Summary                 (1) verified / total per statement type, plus the
                              identity attestations and Sigstore bundles
                          (2) one line per statement that failed, was not
                              checked, or has no credential
  Signers                 (3) valid / total credentials per signing DID, and
                              whether each credential's issuer is its
                              registration's signer
  Hashes                  (4) content addresses: blobs that hash to their CID
  Execution environment   (5) hardware evidence per attestation (report
                              signature, vendor chain, key binding) and whether
                              every `executedOn` names a system that is present

Exit code: 0 everything verified, 1 anything failed or was tampered with,
2 nothing failed but something could not be checked or is missing (including
when a dependency is missing, so nothing could be checked at all). A hash the
signer declared as committed by reference (storage="by-reference" on its
signed, intact metadata) is not "missing": it is listed under Hashes with its
reason and does not by itself make the exit code 2.

Composes the existing checks; it re-implements none of them:
verify_credentials (eqty_sdk verify_vc / verify_statement), parse_manifest's
content check (eqty_sdk get_cid_for_bytes) and verify_attestation. The only
logic of its own is the joins between them and two field comparisons.

KEY BINDING: whether the hardware vouches for the DID the attestation claims.
Checked when the credential declares `userData.type: key`: the signed evidence
must carry the DID's public key (a TPM quote's extraData, an Intel TDX
REPORTDATA, an NVIDIA SPDM nonce). It counts only when that evidence itself
verified; a TPM quote must also be bound to a verified hardware report, which
then reads as bound too. Anything else is a KNOWN GAP, printed as "not checked"
and stated once below the evidence lines: without a key claim the commitment
rule lives in EQTY's vcomp code and integrity-py exposes no primitive for it.
"Not checked" does not affect the exit code; a failed key binding does.
"""
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import parse_manifest as P  # noqa: E402
import verify_credentials as V  # noqa: E402

LIMIT = 10
KEY_BINDING_GAP = ("Key binding (report key material -> claimed DID) is checked where the "
                   "evidence declares the DID's key (userData type \"key\"); where it reads not "
                   "checked, the commitment rule is defined in EQTY's vcomp code and integrity-py "
                   "does not expose it.")
SEVERITY = {"failed": 0, "unchecked": 1, "missing": 2}
# Evidence where no check could run at all, in words that cannot be mistaken
# for evidence that is present but could not be checked.
NOTHING_TO_CHECK = {"report_blob_missing": "evidence blob not in the manifest",
                    "report_blob_invalid": "evidence blob is not valid base64",
                    "no_report": "the evidence names no report",
                    "unsupported_evidence_type": "evidence type not supported by this skill"}


def nothing_to_check(i):
    """Why no check ran for a hardware item, or None when some did."""
    why = NOTHING_TO_CHECK.get(i.get("reason")) if i.get("nothing_checked") else None
    return why and why + (" (%s)" % i["cid"] if i.get("cid") else "")
DRIFT = "Registration does not re-hash"
DRIFT_NOTE = ("Tampering or canonicalization drift from an older emitter; "
              "the manifest alone cannot tell which.")


def _id(v):
    """A DID field may be a string or {'id': ...}; drop any #fragment."""
    if isinstance(v, dict):
        v = v.get("id")
    return v.split("#")[0] if isinstance(v, str) else None


def _tally(statuses):
    c = Counter(statuses)
    return {"verified": c["verified"], "failed": c["failed"],
            "unchecked": c["unchecked"], "missing": c["missing"], "total": len(statuses)}


def _credentials(data, sigs, ids):
    """One entry per CredentialRegistration: what it vouches for, who signed
    it, its signature's status, and its own statement's @id result."""
    S = data.get("statements") or {}
    out = {}
    for sid, r in sigs.items():
        st = S[sid]
        cred = st.get("credential") if isinstance(st.get("credential"), dict) else {}
        subj = (cred.get("credentialSubject") or {}) if cred else {}
        env = None
        if not cred and not st.get("sigstoreBundle"):   # nothing signed at all: reason no_signature
            kind, subject, label = "unsigned", None, "CredentialRegistration %s" % sid
            signer = _id(st.get("registeredBy"))
        elif r.get("signatureFormat") == "sigstore-dsse" or st.get("sigstoreBundle"):
            kind, subject = "sigstore", st.get("subject")
            label = "Sigstore attestation for %s" % subject
            signer = _id(r.get("verificationMethod")) or _id(st.get("registeredBy"))
        else:
            subject = subj.get("id")
            signer = _id(cred.get("issuer")) or _id(st.get("registeredBy"))
            if "IdentityAttestation" in (cred.get("type") or []):
                kind = "identity"
                itype = (subj.get("identity") or {}).get("type") or "unknown identity type"
                label = "IdentityAttestation %s (%s)" % (subject or "(no subject)", itype)
                env = "%s %s" % (itype, subject or "(no subject)")
            elif subject in S:
                kind, label = "statement", "%s %s" % (S[subject].get("@type"), subject)
            else:
                kind, label = "dangling", "a subject not in this manifest (%s)" % subject

        # Status is the SIGNATURE's. The registration's own @id is reported on
        # its own line: older emitters wrote registrations that no longer
        # re-hash while their credentials verify, so a mismatch there cannot
        # be told apart from tampering and must not be counted as a failure.
        # The join above reads the subject from the signed credential, so a
        # credential lifted into another registration still vouches only for
        # its true subject.
        valid, idv = r.get("valid"), ids.get(sid, {}).get("valid")
        status = {True: "verified", False: "failed", None: "unchecked"}[valid]
        out[sid] = {"kind": kind, "subject": subject, "label": label, "signer": signer,
                    "status": status, "reason": r.get("reason"), "detail": r.get("detail"), "env": env,
                    "found": r.get("found"),
                    "id_valid": idv, "sig_valid": valid,
                    "issuer": _id(cred.get("issuer")) if cred else None,
                    "registeredBy": _id(st.get("registeredBy"))}
    return out


def _credential_issues(c, sid):
    """The (2) lines for one credential: its signature, then its registration."""
    out = []
    reg = " (its registration %s also does not hash to its @id)" % sid if c["id_valid"] is False else ""
    if c["sig_valid"] is False and c["reason"] == "malformed_credential":
        out.append(("failed", "Malformed credential", "Malformed credential for %s%s" % (c["label"], reg)))
    elif c["sig_valid"] is False:
        out.append(("failed", "Signature failed", "Signature validation failed for %s%s" % (c["label"], reg)))
    elif c["sig_valid"] is None:
        why = "%s: %s" % (c["reason"], c["found"]) if c.get("found") else c["reason"]
        out.append(("unchecked", "Signature not checked", "Signature not checked for %s (%s)" % (
            c["label"], why)))
    if c["sig_valid"] is not False and c["id_valid"] is False:
        out.append(("unchecked", "Registration does not re-hash",
                    "Registration %s does not hash to its @id, for %s" % (sid, c["label"])))
    elif c["id_valid"] is None:
        out.append(("unchecked", "Registration not checked", "Registration integrity not checked for %s" % c["label"]))
    return out


def _hardware(data, creds):
    """Per-evidence hardware results, split into the three things a reader
    needs told apart: the report signature, the vendor chain, the key binding."""
    try:
        import verify_attestation as A
        results = A.verify_manifest_attestations(data)
    except SystemExit as e:  # cryptography missing
        return {"status": "unavailable", "detail": str(e).splitlines()[0], "items": []}
    S = data.get("statements") or {}

    def agg(checks):
        vals = [c["passed"] for c in checks]
        if not vals:
            return None, None
        if False in vals:
            return False, next(c["detail"] for c in checks if c["passed"] is False)
        if None in vals:
            return None, next(c["detail"] for c in checks if c["passed"] is None)
        return True, None

    items = []
    for sid, r in results.items():
        c = creds.get(sid, {})
        label = c.get("env") or c.get("label", sid)
        if c.get("kind") == "statement":   # older layout: evidence vouches for a DidRegistration
            reg = S.get(c["subject"], {})
            if reg.get("did"):
                label = "%s %s" % ((reg.get("vcomp") or {}).get("@type") or reg.get("@type"), reg["did"])
        checks = r.get("checks") or []
        chain = [x for x in checks if x["check"].endswith("_chain_consistent")
                 or x["check"].startswith("root_is_pinned")]
        # `bound_*`: claims and keys tied to other, already-verified evidence
        # (a TPM attestation key bound to a verified AMD report, say).
        bind = [x for x in checks if x["check"].startswith("bound_")]
        keyb = [x for x in checks if x["check"] == "key_bound_to_did"]
        sig = [x for x in checks if x not in chain and x not in bind and x not in keyb]
        sig_ok, sig_why = agg(sig)
        chain_ok, chain_why = agg(chain)
        bind_ok, bind_why = agg(bind)
        key_ok, key_why = agg(keyb)
        via_binding = bool(bind) and not chain and bind_ok is True
        if key_ok is True and not (sig_ok is True and (chain_ok is True or via_binding)):
            key_ok, key_why = None, "the evidence carrying the commitment did not itself verify"
        if not checks:                     # nothing could run: missing/invalid blob, no chain ...
            sig_why = chain_why = r.get("detail") or r.get("reason")
        items.append({"statement": sid, "label": label, "format": r.get("format"),
                      "signature": sig_ok, "signature_detail": sig_why,
                      "chain": chain_ok, "chain_detail": chain_why,
                      "has_binding": bool(bind), "binding": bind_ok, "binding_detail": bind_why,
                      # no vendor chain of its own, but its key is authenticated through
                      # a hardware report that verified against a pinned root
                      "chain_via_binding": via_binding,
                      "key_binding": key_ok, "key_binding_detail": key_why, "key_binding_via": None,
                      "did": ((S.get(sid, {}).get("credential") or {}).get("credentialSubject") or {}).get("id"),
                      "evidence": r.get("evidence_declared") or [], "reason": r.get("reason"),
                      "nothing_checked": not checks, "cid": r.get("cid")})
    # A TPM quote that binds the DID is itself bound to a verified hardware report
    # of the same identity, so that report vouches for the DID through it.
    # ponytail: matched by DID, not by the exact report the TPM bound through; one
    # AMD/TDX report per identity in every manifest seen -- key on the report if not.
    bound_dids = {i["did"] for i in items if i["key_binding"] is True and i["has_binding"]}
    for i in items:
        if (i["key_binding"] is None and i["did"] in bound_dids and i["signature"] is True
                and i["chain"] is True and set(i["evidence"]) & set(A.TPM_BINDING_REPORTS)):
            i["key_binding"], i["key_binding_via"] = True, "through the TPM quote"
    return {"status": "run", "items": items}


def _executed_on(data):
    """Every `executedOn` must name a DID this manifest gives an identity to:
    a DidRegistration, or the subject of an IdentityAttestation. A dangling
    link means the step claims to have run on a system nobody describes."""
    S = data.get("statements") or {}
    systems = set()
    links = []
    for sid, s in S.items():
        if s.get("@type") == "DidRegistration" and s.get("did"):
            systems.add(s["did"])
        cred = s.get("credential") if isinstance(s.get("credential"), dict) else None
        subj = (cred or {}).get("credentialSubject") or {}
        if cred and "IdentityAttestation" in (cred.get("type") or []):
            if subj.get("id"):
                systems.add(subj["id"])
            for did in P.as_list((subj.get("identity") or {}).get("executedOn")):
                links.append(("IdentityAttestation %s (%s)" % (
                    subj.get("id"), (subj.get("identity") or {}).get("type")), did))
        for did in P.as_list(s.get("executedOn")):
            links.append(("%s %s" % (s.get("@type"), sid), did))
    return [{"from": f, "did": _id(d), "present": _id(d) in systems} for f, d in links]


def verification_summary(data):
    """Compute every block. The only place the joins live, so the CLI, the
    report and any caller cannot disagree about what the numbers are."""
    S = data.get("statements") or {}
    sigs = V.verify_manifest(data)
    ids = V.verify_statements(data)
    creds = _credentials(data, sigs, ids)
    issues = []

    # (1) + (2): statements other than credentials, joined to what vouches for them.
    by_subject = defaultdict(list)
    for sid, c in creds.items():
        if c["kind"] == "statement":
            by_subject[c["subject"]].append(sid)
    per_type = defaultdict(list)
    for sid, s in S.items():
        t = s.get("@type")
        if t == "CredentialRegistration":
            continue
        mine = [creds[c] for c in by_subject.get(sid, [])]
        idv = ids.get(sid, {}).get("valid")
        states = [c["status"] for c in mine]
        if idv is False:
            issues.append(("failed", "Statement modified", "Statement does not hash to its @id: %s %s" % (t, sid)))
        if not mine:
            status = "failed" if idv is False else "missing"
            issues.append(("missing", "Credential missing", "Credential missing for %s %s" % (t, sid)))
        elif idv is False or "failed" in states:
            status = "failed"
        elif idv is None or "unchecked" in states:
            status = "unchecked"
        else:
            status = "verified"
        if idv is None:
            issues.append(("unchecked", "Statement not checked", "Statement integrity not checked for %s %s (%s)" % (
                t, sid, ids.get(sid, {}).get("reason"))))
        per_type[t].append(status)
    rows = sorted(((t, _tally(v)) for t, v in per_type.items()), key=lambda x: (-x[1]["total"], x[0]))

    for kind, name in (("identity", "IdentityAttestation"), ("sigstore", "Sigstore attestation")):
        mine = [c["status"] for c in creds.values() if c["kind"] == kind]
        if mine:
            rows.append((name, _tally(mine)))

    for sid, c in creds.items():
        issues += _credential_issues(c, sid)
        if c["kind"] == "dangling":
            issues.append(("missing", "Dangling credential", "Credential %s names a subject not in this manifest: %s"
                           % (sid, c["subject"])))

    # (3) signers, and the issuer / registration-signer agreement.
    signers = defaultdict(list)
    for c in creds.values():
        signers[c["signer"] or "(no signer named)"].append(c["status"])
    signer_rows = sorted(((d, _tally(v)) for d, v in signers.items()), key=lambda x: (-x[1]["total"], x[0]))
    w3c = [(sid, c) for sid, c in creds.items() if c["kind"] not in ("sigstore", "unsigned")]
    issuer_ok = 0
    for sid, c in w3c:
        if c["issuer"] and c["issuer"] == c["registeredBy"]:
            issuer_ok += 1
        else:
            issues.append(("failed", "Issuer mismatch", "Credential issuer %s is not its registration's signer %s, for %s"
                           % (c["issuer"], c["registeredBy"], c["label"])))

    # (4) hashes: presentation of the existing content check.
    content = P.Manifest(data).content_integrity_check()

    # (5) execution environment.
    hardware = _hardware(data, creds)
    for h in hardware["items"]:
        if nothing_to_check(h):
            issues.append(("unchecked", "Hardware evidence missing", "Nothing to check for %s: %s" % (h["label"], nothing_to_check(h))))
            continue
        if h["signature"] is False:
            issues.append(("failed", "Hardware signature failed", "Hardware report signature failed for %s: %s" % (h["label"], h["signature_detail"])))
        elif h["signature"] is None:
            issues.append(("unchecked", "Hardware signature not checked", "Hardware report signature not checked for %s: %s" % (h["label"], h["signature_detail"])))
        if h["chain"] is False:
            issues.append(("failed", "Vendor chain failed", "Vendor certificate chain failed for %s: %s" % (h["label"], h["chain_detail"])))
        if h["has_binding"] and h["binding"] is False:
            issues.append(("failed", "Hardware binding failed", "Hardware binding failed for %s: %s" % (h["label"], h["binding_detail"])))
        elif h["has_binding"] and h["binding"] is None:
            issues.append(("unchecked", "Hardware binding not checked", "Hardware binding not checked for %s: %s" % (h["label"], h["binding_detail"])))
        elif h["chain"] is None and h["signature"] is not None and not h["has_binding"]:
            issues.append(("unchecked", "Vendor chain not checked", "Vendor certificate chain not checked for %s: %s" % (h["label"], h["chain_detail"])))
        if h["key_binding"] is False:
            issues.append(("failed", "Key binding failed", "Key binding failed for %s: %s" % (h["label"], h["key_binding_detail"])))
    links = _executed_on(data)
    for l in links:
        if not l["present"]:
            issues.append(("failed", "Missing system", "%s executedOn names a system not in this manifest: %s" % (l["from"], l["did"])))

    issues.sort(key=lambda i: (SEVERITY[i[0]], i[1], i[2]))
    return {"rows": rows, "issues": issues, "signers": signer_rows,
            "issuer_agreement": {"agree": issuer_ok, "total": len(w3c)},
            "content": content, "hardware": hardware, "executed_on": links,
            "exit_code": _exit_code(issues, content, hardware)}


def _exit_code(issues, content, hardware):
    if any(i[0] == "failed" for i in issues) or content.get("mismatched") \
            or content.get("invalid_base64"):
        return 1
    if issues or content.get("missing") or content.get("status") != "verified" \
            or hardware["status"] != "run":
        return 2
    return 0


# ----------------------------------------------------------------- rendering

def _urn(cid):
    return cid if cid.startswith("urn:") else "urn:cid:" + cid


def _cap(lines, full, indent):
    shown = lines if full else lines[:LIMIT]
    out = [indent + l for l in shown]
    if len(lines) > len(shown):
        out.append("%s… and %d more (--full to list all)" % (indent, len(lines) - len(shown)))
    return out


def _unchecked(t):
    return (", %d not checked" % t["unchecked"]) if t["unchecked"] else ""


def _state(v):
    return {True: "verified", False: "FAILED", None: "not checked"}[v]


BY_REFERENCE_NOTE = ("declared by the signer as committed by CID only; "
                     "the bytes are not embedded, so they cannot be checked without the original file")


def _by_reference_lines(c, full):
    """Assets the emitter deliberately left out. Not missing, and not verified:
    one count line, the note, then each asset by name with its stated reason."""
    br = c.get("by_reference") or {}
    if not br:
        return []
    out = ["- %d hash%s by reference, %s" % (len(br), " is" if len(br) == 1 else "es are", BY_REFERENCE_NOTE)]
    items = []
    for cid, d in sorted(br.items()):
        item = "%s — %s" % (_urn(cid), d.get("name") or "unnamed")
        if d.get("reason"):
            item += " (%s)" % d["reason"]
        if d.get("obtain_from"):
            item += "; obtain from: %s" % d["obtain_from"]
        items.append(item)
    return out + _cap(items, full, "  - ")


def render_text(s, full=False):
    L = ["Summary", "======="]
    for name, t in s["rows"]:
        L.append("%d / %d %s verified%s" % (t["verified"], t["total"], name, _unchecked(t)))
    if s["issues"]:
        L.append("")
        by_kind = defaultdict(list)          # issues arrive sorted, so kinds stay in severity order
        for _sev, kind, text in s["issues"]:
            by_kind[kind].append(text)
        for kind, lines in by_kind.items():
            if kind == DRIFT and not full:
                # Uniform, and not a failure on its own: one count line, not one per CID.
                n = len(lines)
                L.append("* %d registration%s not hash to %s @id; %s (--full to list)" % (
                    n, " does" if n == 1 else "s do", "its" if n == 1 else "their",
                    "its credential verifies" if n == 1 else "each one's credential verifies"))
            elif kind == "Missing system" and not full:
                # One line per missing system, not one per statement pointing at it.
                by_did = defaultdict(Counter)
                for l in s["executed_on"]:
                    if not l["present"]:
                        by_did[l["did"]][l["from"].split(" ")[0]] += 1
                for did, types in by_did.items():
                    n = sum(types.values())
                    L.append("* %d statement%s name%s a system not in this manifest as executedOn: %s (%s; --full to list)" % (
                        n, "" if n == 1 else "s", "s" if n == 1 else "", did,
                        ", ".join("%d %s" % (c, t) for t, c in types.most_common())))
            else:
                L += _cap(lines, full, "* ")
            if kind == DRIFT:
                L.append("  " + DRIFT_NOTE)

    L += ["", "Signers:"]
    for did, t in s["signers"]:
        L.append("- %s signed %d / %d valid credentials%s" % (did, t["verified"], t["total"], _unchecked(t)))
    ia = s["issuer_agreement"]
    if ia["total"]:
        L.append("- %d / %d credentials: issuer is the registration's signer" % (ia["agree"], ia["total"]))

    c = s["content"]
    L += ["", "Hashes:"]
    if c.get("status") != "verified":
        L.append("- not checked — %s" % c.get("detail"))
        if c.get("missing"):
            L.append("- %d hash%s missing pre-image blobs" % (len(c["missing"]), " has" if len(c["missing"]) == 1 else "es have"))
        L += _by_reference_lines(c, full)
    else:
        attached = c["total"] - len(c["missing"]) - len(c.get("by_reference") or {})
        bad64 = c.get("invalid_base64") or []
        other = c["unverifiable"] - len(bad64)
        L += ["- Total of %d hashes" % c["total"],
              "- %d have pre-images attached" % attached,
              "- %d / %d verified" % (c["matched"], attached)]
        if c["mismatched"]:
            L.append("- Tampering detected in %d of %d" % (len(c["mismatched"]), attached))
            L += _cap([_urn(x) for x in c["mismatched"]], full, "  - ")
        else:
            L.append("- No tampering detected")
        if bad64:
            L.append("- %d pre-image%s not valid base64" % (len(bad64), " is" if len(bad64) == 1 else "s are"))
            L += _cap([_urn(x) for x in bad64], full, "  - ")
        if other:
            L.append("- %d not checked (CID is not BLAKE3)" % other)
        L += _by_reference_lines(c, full)
        if c["missing"]:
            L.append("- %d hash%s missing pre-image blobs" % (len(c["missing"]), " has" if len(c["missing"]) == 1 else "es have"))
            L += _cap([_urn(x) for x in c["missing"]], full, "  - ")
        else:
            L.append("- No missing pre-image blobs")

    h, links = s["hardware"], s["executed_on"]
    if h["items"] or links or h["status"] != "run":
        L += ["", "Execution environment:"]
        if links:
            L.append("- %d / %d executedOn links name a system present in this manifest"
                     % (sum(1 for l in links if l["present"]), len(links)))
        if h["status"] != "run":
            L.append("- hardware evidence not checked — %s" % h["detail"])
        for i in h["items"]:
            chain = ("not needed (its key is bound to a verified hardware report)"
                     if i["chain_via_binding"] else _state(i["chain"]))
            binding = " · hardware binding %s" % _state(i["binding"]) if i["has_binding"] else ""
            key = _state(i["key_binding"]) + (" (%s)" % i["key_binding_via"] if i["key_binding_via"] else "")
            if nothing_to_check(i):
                L.append("- %s: nothing to check — %s · key binding %s" % (i["label"], nothing_to_check(i), key))
                continue
            L.append("- %s: report signature %s · vendor chain %s%s · key binding %s"
                     % (i["label"], _state(i["signature"]), chain, binding, key))
        if any(i["key_binding"] is None for i in h["items"]):
            L.append("  " + KEY_BINDING_GAP)
    return "\n".join(L)


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print(__doc__)
        sys.exit(0)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__)
        sys.exit(64)
    try:
        s = verification_summary(P.load(args[0]))
    except SystemExit as e:
        # A missing dependency says nothing about the manifest: nothing could be
        # checked, which is exit 2, never exit 1 ("failed").
        print(e, file=sys.stderr)
        sys.exit(2)
    print(render_text(s, full="--full" in sys.argv))
    sys.exit(s["exit_code"])


if __name__ == "__main__":
    main()
