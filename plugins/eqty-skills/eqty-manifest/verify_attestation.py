#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["eqty-sdk>=2.4.1", "cryptography", "base58"]
# ///
"""Verify the HARDWARE ATTESTATION EVIDENCE carried in an EQTY manifest.

This is a different question from `verify_credentials.py`. That script proves
a manifest's own signature chain: who registered what, when. This one asks
whether the TEE evidence a manifest carries -- an Intel TDX quote, an AMD
SEV-SNP report, an NVIDIA CC (SPDM) report -- was genuinely produced by
vendor silicon and has not been altered. The signer of the manifest and the
signer of an attestation report are different parties, so the two checks are
independent: either can pass while the other fails.

WHAT THIS DOES AND DOES NOT ESTABLISH
-------------------------------------
It establishes: this report was signed by a key chaining to a PINNED vendor
root (Intel SGX Root CA / AMD ARK), and the report bytes are intact.

It does NOT establish that the enclave was running the code you expect.
That requires reference measurements -- Intel TCB info, NVIDIA RIM, expected
OVMF/kernel values -- which do not ship with these manifests. Every result
therefore carries `measurements_checked: false`, and callers must not round
"authentic hardware" up to "ran the right code". They are different claims
and the gap between them is where a compromised-but-genuine machine lives.

TRUST ANCHORS ARE PINNED, NOT TAKEN FROM THE FILE
-------------------------------------------------
Each evidence blob embeds its own certificate chain, and those chains are
self-consistent by construction -- an attacker forging one would forge a
matching root too. So a chain is only trusted when its root matches a
locally pinned vendor certificate in roots/. Intel and AMD roots are pinned
(and were confirmed byte-identical to the copies embedded in the example
manifests). NVIDIA's Device Identity CA is pinned too, matched against the
SHA-256 fingerprint NVIDIA publishes for it -- see roots/PROVENANCE.md for
how that copy was obtained and why a fingerprint match is what makes it a
trust anchor rather than a self-referential one. Any vendor whose root is
absent still returns `valid: null` with reason `trust_anchor_unavailable`:
chain and signature are checked and reported, but nothing anchors them, and
that is stated rather than glossed.

    uv run eqty-manifest/verify_attestation.py <manifest.json>
    uv run eqty-manifest/verify_attestation.py <manifest.json> <statement_id>
"""
import hashlib
import json
import os
import re
import struct
import sys
import warnings

warnings.filterwarnings("ignore")

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, rsa, padding, utils
except ImportError:
    x509 = None

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOTS_DIR = os.path.join(SCRIPT_DIR, "roots")

PINNED_ROOTS = {
    "intel": "intel-sgx-root-ca.pem",
    "amd": "amd-milan-ark-ask.pem",
    "nvidia": "nvidia-device-identity-ca.pem",
}

PEM_RE = re.compile(rb"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.S)

TDX_HEADER_LEN = 48
TDX_BODY_LEN = 584
SEV_SIGNED_LEN = 0x2A0
SEV_REPORT_LEN = 1184


def _certs(data: bytes):
    """Parse every PEM certificate in a blob. A malformed certificate must
    NOT crash verification -- a corrupted or forged chain is exactly the
    input this tool exists to handle, so it degrades to 'no usable chain'
    and is reported, never raised."""
    out = []
    for b in PEM_RE.findall(data or b""):
        try:
            out.append(x509.load_pem_x509_certificate(b))
        except Exception:
            continue
    return out


def _load_pinned(name):
    path = os.path.join(ROOTS_DIR, PINNED_ROOTS[name])
    if not os.path.exists(path):
        return []
    return _certs(open(path, "rb").read())


def _verify_cert_signature(child, parent):
    """Verify one certificate's signature by its issuer, handling both the
    ECDSA chains Intel uses and the RSA-PSS chains AMD uses."""
    pk = parent.public_key()
    h = child.signature_hash_algorithm
    if isinstance(pk, rsa.RSAPublicKey):
        params = child.signature_algorithm_parameters
        pad = params if isinstance(params, padding.PSS) else padding.PKCS1v15()
        pk.verify(child.signature, child.tbs_certificate_bytes, pad, h)
    else:
        pk.verify(child.signature, child.tbs_certificate_bytes, ec.ECDSA(h))
    return True


def _chain_is_consistent(chain):
    """Each certificate signed by the next one up. Proves internal
    consistency ONLY -- says nothing about whether the root is genuine.

    Fewer than two certificates is INCOMPLETE evidence, not a bad chain:
    nothing links the leaf to anything, so trust is unchecked (None), never
    failed. The report signature is still checked against the leaf."""
    if len(chain) < 2:
        return None, "chain has %d usable certificate(s); cannot establish a path" % len(chain)
    for i in range(len(chain) - 1):
        try:
            _verify_cert_signature(chain[i], chain[i + 1])
        except Exception as e:
            return False, "link %d (%s) not signed by its issuer: %s" % (
                i, chain[i].subject.rfc4514_string()[:40], type(e).__name__)
    return True, None


def _root_is_pinned(chain, vendor):
    if len(chain) < 2:
        return None, "chain is incomplete, so there is no root to compare with the pinned %s root" % vendor
    pinned = _load_pinned(vendor)
    if not pinned:
        return None, "no pinned root bundled for %r" % vendor
    fp = chain[-1].fingerprint(hashes.SHA256())
    for p in pinned:
        if p.fingerprint(hashes.SHA256()) == fp:
            return True, None
    return False, ("chain root %s does not match any pinned %s root"
                   % (chain[-1].subject.rfc4514_string()[:50], vendor))


def _raw_ecdsa_to_der(raw, size):
    return utils.encode_dss_signature(int.from_bytes(raw[:size], "big"),
                                      int.from_bytes(raw[size:size * 2], "big"))


# --------------------------------------------------------------------------
# Intel TDX
# --------------------------------------------------------------------------
def parse_tdx_quote(q: bytes):
    ver, ak_type, tee_type = struct.unpack_from("<HHI", q, 0)
    out = {"format": "intel-tdx-quote", "version": ver, "ak_type": ak_type,
           "tee_type": hex(tee_type)}
    body = q[TDX_HEADER_LEN:TDX_HEADER_LEN + TDX_BODY_LEN]
    out["measurements"] = {
        "mrtd": body[184:232].hex(),
        "mrconfigid": body[232:280].hex(),
        "rtmr0": body[376:424].hex(),
        "report_data": body[520:584].hex(),
    }
    p = TDX_HEADER_LEN + TDX_BODY_LEN + 4
    out["_hdr"], out["_body"] = q[:TDX_HEADER_LEN], body
    out["_sig"], out["_akey"] = q[p:p + 64], q[p + 64:p + 128]
    p += 128
    ctype, csize = struct.unpack_from("<HI", q, p)
    p += 6
    inner = q[p:p + csize]
    out["_qe_report"], out["_qe_sig"] = inner[:384], inner[384:448]
    alen = struct.unpack_from("<H", inner, 448)[0]
    out["_auth"] = inner[450:450 + alen]
    p2 = 450 + alen
    _ict, icsize = struct.unpack_from("<HI", inner, p2)
    out["_chain"] = inner[p2 + 6:p2 + 6 + icsize]
    return out


def verify_tdx(q: bytes):
    checks = []
    try:
        parsed = parse_tdx_quote(q)
    except Exception as e:
        return {"valid": None, "reason": "unparseable",
                "detail": "%s: %s" % (type(e).__name__, e)}
    if parsed["tee_type"] != "0x81":
        return {"valid": None, "reason": "not_tdx",
                "detail": "tee_type %s is not TDX (0x81)" % parsed["tee_type"]}
    chain = _certs(parsed["_chain"])
    if not chain:
        return {"valid": None, "reason": "no_certificate_chain",
                "detail": "quote carries no PCK certificate chain"}

    ok, err = _chain_is_consistent(chain)
    checks.append({"check": "pck_chain_consistent", "passed": ok, "detail": err})
    pinned, perr = _root_is_pinned(chain, "intel")
    checks.append({"check": "root_is_pinned_intel_sgx_root_ca",
                   "passed": pinned, "detail": perr})

    try:
        chain[0].public_key().verify(_raw_ecdsa_to_der(parsed["_qe_sig"], 32),
                                     parsed["_qe_report"], ec.ECDSA(hashes.SHA256()))
        checks.append({"check": "qe_report_signed_by_pck", "passed": True, "detail": None})
    except Exception as e:
        checks.append({"check": "qe_report_signed_by_pck", "passed": False,
                       "detail": type(e).__name__})

    bound = hashlib.sha256(parsed["_akey"] + parsed["_auth"]).digest() == parsed["_qe_report"][320:352]
    checks.append({"check": "attestation_key_bound_to_qe_report", "passed": bound,
                   "detail": None if bound else "sha256(akey||auth) != QE report data"})

    try:
        pk = ec.EllipticCurvePublicNumbers(
            int.from_bytes(parsed["_akey"][:32], "big"),
            int.from_bytes(parsed["_akey"][32:], "big"), ec.SECP256R1()).public_key()
        pk.verify(_raw_ecdsa_to_der(parsed["_sig"], 32),
                  parsed["_hdr"] + parsed["_body"], ec.ECDSA(hashes.SHA256()))
        checks.append({"check": "quote_signature_over_td_report", "passed": True, "detail": None})
    except Exception as e:
        checks.append({"check": "quote_signature_over_td_report", "passed": False,
                       "detail": type(e).__name__})
    return _summarize(checks, parsed, "intel-tdx")


# --------------------------------------------------------------------------
# AMD SEV-SNP
# --------------------------------------------------------------------------
def parse_sev_report(r: bytes):
    out = {"format": "amd-sev-snp-report",
           "version": struct.unpack_from("<I", r, 0)[0],
           "guest_svn": struct.unpack_from("<I", r, 4)[0],
           "policy": hex(struct.unpack_from("<Q", r, 8)[0])}
    out["measurements"] = {"measurement": r[0x90:0x90 + 48].hex(),
                           "report_data": r[0x50:0x50 + 64].hex()}
    out["_signed"] = r[:SEV_SIGNED_LEN]
    out["_sig"] = r[SEV_SIGNED_LEN:SEV_SIGNED_LEN + 512]
    out["_chain"] = r[SEV_REPORT_LEN:]
    return out


def verify_sev(r: bytes, chain_pem: bytes = None):
    """`chain_pem` is the VCEK/ASK/ARK chain when the evidence carries it as
    its own blob (`VCompAmdSevEvidenceV1`); the older layout embeds it after
    the report instead, and passes None."""
    checks = []
    try:
        parsed = parse_sev_report(r)
    except Exception as e:
        return {"valid": None, "reason": "unparseable",
                "detail": "%s: %s" % (type(e).__name__, e)}
    chain = _certs(chain_pem if chain_pem is not None else parsed["_chain"])
    if not chain:
        return {"valid": None, "reason": "no_certificate_chain",
                "detail": "report carries no VCEK/ASK/ARK chain (certificateChain "
                          "is often null in these manifests -- nothing to verify against)"}

    ok, err = _chain_is_consistent(chain)
    checks.append({"check": "vcek_chain_consistent", "passed": ok, "detail": err})
    pinned, perr = _root_is_pinned(chain, "amd")
    checks.append({"check": "root_is_pinned_amd_ark", "passed": pinned, "detail": perr})

    # SEV-SNP carries r and s as 72-byte LITTLE-endian values.
    sig = parsed["_sig"]
    der = utils.encode_dss_signature(int.from_bytes(sig[0:72][::-1], "big"),
                                     int.from_bytes(sig[72:144][::-1], "big"))
    try:
        chain[0].public_key().verify(der, parsed["_signed"], ec.ECDSA(hashes.SHA384()))
        checks.append({"check": "report_signed_by_vcek", "passed": True, "detail": None})
    except Exception as e:
        checks.append({"check": "report_signed_by_vcek", "passed": False,
                       "detail": type(e).__name__})
    return _summarize(checks, parsed, "amd-sev-snp")


# --------------------------------------------------------------------------
# NVIDIA CC (SPDM)
# --------------------------------------------------------------------------
def parse_nvidia_spdm(rep: bytes):
    return {"format": "nvidia-cc-spdm",
            "spdm_version": hex(rep[0]), "spdm_code": hex(rep[1]),
            "measurements": {"response_sha384": hashlib.sha384(rep[:-96]).hexdigest()},
            "_signed": rep[:-96], "_sig": rep[-96:]}


def verify_nvidia(rep: bytes, chain_pem: bytes):
    checks = []
    try:
        parsed = parse_nvidia_spdm(rep)
    except Exception as e:
        return {"valid": None, "reason": "unparseable",
                "detail": "%s: %s" % (type(e).__name__, e)}
    chain = _certs(chain_pem or b"")
    if not chain:
        return {"valid": None, "reason": "no_certificate_chain",
                "detail": "no NVIDIA certificate chain supplied alongside the report"}

    ok, err = _chain_is_consistent(chain)
    checks.append({"check": "nvidia_chain_consistent", "passed": ok, "detail": err})
    pinned, perr = _root_is_pinned(chain, "nvidia")
    checks.append({"check": "root_is_pinned_nvidia_device_identity_ca",
                   "passed": pinned, "detail": perr})
    try:
        chain[0].public_key().verify(_raw_ecdsa_to_der(parsed["_sig"], 48),
                                     parsed["_signed"], ec.ECDSA(hashes.SHA384()))
        checks.append({"check": "spdm_signature_over_response", "passed": True, "detail": None})
    except Exception as e:
        checks.append({"check": "spdm_signature_over_response", "passed": False,
                       "detail": type(e).__name__})
    return _summarize(checks, parsed, "nvidia-cc")


# --------------------------------------------------------------------------
def _summarize(checks, parsed, kind):
    """A result is only `true` when every check passed INCLUDING the pinned
    root. A check that could not be performed (None) yields `null`, never
    `true` -- an unanchored chain must not read as verified hardware."""
    failed = [c for c in checks if c["passed"] is False]
    unknown = [c for c in checks if c["passed"] is None]
    if failed:
        valid, reason = False, "verification_failed"
    elif any(c["check"].endswith("_chain_consistent") for c in unknown):
        valid, reason = None, "incomplete_certificate_chain"
    elif unknown:
        valid, reason = None, "trust_anchor_unavailable"
    else:
        valid, reason = True, None
    out = {"valid": valid, "evidence_type": kind, "checks": checks,
           "measurements_checked": False,
           "measurements_note": "Signature and chain only. Reference values (Intel TCB "
                                "info, NVIDIA RIM, expected OVMF/kernel measurements) are "
                                "not bundled, so this does NOT establish what code ran.",
           "reported_measurements": parsed.get("measurements", {}),
           "format": parsed.get("format")}
    if reason:
        out["reason"] = reason
    return out


# --------------------------------------------------------------------------
# TPM 2.0 quote (Azure confidential VM vTPM)
# --------------------------------------------------------------------------
TPM_GENERATED = 0xFF544347
TPM_ST_ATTEST_QUOTE = 0x8018
TPM_ALG = {0x000B: hashes.SHA256, 0x000C: hashes.SHA384} if x509 else {}
TPM_ALG_RSASSA, TPM_ALG_RSAPSS = 0x0014, 0x0016


def _tpm2b(b, i):
    n = struct.unpack_from(">H", b, i)[0]
    return b[i + 2:i + 2 + n], i + 2 + n


def parse_tpm_quote(q: bytes):
    """TPMS_ATTEST for a quote: magic, type, qualifiedSigner, extraData,
    clockInfo, firmwareVersion, then TPMS_QUOTE_INFO (PCR selection + digest).
    Raises on anything that is not a complete quote."""
    magic, typ = struct.unpack_from(">IH", q, 0)
    if magic != TPM_GENERATED or typ != TPM_ST_ATTEST_QUOTE:
        raise ValueError("not a TPM2 quote (magic %#x, type %#x)" % (magic, typ))
    _, i = _tpm2b(q, 6)                   # qualifiedSigner
    extra, i = _tpm2b(q, i)
    i += 17 + 8                           # clockInfo, firmwareVersion
    count = struct.unpack_from(">I", q, i)[0]
    i += 4
    selection = []
    for _ in range(count):
        alg, size = struct.unpack_from(">HB", q, i)
        bitmap = q[i + 3:i + 3 + size]
        i += 3 + size
        selection.append((alg, [8 * k + bit for k in range(size) for bit in range(8) if bitmap[k] >> bit & 1]))
    digest, i = _tpm2b(q, i)
    if i != len(q):
        raise ValueError("%d trailing bytes after the quote" % (len(q) - i))
    return {"format": "tpm2-quote", "extra_data": extra.hex(), "pcr_selection": selection,
            "measurements": {"pcr_digest": digest.hex()}, "_digest": digest}


def did_key_x(did):
    """The X coordinate of a P-256 `did:key`, or None for any other DID."""
    if not isinstance(did, str) or not did.startswith("did:key:z"):
        return None
    import base58
    try:
        raw = base58.b58decode(did[len("did:key:z"):])
    except ValueError:
        return None
    # multicodec p256-pub (0x1200, varint 80 24) + 33-byte compressed point
    return raw[3:] if raw[:2] == b"\x80\x24" and len(raw) == 35 else None


def committed_key_bytes(kind, raw):
    """The 32 signed bytes where EQTY's `userData: {type: "key"}` evidence puts the
    DID's public-key X coordinate, or None where this evidence has no such field.
    TPM: the quote's extraData. Intel TDX: REPORTDATA[32:64] (the first half is
    zero). NVIDIA CC: the nonce of the SPDM GET_MEASUREMENTS request (code 0xE0)
    that opens the report, which the SPDM signature covers."""
    if kind == "tpm":
        return bytes.fromhex(parse_tpm_quote(raw)["extra_data"])
    if kind == "tdx" and len(raw) >= TDX_HEADER_LEN + 584:
        return raw[TDX_HEADER_LEN + 552:TDX_HEADER_LEN + 584]
    if kind == "nvidia" and len(raw) >= 36 and raw[1] == 0xE0:
        return raw[4:36]
    return None


def key_binding_check(kind, raw, did_claim, subject):
    """The `key_bound_to_did` check for evidence whose credential declares
    `userData: {type: "key", value: did_claim}`, or None when none applies (no key
    claim, a DID that is not P-256, or evidence without a known field)."""
    if did_claim is None:
        return None
    if did_claim != subject:
        return {"check": "key_bound_to_did", "passed": False,
                "detail": "the evidence commits to %s, not to the credential's subject" % did_claim}
    x, got = did_key_x(did_claim), committed_key_bytes(kind, raw)
    if x is None or got is None:
        return None
    ok = got == x
    return {"check": "key_bound_to_did", "passed": ok,
            "detail": None if ok else "the signed report does not carry this DID's public key"}


def _add_key_binding(result, check):
    """Append a key-binding check to an already-summarised result."""
    if check is None or "checks" not in result:
        return result
    result["checks"].append(check)
    if check["passed"] is False:
        result["valid"], result["reason"] = False, "verification_failed"
    return result


def verify_tpm(quote: bytes, signature: bytes, ak_pem: bytes, claimed_pcrs=None, bindings=(),
               did_claim=None, subject=None):
    """Checks the quote itself; `bindings` carries the cross-evidence checks
    (runtime data, attestation key) computed by the caller.

    KEY BINDING: when the credential declares `userData: {type: "key", value:
    <DID>}`, the quote's extraData (TPM2 qualifying data) must be that DID's
    P-256 public-key X coordinate -- EQTY's rule, see committed_key_bytes().
    It is checked only for the credential's own subject and a P-256 did:key;
    otherwise no check is added and key binding stays "not checked".

    TRUST PATH: no TPM root is pinned, so the attestation key is NOT checked
    against Microsoft's vTPM CA. It is trusted only if it is bound to a
    hardware report (AMD SEV-SNP or Intel TDX) that verified against a pinned vendor root -- the
    `bound_*` checks. Without that binding the result stays unverified: a
    quote signed by a key the file itself supplies proves nothing."""
    checks = []
    try:
        parsed = parse_tpm_quote(quote)
    except Exception as e:
        return {"valid": None, "reason": "unparseable", "detail": "%s: %s" % (type(e).__name__, e)}
    try:
        from cryptography.hazmat.primitives import serialization
        ak = serialization.load_pem_public_key(ak_pem)
        sig_alg, hash_alg = struct.unpack_from(">HH", signature, 0)
        sig, _ = _tpm2b(signature, 4)
        pad = (padding.PKCS1v15() if sig_alg == TPM_ALG_RSASSA else
               padding.PSS(padding.MGF1(TPM_ALG[hash_alg]()), padding.PSS.AUTO) if sig_alg == TPM_ALG_RSAPSS else None)
        if pad is None or hash_alg not in TPM_ALG:
            return {"valid": None, "reason": "unsupported_signature_scheme",
                    "detail": "TPM signature scheme %#x / hash %#x is not supported" % (sig_alg, hash_alg)}
        ak.verify(sig, quote, pad, TPM_ALG[hash_alg]())
        checks.append({"check": "quote_signed_by_ak", "passed": True, "detail": None})
    except Exception as e:
        checks.append({"check": "quote_signed_by_ak", "passed": False, "detail": type(e).__name__})
    if claimed_pcrs:
        sel = parsed["pcr_selection"]
        if len(sel) != 1 or sel[0][0] not in TPM_ALG or not all(str(k) in claimed_pcrs for k in sel[0][1]):
            checks.append({"check": "bound_pcr_claim_to_quote", "passed": None,
                           "detail": "the credential does not list every PCR the quote selects"})
        else:
            h = hashes.Hash(TPM_ALG[sel[0][0]]())
            for k in sel[0][1]:
                h.update(bytes.fromhex(claimed_pcrs[str(k)]))
            ok = h.finalize() == parsed["_digest"]
            checks.append({"check": "bound_pcr_claim_to_quote", "passed": ok,
                           "detail": None if ok else "the PCR values the credential claims do not "
                                                     "hash to the digest the TPM signed"})
    kb = key_binding_check("tpm", quote, did_claim, subject)
    if kb:
        checks.append(kb)
    checks += list(bindings)
    return _summarize(checks, parsed, "tpm")


# Hardware reports a TPM can be bound through: evidence type -> (report field,
# byte offset of the signed report_data). Azure confidential VMs on either
# vendor put SHA-256(runtime JSON) in the first 32 bytes of report_data.
TPM_BINDING_REPORTS = {
    "VCompAmdSevEvidenceV1": ("amdSevReport", 0x50, "AMD SEV-SNP report"),
    "VCompIntelTdxEvidenceV1": ("intelTdxReport", TDX_HEADER_LEN + 520, "Intel TDX quote"),
}


def _tpm_bindings(m, P, statements, tpm_cred, ak_pem, results):
    """Bind the TPM's attestation key to a hardware report for the same
    identity: that report's signed report_data must commit to the VM's runtime
    data, and the runtime data's `HCLAkPub` must be this attestation key.
    Azure confidential-VM layout: report_data[:32] = SHA-256(runtime JSON)."""
    import base64 as _b
    from cryptography.hazmat.primitives import serialization
    did = (tpm_cred.get("credentialSubject") or {}).get("id")
    for sid, st in statements.items():
        cred = st.get("credential") if isinstance(st.get("credential"), dict) else {}
        ev = cred.get("evidence") or {}
        ud = ((cred.get("credentialSubject") or {}).get("identity") or {}).get("userData") or {}
        if (cred.get("credentialSubject") or {}).get("id") != did or ev.get("type") not in TPM_BINDING_REPORTS \
                or ud.get("type") != "sha256" or not ud.get("source"):
            continue
        field, rd_at, what = TPM_BINDING_REPORTS[ev["type"]]
        report = m.raw_bytes(P.strip_urn(ev.get(field) or ""))
        runtime = m.raw_bytes(P.strip_urn(ud["source"]))
        if report is None or runtime is None:
            continue
        hw_ok = (results.get(sid) or {}).get("valid")
        json_part = runtime.rstrip(b"\0")
        bound = hashlib.sha256(json_part).digest() == report[rd_at:rd_at + 32]
        checks = [{"check": "bound_runtime_data_to_hardware_report",
                   "passed": bound if hw_ok is True else (False if hw_ok is False else None),
                   "detail": None if bound and hw_ok is True else
                   ("the %s it relies on did not verify" % what if hw_ok is not True else
                    "the %s's report_data does not commit to this runtime data" % what)}]
        try:
            key = next(k for k in json.loads(json_part)["keys"] if k.get("kid") == "HCLAkPub")
            n = int.from_bytes(_b.urlsafe_b64decode(key["n"] + "=="), "big")
            e = int.from_bytes(_b.urlsafe_b64decode(key["e"] + "=="), "big")
            nums = serialization.load_pem_public_key(ak_pem).public_numbers()
            same = (nums.n, nums.e) == (n, e)
            checks.append({"check": "bound_ak_to_runtime_data", "passed": same,
                           "detail": None if same else "the runtime data names a different attestation key"})
        except Exception as ex:
            checks.append({"check": "bound_ak_to_runtime_data", "passed": None,
                           "detail": "runtime data carries no readable HCLAkPub (%s)" % type(ex).__name__})
        return checks
    return [{"check": "bound_ak_to_hardware_report", "passed": None,
             "detail": "no verified hardware report for this identity commits to the attestation key, "
                       "and no TPM root is pinned, so the key is unauthenticated"}]


EVIDENCE_DISPATCH = {
    "EqtyVCompIntelTdxV0Evidence": ("tdx", "report"),
    "VCompIntelTdxEvidenceV1": ("tdx", "intelTdxReport"),
    "EqtyVCompAmdSevV1Evidence": ("sev", "report"),
    "VCompAmdSevEvidenceV1": ("sev", "amdSevReport"),
    "EqtyVCompNvidiaCcV0Evidence": ("nvidia", "report"),
    "VCompNvidiaCcEvidenceV1": ("nvidia", "nvidiaCcReport"),
    "VCompTpmEvidenceV1": ("tpm", "quote"),
}


def verify_manifest_attestations(data, only_statement_id=None):
    if x509 is None:
        raise SystemExit("Missing required package: cryptography\n"
                         "Install: run the script with `uv run`, or: pip install -r requirements.txt")
    sys.path.insert(0, SCRIPT_DIR)
    import parse_manifest as P

    m = P.Manifest(data)
    results = {}
    deferred = []
    for sid, st in data.get("statements", {}).items():
        if only_statement_id and sid != only_statement_id:
            continue
        cred = st.get("credential")
        if not isinstance(cred, dict) or "evidence" not in cred:
            continue
        ev = cred["evidence"]
        types = ev.get("type") or []
        if isinstance(types, str):
            types = [types]
        handler = next((EVIDENCE_DISPATCH[t] for t in types if t in EVIDENCE_DISPATCH), None)
        if handler is None:
            results[sid] = {"valid": None, "reason": "unsupported_evidence_type",
                            "detail": "evidence type %s is not supported by this skill, so "
                                      "it was not checked" % ", ".join(map(str, types))}
            continue
        kind, field = handler
        cid = ev.get(field)
        if not cid:
            results[sid] = {"valid": None, "reason": "no_report",
                            "detail": "evidence declares %s but the %r field is null or absent"
                                      % (types, field)}
            continue
        raw = m.raw_bytes(P.strip_urn(cid))
        if raw is None:
            # Present-but-undecodable is a different fault from absent: the
            # content check reports it as invalid_base64, so say the same here.
            present = P.strip_urn(cid) in m.blobs
            results[sid] = {"valid": None, "cid": cid,
                            "reason": "report_blob_invalid" if present else "report_blob_missing",
                            "detail": ("evidence references %s but that blob is not valid base64"
                                       if present else
                                       "evidence references %s but that blob is not in the manifest") % cid}
            continue
        if kind == "tpm":                 # needs the hardware reports' results: done below
            deferred.append((sid, cred, ev, raw, types))
            continue
        if kind == "tdx":
            results[sid] = verify_tdx(raw)
        elif kind == "sev":
            chain_cid = ev.get("certificateChain")
            chain = m.raw_bytes(P.strip_urn(chain_cid)) if chain_cid else None
            results[sid] = verify_sev(raw, chain if chain_cid else None)
            if chain_cid and chain is None:
                results[sid]["detail"] = ("evidence references certificate chain %s but that "
                                          "blob is not in the manifest (or not valid base64)" % chain_cid)
        else:
            chain_cid = ev.get("certificateChain")
            chain = m.raw_bytes(P.strip_urn(chain_cid)) if chain_cid else None
            results[sid] = verify_nvidia(raw, chain)
            if chain_cid and chain is None:
                results[sid]["detail"] = ("evidence references certificate chain %s but that "
                                          "blob is not in the manifest (or not valid base64)" % chain_cid)
        subj = cred.get("credentialSubject") or {}
        ud = (subj.get("identity") or {}).get("userData") or {}
        _add_key_binding(results[sid], key_binding_check(
            kind, raw, ud.get("value") if ud.get("type") == "key" else None, subj.get("id")))
        results[sid]["evidence_declared"] = types

    # TPM last: its attestation key is trusted only through a hardware report
    # that has already verified, so those results must exist first.
    for sid, cred, ev, quote, types in deferred:
        sig = m.raw_bytes(P.strip_urn(ev.get("quoteSignature") or ""))
        ak = m.raw_bytes(P.strip_urn(ev.get("AKPublicKey") or ""))
        if sig is None or ak is None:
            results[sid] = {"valid": None, "reason": "report_blob_missing",
                            "cid": ev.get("quoteSignature") if sig is None else ev.get("AKPublicKey"),
                            "detail": "the quote's %s blob is not in the manifest (or not valid base64)"
                                      % ("quoteSignature" if sig is None else "AKPublicKey")}
        else:
            subj = cred.get("credentialSubject") or {}
            ident = subj.get("identity") or {}
            pcrs, ud = ident.get("pcr"), ident.get("userData") or {}
            results[sid] = verify_tpm(quote, sig, ak, pcrs if isinstance(pcrs, dict) else None,
                                      _tpm_bindings(m, P, data.get("statements", {}), cred, ak, results),
                                      ud.get("value") if ud.get("type") == "key" else None, subj.get("id"))
        results[sid]["evidence_declared"] = types
    return results


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print(__doc__)
        sys.exit(0)
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    sys.path.insert(0, SCRIPT_DIR)
    import parse_manifest as P
    data = P.load(sys.argv[1])
    only = sys.argv[2] if len(sys.argv) > 2 else None
    print(json.dumps(verify_manifest_attestations(data, only), indent=2, default=str))


if __name__ == "__main__":
    main()
