#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["eqty-sdk>=2.4.1", "cryptography", "base58"]
# ///
"""
report.py — Render an EQTY lineage manifest as a self-contained HTML report.

This is the "make it consumable for a human" half of the skill. It produces
one file, with no external assets, no JavaScript and no network access, that
a person who was not present for the run can open and understand.

    uv run eqty-manifest/report.py <manifest.json> <out.html>
        [--title TEXT]           # headline (defaults to the run kind)
        [--session TEXT]         # 1-2 sentences starting "The session"/"This session"
        [--step-notes steps.json]# {"<statement id or step #>": "one line"}
        [--narrative notes.md]   # agent-authored prose for "What this run was"
        [--no-verify]            # skip cryptographic verification (see below)

WHY THIS RUNS VERIFICATION ITSELF
---------------------------------
The report is a portable artifact; the conversation that produced it is not.
If its trust numbers were transcribed from what an agent happened to run in
chat, they would be claims *typed* by the agent rather than facts *computed*
by the file. So the numbers in the report come from calling the same
verifiers the CLI calls -- verify_credentials.verify_manifest() and
verify_attestation, imported, never reimplemented. Verification costs
0.2-0.7s on the example corpus, which is a cheap price for the file being
independently true. `--no-verify` exists for speed, and says so in the
output rather than leaving a reader to assume the checks passed.

WHAT IS COMPUTED VS. AUTHORED
-----------------------------
Everything structural -- metrics, participants, phases, per-step detail,
trust results -- is computed here. The one thing code cannot produce is what
the run *meant* ("two client sites train a classifier locally and never
share it"). That is the interpreting agent's job, and it arrives via
`--narrative`. Without it the report is still complete and still honest; it
is simply thinner in the one section that most wants prose.

REPORTING DISCIPLINE
--------------------
The three integrity checks (content addresses, signatures, attestation) fail
independently and are never merged into a single verdict. A check that could
not run renders as "not checked", never as absent and never as green. A
claimed execution environment is never presented as a verified one, and even
verified TEE evidence does not establish which code ran.
"""

import argparse
import datetime
import html
import json
import os
import sys
from collections import Counter, OrderedDict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import parse_manifest as P  # noqa: E402


# Metadata fields that, when present, give an explicit grouping for the
# roll-up. Ordered by preference. Anything not listed here falls back to
# timestamp clustering, and the report says which basis was used.
GROUP_KEYS = ("round", "iteration", "epoch", "turn", "phase", "step_index")


# ---------------------------------------------------------------- helpers

def _short(s, n=14):
    """Abbreviate a DID or CID for display. Never used as the only
    identifier for something -- the full value stays available in the
    appendix -- because a truncated hash is not an identifier."""
    if not s:
        return ""
    s = str(s)
    return s if len(s) <= n + 3 else s[:n] + "…"


def _parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def _clock(ts):
    d = _parse_ts(ts)
    return d.strftime("%H:%M:%S") if d else (ts or "")


def _span(first, last):
    a, b = _parse_ts(first), _parse_ts(last)
    if not a or not b:
        return None
    secs = (b - a).total_seconds()
    if secs < 1:
        return "<1 s"
    if secs < 90:
        return "%g s" % round(secs, 1)
    if secs < 5400:
        return "%g min" % round(secs / 60, 1)
    return "%g h" % round(secs / 3600, 1)


def _meta(entry):
    m = entry.get("metadata")
    return m if isinstance(m, dict) else {}


def _step_name(entry):
    md = _meta(entry)
    return md.get("name") or md.get("label") or _short(entry.get("statement_id"), 10)


def _label_for(m, cid, preview):
    """Prefer a registered human name for a CID; fall back to the first
    meaningful line of its resolved content; never headline a bare CID."""
    md = m.metadata_for(cid)
    if isinstance(md, dict) and md.get("name"):
        return str(md["name"])
    if preview:
        line = str(preview).strip().splitlines()[0] if str(preview).strip() else ""
        if line:
            return line[:70] + ("…" if len(line) > 70 else "")
    return _short(cid, 18)


def _collapse(names):
    """server-init, train, train, agg  ->  'server-init -> 2x train -> agg'"""
    out = []
    for n in names:
        if out and out[-1][0] == n:
            out[-1][1] += 1
        else:
            out.append([n, 1])
    return " → ".join(("%d× %s" % (c, n)) if c > 1 else n for n, c in out)


# ------------------------------------------------------------- roll-up

def group_steps(m, timeline):
    """Roll the step-level DAG up into human-scale phases.

    Two bases, in order of preference:
      1. an explicit grouping field in the step metadata (`round`, etc.) --
         this is what a federated-learning manifest carries;
      2. contiguous runs of identical timestamp -- which for an agent trace
         recovers the real turn structure, since a model call and the tool
         calls it triggers share a second.

    The basis is returned alongside the phases and printed in the report,
    because a derived grouping should not be mistaken for one the manifest
    declared.
    """
    key = next((k for k in GROUP_KEYS
                if any(k in _meta(e) for e in timeline)), None)

    buckets = OrderedDict()
    if key:
        basis = "the manifest's own <code>%s</code> field" % html.escape(key)
        for e in timeline:
            buckets.setdefault(_meta(e).get(key, "—"), []).append(e)
        label_fmt = lambda k, i: "%s %s" % (key.replace("_", " ").title(), k)
    else:
        basis = ("contiguous timestamp clusters — this manifest declares no "
                 "round/iteration field, so the grouping is derived, not stated")
        for e in timeline:
            ts = e.get("timestamp")
            if buckets and next(reversed(buckets)) == ts:
                buckets[ts].append(e)
            else:
                buckets[ts] = [e]
        label_fmt = lambda k, i: "Segment %d" % i

    phases = []
    for i, (k, steps) in enumerate(buckets.items(), 1):
        times = [s.get("timestamp") for s in steps if s.get("timestamp")]
        produced = []
        for s in steps:
            for o in s.get("outputs") or []:
                lab = _label_for(m, o["cid"], o.get("preview"))
                if lab not in produced:
                    produced.append(lab)
        phases.append({
            "label": label_fmt(k, i),
            "steps": steps,
            "step_count": len(steps),
            "start": min(times) if times else None,
            "end": max(times) if times else None,
            "duration": _span(min(times), max(times)) if times else None,
            "chain": _collapse([_step_name(s) for s in steps]),
            "operators": sorted({_meta(s).get("site") or _short(s.get("operatedBy"), 10)
                                 for s in steps if s.get("operatedBy") or _meta(s).get("site")}),
            "produced": produced,
        })
    return {"basis": basis, "grouping_key": key, "phases": phases}


# ------------------------------------------------------------ trust rows

def signature_report(data, verify=True):
    """Signature verification, via verify_credentials -- imported, not
    reimplemented. A missing optional dependency yields status
    'not_checked', which the renderer shows as unknown rather than clean."""
    total = sum(1 for s in data.get("statements", {}).values()
                if s.get("@type") == "CredentialRegistration")
    if not verify:
        return {"status": "skipped", "total": total,
                "detail": "--no-verify was passed; signatures were NOT checked"}
    try:
        import verify_credentials as VC
        results = VC.verify_manifest(data)
    except SystemExit as e:            # _check_deps() exits on missing deps
        return {"status": "not_checked", "total": total, "detail": str(e)}
    except Exception as e:             # noqa: BLE001 - never fail the report
        return {"status": "error", "total": total,
                "detail": "%s: %s" % (type(e).__name__, e)}

    vals = [r.get("valid") for r in results.values()]
    by_subject = {}
    for sid, r in results.items():
        subj = ((data["statements"][sid].get("credential") or {})
                .get("credentialSubject") or {}).get("id")
        if subj:
            by_subject[subj] = r.get("valid")
    return {
        "status": "run",
        "total": len(results),
        "valid": sum(1 for v in vals if v is True),
        "invalid": sum(1 for v in vals if v is False),
        "inconclusive": sum(1 for v in vals if v is None),
        "key_types": dict(Counter(r.get("keyType") for r in results.values() if r.get("keyType"))),
        "context_sources": dict(Counter(r.get("contextSource") for r in results.values()
                                        if r.get("contextSource"))),
        "reasons": dict(Counter(r.get("reason") for r in results.values() if r.get("reason"))),
        "by_subject": by_subject,
    }


def statement_integrity_report(data, verify=True):
    """Does each statement still hash to its own `@id`? A SEPARATE axis from
    signature validity, via verify_credentials -> eqty_sdk.verify_statement.

    Reported on its own row because neither check implies the other. A
    credential lifted verbatim into another registration keeps a valid
    signature and is caught only here; conversely a statement can hash
    correctly and carry a proof that does not verify."""
    total = len(data.get("statements") or {})
    if not verify:
        return {"status": "skipped", "total": total,
                "detail": "--no-verify was passed; statement integrity was NOT checked"}
    try:
        import verify_credentials as VC
        results = VC.verify_statements(data)
    except SystemExit as e:
        return {"status": "not_checked", "total": total, "detail": str(e)}
    except Exception as e:  # noqa: BLE001 - never fail the report
        return {"status": "error", "total": total,
                "detail": "%s: %s" % (type(e).__name__, e)}

    vals = [r.get("valid") for r in results.values()]
    failed = {sid: r for sid, r in results.items() if r.get("valid") is False}
    return {
        "status": "run",
        "total": len(results),
        "valid": sum(1 for v in vals if v is True),
        "invalid": len(failed),
        "inconclusive": sum(1 for v in vals if v is None),
        "reasons": dict(Counter(r.get("reason") for r in results.values() if r.get("reason"))),
        "failed_types": dict(Counter(r.get("@type") for r in failed.values())),
        "failed_ids": sorted(failed)[:20],
    }


def _stmt_row(s):
    st = s.get("status")
    if st == "run":
        if s["total"] == 0:
            return "unk", "no statements in this manifest"
        if s["invalid"]:
            types = ", ".join(sorted(s.get("failed_types") or {})) or "statements"
            return "warn", ("%d of %d statements do not hash to their @id (%s)"
                            % (s["invalid"], s["total"], types))
        if s["inconclusive"]:
            return "warn", "%d/%d intact, %d could not be checked" % (
                s["valid"], s["total"], s["inconclusive"])
        return "ok", "%d/%d statements hash to their @id" % (s["valid"], s["total"])
    if st == "skipped":
        return "unk", "not checked (--no-verify)"
    return "unk", "not checked — %s" % _short(s.get("detail", st), 90)


def _verification(data):
    import summary
    return summary.verification_summary(data)


def attestation_report(m, timeline, verify=True):
    """Two strictly separate layers: what environment each step CLAIMS, and
    whether the underlying TEE evidence actually verified. Claimed is never
    rendered as verified, and verified never means 'ran the expected code'."""
    claimed = [e for e in timeline if (e.get("environment") or {}).get("environment_claimed")]
    types = Counter((e.get("environment") or {}).get("type") for e in claimed)
    out = {
        "steps_total": len(timeline),
        "steps_claiming_environment": len(claimed),
        "steps_without_claim": len(timeline) - len(claimed),
        "claimed_types": dict(types),
    }
    if not verify:
        out["evidence"] = {"status": "skipped",
                           "detail": "--no-verify was passed; TEE evidence was NOT checked"}
        return out
    # parse_manifest._verify_evidence() already reports 'unavailable' rather
    # than passing when `cryptography` is absent -- reuse that discipline.
    out["evidence"] = m._verify_evidence()
    return out


# ---------------------------------------------------------- participants

def participants(m, timeline, summary):
    rows = []
    for did, count in sorted(summary["signers"].items(), key=lambda kv: -kv[1]):
        mine = [e for e in timeline if e.get("operatedBy") == did]
        sites = {_meta(e).get("site") for e in mine if _meta(e).get("site")}
        meta = m.metadata_for(did) if did else None
        rows.append({
            "did": did,
            "statements": count,
            "role": ", ".join(sorted(s for s in sites if s)) or None,
            "steps_operated": len(mine),
            "step_names": sorted({_step_name(e) for e in mine}),
            # entity metadata can carry PII; surface only that it exists.
            "has_entity_metadata": isinstance(meta, dict) and bool(meta),
        })
    return rows


def headline(timeline, summary):
    """Derived, deliberately conservative framing. The confident version of
    this belongs in --narrative, written by the agent that read the run."""
    md = [_meta(e) for e in timeline]
    frameworks = Counter(d.get("framework") for d in md if d.get("framework"))
    kinds = Counter(d.get("computation_type") for d in md if d.get("computation_type"))
    if any("flare_job_id" in d for d in md):
        kind = "Federated learning run (NVIDIA FLARE)"
    elif "chat_model" in kinds:
        kind = "LLM agent run"
    elif kinds:
        kind = "Computation (%s)" % ", ".join(sorted(kinds))
    else:
        kind = "Computation"
    rng = summary.get("time_range") or [None, None]
    return {
        "kind": kind,
        "framework": frameworks.most_common(1)[0][0] if frameworks else None,
        "wall_clock": _span(rng[0], rng[1]),
        "start": rng[0],
        "end": rng[1],
        "step_count": len(timeline),
    }


# Plain-language reading of each summary.py problem kind, shown above its raw
# lines. These say what the result means and what evidence would settle it --
# never why it happened: the manifest records absence, not its cause.
PROBLEM_HELP = {
    "Statement modified": ("The statement's bytes no longer hash to its own @id, so it changed after it was registered.",
                           "The original statement bytes; this one cannot be trusted as written."),
    "Credential missing": ("No credential in this manifest signs this statement. It is unsigned, not forged.",
                           "A CredentialRegistration whose subject is this statement."),
    "Statement not checked": ("The statement's hash against its @id could not be computed here.",
                              "Rerun with the verifier's dependencies installed."),
    "Malformed credential": ("The credential is not well-formed enough to verify.", "A well-formed credential for the same statement."),
    "Signature failed": ("The signature does not verify against the key it names. The signed content is not what was signed.",
                         "A credential whose signature verifies; treat the subject as unverified until then."),
    "Signature not checked": ("The signature could not be checked (unsupported proof type, or dates outside validity). Unknown, not failed.",
                              "A proof type this verifier supports, or a check inside the credential's validity window."),
    "Registration does not re-hash": (
        "The credential verifies, but its own registration no longer hashes to its @id.",
        "Nothing in the manifest alone can tell tampering from older-emitter drift; see the note below."),
    "Registration not checked": ("The registration's hash against its @id could not be computed.", "Rerun with dependencies installed."),
    "Dangling credential": ("A credential vouches for a subject this manifest does not contain.",
                           "The statement the credential names, included in the manifest."),
    "Issuer mismatch": ("The credential's issuer is not the identity that registered it.",
                        "A credential issued by the registration's own signer."),
    "Hardware evidence missing": ("An identity points to hardware evidence the manifest does not carry, so there was nothing to check.",
                                  "The evidence blobs themselves, included in the manifest."),
    "Hardware signature failed": ("The hardware report's signature does not verify. Do not treat that hardware as attested.",
                                  "A hardware report whose signature verifies."),
    "Hardware signature not checked": ("The hardware report's signature could not be checked. Unknown, not failed.",
                                       "The report and its evidence blobs in a form this verifier supports."),
    "Vendor chain failed": ("The report's certificate chain does not reach the pinned vendor root.",
                            "A chain that verifies to the vendor's root."),
    "Vendor chain not checked": ("The certificate chain could not be followed to a vendor root (for example, too short).",
                                 "The full certificate chain, included with the evidence."),
    "Hardware binding failed": ("A key claimed to be bound to verified hardware is not.", "Evidence that binds the key to a verified report."),
    "Hardware binding not checked": ("The binding to a verified hardware report could not be checked.",
                                     "A verified hardware report for the same identity."),
    "Key binding failed": ("The hardware evidence does not carry the DID's key it claims to vouch for.",
                           "Evidence whose report data carries this DID's public key."),
    "Missing system": ("Steps say they ran on a system this manifest gives no identity to, so where they ran cannot be checked.",
                       "An identity attestation or DID registration for that system, included in the manifest."),
}


SAFE_POD_FIELDS = ("name", "namespace")


def identity_info(data):
    """What the manifest says about each identity: attested types, any pod /
    container names (workload labels, not personal data), and the hosts its
    attestation says it runs on. Personal entity metadata is never read here."""
    S = data.get("statements") or {}
    out = {}

    def did_of(v):
        if isinstance(v, dict):
            v = v.get("id")
        return v.split("#")[0] if isinstance(v, str) else None

    for sid, s in S.items():
        if s.get("@type") == "DidRegistration" and s.get("did"):
            d = out.setdefault(did_of(s["did"]), {"types": []})
            d["registered"] = True
            t = (s.get("vcomp") or {}).get("@type") if isinstance(s.get("vcomp"), dict) else None
            if t and t not in d["types"]:
                d["types"].append(t)
        cred = s.get("credential") if isinstance(s.get("credential"), dict) else None
        if not cred or "IdentityAttestation" not in (cred.get("type") or []):
            continue
        subj = cred.get("credentialSubject") or {}
        did = did_of(subj.get("id"))
        if not did:
            continue
        d = out.setdefault(did, {"types": []})
        ident = subj.get("identity") if isinstance(subj.get("identity"), dict) else {}
        if ident.get("type") and ident["type"] not in d["types"]:
            d["types"].append(ident["type"])
        if isinstance(ident.get("pod"), dict):
            d["pod"] = {k: str(ident["pod"][k]) for k in SAFE_POD_FIELDS if ident["pod"].get(k)}
        names = [c.get("name") for c in P.as_list(ident.get("containers")) if isinstance(c, dict) and c.get("name")]
        if names:
            d["containers"] = sorted(set(d.get("containers", []) + names))
        hosts = [did_of(x) for x in P.as_list(ident.get("executedOn"))]
        d["executedOn"] = sorted(set(d.get("executedOn", []) + [h for h in hosts if h]))
        ev = cred.get("evidence")
        types = [str(t) for e in P.as_list(ev) if isinstance(e, dict)
                 for t in P.as_list(e.get("type")) if t]
        if types:
            d["evidence_types"] = sorted(set(d.get("evidence_types", []) + types))
    return out


# ------------------------------------------------------------- the model

def build_model(path, narrative=None, verify=True, title=None, session=None, step_notes=None):
    data = P.load(path)
    import hashlib
    with open(path, "rb") as fh:
        sha256 = hashlib.sha256(fh.read()).hexdigest()
    m = P.Manifest(data)
    summary = m.summary()
    timeline = m.timeline()

    return {
        "source": {
            "file": os.path.basename(path),
            "generated": datetime.datetime.now(datetime.timezone.utc)
                          .strftime("%Y-%m-%d %H:%M:%SZ"),
            "manifest_version": data.get("version"),
            "verified": verify,
            "sha256": sha256,
        },
        "title": title,
        "session": session,
        "step_notes": step_notes or {},
        # blobs whose CID this skill can hash (BLAKE3); others read "not checked"
        "blob_keys": sorted(k for k in (data.get("blobs") or {}) if P.cid_digest(k) is not None),
        "identities": identity_info(data),
        "headline": headline(timeline, summary),
        "metrics": {
            "statements": summary["total_statements"],
            "statement_types": summary["statement_type_counts"],
            "blobs": summary["total_blobs"],
            "signers": len(summary["signers"]),
            "steps": len(timeline),
            "orphaned_blobs": summary["orphaned_blob_count"],
        },
        "trust": {
            "content": summary["content_verification"],
            "signatures": signature_report(data, verify),
            "statement_integrity": statement_integrity_report(data, verify),
            "attestation": attestation_report(m, timeline, verify),
            "problems": summary["integrity_problems"],
            # The summary.py block, computed once: the HTML trust section and
            # the terminal block both render THIS, so they cannot disagree.
            "verification": _verification(data) if verify else None,
        },
        "participants": participants(m, timeline, summary),
        "rollup": group_steps(m, timeline),
        "steps": [{
            "n": i,
            "statement_id": e["statement_id"],
            "timestamp": e.get("timestamp"),
            "name": _step_name(e),
            "type": _meta(e).get("computation_type"),
            "operatedBy": e.get("operatedBy"),
            "executedOn": e.get("executedOn"),
            "site": _meta(e).get("site"),
            "environment": e.get("environment"),
            "inputs": [{"cid": x["cid"], "label": _label_for(m, x["cid"], x.get("preview")),
                        "preview": x.get("preview")} for x in e.get("inputs") or []],
            "outputs": [{"cid": x["cid"], "label": _label_for(m, x["cid"], x.get("preview")),
                         "preview": x.get("preview")} for x in e.get("outputs") or []],
        } for i, e in enumerate(timeline, 1)],
        "narrative": narrative,
    }


# ------------------------------------------------------------- rendering

E = html.escape

CSS = """
:root{
  --bg:#f5f6f4; --fg:#1c1f1d; --muted:#6b716c; --rule:#e3e6e1; --panel:#ffffff; --soft:#f3f4f2;
  --ok:#1f7a4d; --ok-bg:#e3f3ea; --warn:#8a5a00; --warn-bg:#fbefd6;
  --bad:#a41f1f; --bad-bg:#fbe3e1; --unk:#5a5f6b; --unk-bg:#eceef1;
  --accent:#2f6f5e; --accent-soft:#e6f0ec; --in:#b7791f; --out:#2f6f5e;
  --hw:#7a4fb5; --hw-bg:#f1ebf9; --hero1:#1d2b26; --hero2:#2f4a40;
  --c1:#2f6f5e; --c2:#d08c2c; --c3:#5b7fb8; --c4:#8a6fc2; --c5:#c4584f; --c6:#4f9fa8; --c7:#9aa29b;
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#121513; --fg:#e7ebe8; --muted:#9aa29b; --rule:#2b312d; --panel:#1b1f1c; --soft:#161a17;
    --ok:#7fd4a8; --ok-bg:#183326; --warn:#f0c071; --warn-bg:#3a2c12;
    --bad:#f2a19b; --bad-bg:#3d1b18; --unk:#a8adb8; --unk-bg:#23262b;
    --accent:#6cc2a6; --accent-soft:#1f2e28; --hw:#b796e6; --hw-bg:#261f33;
  }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font:15px/1.55 ui-sans-serif,-apple-system,"Segoe UI",Helvetica,Arial,sans-serif;}
.wrap{max-width:920px;margin:0 auto;padding:24px 16px 64px}
h2{font-size:20px;margin:0 0 6px}
h3{font-size:15px;margin:22px 0 8px}
p{margin:0 0 12px}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.88em}
pre{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.82em;line-height:1.45;
  background:var(--soft);border:1px solid var(--rule);border-radius:6px;padding:.7rem .9rem;overflow-x:auto;white-space:pre}
.card{background:var(--panel);border:1px solid var(--rule);border-radius:14px;padding:26px;margin-top:18px}
.sub{color:var(--muted);margin:0 0 16px}
/* ---- header ---- */
.hero{background:linear-gradient(135deg,var(--hero1),var(--hero2));color:#fff;border-radius:16px;padding:30px 28px}
.eyebrow{letter-spacing:.14em;font-size:12px;opacity:.7;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.hero h1{font-size:28px;line-height:1.2;margin:10px 0 12px;letter-spacing:-.01em}
.hero p{opacity:.88;margin:0 0 6px}
.hero code{background:rgba(255,255,255,.12);padding:0 4px;border-radius:4px}
.strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(118px,1fr));margin-top:20px;
  border:1px solid rgba(255,255,255,.18);border-radius:10px;overflow:hidden}
.strip div{padding:12px 14px;border-right:1px solid rgba(255,255,255,.18)}
.strip div:last-child{border-right:0}
.strip b{display:block;font-size:21px;font-weight:650;letter-spacing:-.01em}
.strip span{display:block;font-size:11px;letter-spacing:.08em;text-transform:uppercase;opacity:.75;margin-top:2px}
.strip .flag b{color:#f6c87a}
.pills{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0 0}
.gen{color:var(--muted);font-size:12.5px;margin:10px 0 0}
/* ---- pills ---- */
.pill{display:inline-flex;align-items:center;gap:6px;padding:4px 11px;border-radius:999px;font-size:12.5px;font-weight:500}
.pill.sm{font-size:11px;padding:1px 8px;font-weight:600}
.ok{background:var(--ok-bg);color:var(--ok)} .warn{background:var(--warn-bg);color:var(--warn)}
.bad{background:var(--bad-bg);color:var(--bad)} .unk{background:var(--unk-bg);color:var(--unk)}
/* ---- tables / verification ---- */
table{width:100%;border-collapse:collapse;margin:8px 0 16px;font-size:14px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--rule);vertical-align:top}
th{font-size:11.5px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:600}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
.note{border-left:3px solid var(--rule);padding:2px 0 2px 14px;margin:0 0 14px;color:var(--muted);font-size:13.5px}
.empty{border:1px dashed var(--rule);border-radius:8px;padding:16px;color:var(--muted);font-size:13.5px;background:var(--soft)}
.narr p:last-child{margin-bottom:0}
ul{margin:0 0 12px;padding-left:22px}
.did{word-break:break-all;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;color:var(--muted)}
.bar{display:flex;height:14px;border-radius:7px;overflow:hidden;background:var(--unk-bg);margin:6px 0}
.bar span{display:block;height:100%;min-width:3px}
.bar .ok,.key.ok{background:var(--ok)} .bar .bad,.key.bad{background:var(--bad)}
.bar .warn,.key.warn{background:var(--warn)} .bar .unk,.key.unk{background:var(--unk)}
.legend{color:var(--muted);font-size:12.5px}
.key{display:inline-block;width:9px;height:9px;border-radius:2px;margin:0 5px 0 0}
.issue{margin:18px 0 4px}
.plain{display:grid;grid-template-columns:150px 1fr;gap:4px 12px;font-size:13.5px;margin:6px 0 8px}
.plain b{color:var(--muted);font-weight:600;font-size:11.5px;letter-spacing:.05em;text-transform:uppercase;padding-top:2px}
ul.ids{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;margin:0 0 8px;overflow-wrap:anywhere;color:var(--muted)}
details{margin:0 0 10px} summary{cursor:pointer;color:var(--accent);font-size:13.5px}
/* ---- timeline ---- */
.tl{position:relative;height:150px;margin:8px 12px 0}
.tl .rail{position:absolute;left:0;right:0;top:74px;height:2px;background:var(--rule)}
.tl .dot{position:absolute;top:65px;width:20px;height:20px;margin-left:-10px;border-radius:50%;background:var(--panel);
  border:2px solid var(--ok);color:var(--ok);font-size:10px;font-weight:700;text-align:center;line-height:16px}
.tl .dot.sm{top:69px;width:12px;height:12px;margin-left:-6px;font-size:0}
.tl .dot.bad{border-color:var(--bad);color:var(--bad);background:var(--bad-bg)}
.tl .dot.warn{border-color:var(--warn);color:var(--warn);background:var(--warn-bg)}
.tl .dot.unk{border-color:var(--unk);color:var(--unk);background:var(--unk-bg)}
.tl .lbl{position:absolute;transform:translateX(-50%);text-align:center;font-size:12px;white-space:nowrap}
.tl .lbl.up{top:8px} .tl .lbl.down{top:94px}
.tl .lbl.l0{transform:none} .tl .lbl.lN{transform:translateX(-100%)}
.tl .lbl small{display:block;color:var(--muted);font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.axis{position:relative;height:20px;margin:0 12px;border-top:1px solid var(--rule);color:var(--muted);font-size:11px;
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.axis span{position:absolute;top:4px;transform:translateX(-50%)}
.axis span:first-child{transform:none} .axis span:last-child{transform:translateX(-100%)}
.tkey{display:flex;gap:16px;flex-wrap:wrap;font-size:12px;color:var(--muted);margin-top:12px}
.tkey i{display:inline-block;width:10px;height:10px;border-radius:50%;border:2px solid;margin-right:5px;vertical-align:-1px}
.callout{background:var(--accent-soft);border-radius:10px;padding:12px 14px;margin-top:14px;font-size:14px}
.phase{border:1px solid var(--rule);border-radius:8px;background:var(--soft);padding:12px 14px;margin:8px 0}
.phase h3{margin:0 0 4px;font-size:14px;display:flex;justify-content:space-between;gap:12px}
.phase h3 em{font-style:normal;color:var(--muted);font-size:12px;font-weight:400}
.chain{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px;color:var(--accent);margin:0;word-break:break-word}
/* ---- step cards ---- */
.steps{position:relative}
.steps::before{content:"";position:absolute;left:19px;top:10px;bottom:10px;width:2px;background:var(--rule)}
.step{display:flex;gap:16px;margin-bottom:14px;position:relative;break-inside:avoid}
.snum{flex:0 0 40px;height:40px;border-radius:50%;border:2px solid var(--ok);background:var(--panel);color:var(--ok);
  font-weight:700;font-size:14px;display:flex;align-items:center;justify-content:center;z-index:1}
.snum.n-bad{border-color:var(--bad);color:var(--bad);background:var(--panel)}
.snum.n-warn{border-color:var(--warn);color:var(--warn);background:var(--panel)}
.snum.n-unk{border-color:var(--unk);color:var(--unk);background:var(--panel)}
.sbody{flex:1;min-width:0;border:1px solid var(--rule);border-radius:12px;padding:14px 16px;background:var(--soft)}
.sbody.s-bad{border-left:4px solid var(--bad)} .sbody.s-warn{border-left:4px solid var(--warn)}
.shead{display:flex;flex-wrap:wrap;gap:8px;align-items:baseline}
.shead b{font-size:16px;word-break:break-word}
.shead .t{color:var(--muted);font-size:12px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.tag{margin-left:auto;border:1px solid var(--rule);border-radius:99px;padding:1px 10px;font-size:11px;color:var(--muted);
  background:var(--panel);font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.sbody p.what{margin:6px 0 8px;color:var(--muted)}
.env{font-size:12px;margin:8px 0 12px;display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.env .did{font-size:11px}
.io{display:grid;grid-template-columns:1fr auto 1fr;gap:10px}
.io h4{margin:0 0 6px;font-size:11px;letter-spacing:.1em;color:var(--muted)}
.chip{background:var(--panel);border:1px solid var(--rule);border-radius:8px;padding:9px 12px;font-weight:600;font-size:14px;
  margin-bottom:6px;word-break:break-word}
.chip .cid{display:flex;gap:8px;flex-wrap:wrap;font-weight:400;font-size:10.5px;color:var(--muted);margin-top:2px;
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.chip .h-ok{color:var(--ok)} .chip .h-bad{color:var(--bad);font-weight:700} .chip .h-warn{color:var(--warn)} .chip .h-unk{color:var(--unk)}
.in .chip{border-left:4px solid var(--in)} .out .chip{border-left:4px solid var(--out)}
.arrow{align-self:center;color:var(--muted);font-size:20px;padding-top:16px}
.more{font-size:12px;color:var(--muted)}
/* ---- composition donut ---- */
.comp{display:flex;gap:28px;align-items:center;flex-wrap:wrap}
.clegend{flex:1;min-width:240px}
.clegend div{display:flex;align-items:center;gap:10px;padding:7px 0;border-bottom:1px solid var(--rule);font-size:14px}
.clegend i{width:12px;height:12px;border-radius:3px;flex:0 0 12px}
.clegend small{color:var(--muted);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px}
.clegend span{margin-left:auto;color:var(--muted);font-variant-numeric:tabular-nums}
/* ---- identities ---- */
.grp{font-size:13px;font-weight:700;margin:20px 0 8px}
.stylekey{display:flex;gap:10px;flex-wrap:wrap;font-size:12px;color:var(--muted);margin-bottom:6px}
.stylekey span{border-radius:6px;padding:2px 8px}
.id{border:1px solid var(--rule);border-radius:12px;padding:14px 16px;margin-bottom:10px;background:var(--soft);break-inside:avoid}
.id.hw-ok{border:2px solid var(--hw);background:var(--hw-bg)}
.id.hw-claim{border:2px dashed var(--warn);background:var(--warn-bg)}
.id.missing{border:2px solid var(--bad);background:var(--bad-bg)}
.id .top{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.id .cnt{margin-left:auto;font-size:12px;color:var(--muted)}
.id .did{margin:3px 0 8px}
.id p{margin:6px 0 0;font-size:13px}
.ev{display:inline-block;font-size:11px;border:1px solid currentColor;border-radius:6px;padding:1px 7px;margin:2px 4px 0 0;
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.ev.ok,.ev.warn,.ev.bad,.ev.unk{background:none}
/* ---- provenance ---- */
.prov{display:grid;grid-template-columns:170px 1fr;gap:6px 14px;font-size:13px}
.prov b{color:var(--muted);font-weight:600}
.prov span{word-break:break-all;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
footer{margin-top:24px;color:var(--muted);font-size:12.5px;text-align:center}
@media (max-width:640px){
  .io{grid-template-columns:1fr} .arrow{transform:rotate(90deg);justify-self:center;padding:0}
  .hero h1{font-size:23px} .strip div{border-right:0;border-bottom:1px solid rgba(255,255,255,.18)}
  .plain,.prov{grid-template-columns:1fr} .card{padding:18px}
}
@media print{
  body{background:#fff} .wrap{max-width:none;padding:0}
  .card,.hero,.step,.id{break-inside:avoid-page} .card.long{break-inside:auto}
  .hero{-webkit-print-color-adjust:exact;print-color-adjust:exact}
  details>*{display:block} details>summary{display:none}
}
"""


def _pill(cls, text):
    return '<span class="pill %s">%s</span>' % (cls, E(text))


# Plain-language reason per integrity_check() issue, most severe first; the
# order is also the display order in the problems table.
PROBLEM_REASONS = {
    "cid_mismatch": ("bad", "hash mismatch — content altered"),
    "invalid_base64": ("warn", "broken encoding — cannot be hashed"),
    "missing_blob": ("warn", "missing — referenced, not embedded"),
    "by_reference": ("unk", "by reference — declared, not embedded"),
    "content_verification_unavailable": ("unk", "not checked"),
}


def _content_row(c):
    """The denominator is every blob present OR referenced, so a blob deleted
    from the store shows as a gap (N/M) rather than vanishing (N/N).

    Each failure reason is named separately because they mean different
    things: a hash mismatch is content altered after registration; a missing
    blob is content the manifest names but does not carry; bad base64 is
    content present but unreadable, so it cannot be hashed at all."""
    st = c.get("status")
    mismatched, missing = len(c.get("mismatched") or []), len(c.get("missing") or [])
    by_ref = len(c.get("by_reference") or {})
    bad_b64 = len(c.get("invalid_base64") or [])
    unverifiable = c.get("unverifiable", 0)
    if st == "verified":
        total = c.get("total", c["matched"] + mismatched + unverifiable)
        notes = []
        if mismatched:
            notes.append("%d hash mismatch (content altered)" % mismatched)
        if missing:
            notes.append("%d referenced but missing (not embedded)" % missing)
        if bad_b64:
            notes.append("%d invalid base64 (cannot be hashed)" % bad_b64)
        if unverifiable - bad_b64:
            notes.append("%d with an undecodable CID (not checked)" % (unverifiable - bad_b64))
        cls = "bad" if mismatched else "warn" if notes else "ok"
        if by_ref:  # declared by the signer: listed, but not a warning on its own
            notes.append("%d by reference (declared, not embedded)" % by_ref)
        text = "%d/%d blobs match their CID" % (c["matched"], total)
        return cls, text + (" — " + ", ".join(notes) if notes else "")
    text = "not checked — %s" % (c.get("detail") or st)
    if missing:
        text += "; %d referenced but missing" % missing
    if by_ref:
        text += "; %d by reference" % by_ref
    return "unk", text


def _sig_row(s):
    st = s.get("status")
    if st == "run":
        if s["total"] == 0:
            return "unk", "no credentials in this manifest"
        if s["invalid"]:
            return "bad", "%d of %d signatures FAILED" % (s["invalid"], s["total"])
        if s["inconclusive"]:
            return "warn", "%d/%d verified, %d could not be checked" % (
                s["valid"], s["total"], s["inconclusive"])
        return "ok", "%d/%d signatures verified offline" % (s["valid"], s["total"])
    if st == "skipped":
        return "unk", "not checked (--no-verify)"
    return "unk", "not checked — %s" % _short(s.get("detail", st), 90)


def _att_row(a):
    ev, claimed = a.get("evidence") or {}, a["steps_claiming_environment"]
    if claimed == 0:
        return "unk", "no step claims an execution environment"
    st = ev.get("status")
    if st == "run":
        bits = ["%d step(s) claim an environment" % claimed]
        if ev.get("verified"):
            bits.append("%d evidence blob(s) verified against pinned vendor roots" % ev["verified"])
        if ev.get("inconclusive"):
            bits.append("%d inconclusive" % ev["inconclusive"])
        if ev.get("failed"):
            return "bad", "; ".join(bits + ["%d FAILED" % ev["failed"]])
        return ("warn" if ev.get("inconclusive") or not ev.get("verified") else "ok"), "; ".join(bits)
    return "unk", "%d step(s) claim an environment; evidence not checked" % claimed


# ------------------------------------------- verification summary (HTML)
# The same blocks summary.render_text prints, from the same dict. Wording that
# carries meaning (the drift note, the key-binding gap) comes from summary.py
# so the terminal and the page say it identically.

SEV = {"failed": ("bad", "failed"), "unchecked": ("warn", "not checked"),
       "missing": ("warn", "missing")}
STATE = {True: ("ok", "verified"), False: ("bad", "FAILED"), None: ("unk", "not checked")}
HTML_LIMIT = 10


def _tally_cls(t):
    return "bad" if t["failed"] else "warn" if t["verified"] < t["total"] else "ok"


def _summary_pills(v):
    """Header pills read from the summary, so they match the section below."""
    import summary as SM
    stmt = [t for name, t in v["rows"] if name not in ("IdentityAttestation", "Sigstore attestation")]
    st = {k: sum(t[k] for t in stmt) for k in ("verified", "failed", "total")}
    cr = {k: sum(t[k] for _, t in v["signers"]) for k in ("verified", "failed", "total")}
    hw = v["hardware"]["items"]
    hw_bad = sum(1 for h in hw if h["signature"] is False or h["chain"] is False or h.get("key_binding") is False)
    links_bad = sum(1 for l in v["executed_on"] if not l["present"])
    env_cls = "bad" if hw_bad or links_bad else "warn" if any(
        h["signature"] is None or h["chain"] is None for h in hw) else "ok" if hw else "unk"
    unbound = sum(1 for h in hw if h.get("key_binding") is not True)
    env = ("%d of %d hardware reports failed" % (hw_bad, len(hw)) if hw_bad else
           "%d hardware report(s), key binding %s" % (len(hw), "verified" if not unbound else
           "not checked" if unbound == len(hw) else "not checked for %d" % unbound) if hw else
           "no hardware evidence")
    if links_bad:
        env += "; %d executedOn link(s) name no system" % links_bad
    return [("statements", _tally_cls(st), "%d/%d verified" % (st["verified"], st["total"])),
            ("credentials", _tally_cls(cr), "%d/%d valid" % (cr["verified"], cr["total"])),
            ("content",) + _hashes_pill(v["content"]),
            ("execution environment", env_cls, env)]


def _hashes_pill(c):
    """Block (4)'s numbers: verified out of the hashes that have a pre-image."""
    if c.get("status") != "verified":
        return _content_row(c)
    by_ref = len(c.get("by_reference") or {})
    attached = c["total"] - len(c["missing"]) - by_ref
    notes = [x for x in [c["mismatched"] and "%d tampered" % len(c["mismatched"]),
                         c.get("invalid_base64") and "%d invalid base64" % len(c["invalid_base64"]),
                         c["missing"] and "%d missing pre-image" % len(c["missing"])] if x]
    cls = "bad" if c["mismatched"] or c.get("invalid_base64") else "warn" if notes else "ok"
    if by_ref:
        notes.append("%d by reference" % by_ref)
    return cls, "%d/%d verified%s" % (c["matched"], attached, " — " + ", ".join(notes) if notes else "")


def _ids_html(lines):
    """First HTML_LIMIT lines visible, the rest behind a native <details>."""
    li = lambda xs: "".join("<li>%s</li>" % E(x) for x in xs)
    out = '<ul class="ids">%s</ul>' % li(lines[:HTML_LIMIT])
    if len(lines) > HTML_LIMIT:
        out += ('<details><summary>%d more</summary><ul class="ids">%s</ul></details>'
                % (len(lines) - HTML_LIMIT, li(lines[HTML_LIMIT:])))
    return out


def _hash_bar(c):
    """Stacked bar over every hash: verified / tampered / invalid base64 /
    missing / not checked. Plain CSS, no script, themed by the page tokens."""
    bad64 = len(c.get("invalid_base64") or [])
    parts = [("ok", "verified", c["matched"]), ("bad", "tampered", len(c["mismatched"])),
             ("bad", "invalid base64", bad64), ("warn", "missing pre-image", len(c["missing"])),
             ("unk", "by reference", len(c.get("by_reference") or {})),
             ("unk", "not checked", c["unverifiable"] - bad64)]
    parts = [p for p in parts if p[2]]
    total = c["total"] or 1
    bar = "".join('<span class="%s" style="width:%.3f%%" title="%d %s"></span>'
                  % (cls, 100.0 * n / total, n, E(name)) for cls, name, n in parts)
    legend = " · ".join('<span class="key %s"></span>%d %s' % (cls, n, E(name)) for cls, name, n in parts)
    return ('<div class="bar" role="img" aria-label="%s">%s</div><p class="legend">%s</p>'
            % (E(", ".join("%d %s" % (n, name) for _, name, n in parts)), bar, legend))


def _verification_html(v, pills=None):
    import summary as SM
    h = []
    a = h.append
    a("<h2>Verification summary</h2>")
    if pills:
        a('<div class="pills" style="margin:4px 0 16px">%s</div>' % "".join(
            _pill(cls, "%s: %s" % (name, text)) for name, cls, text in pills))
    a('<p class="note">The same block <code>summary.py</code> prints, computed once from this '
      'manifest. Checks fail independently and are never merged into one verdict; a check '
      'that could not run says <em>not checked</em>, never passes.</p>')

    # (1) per statement type
    a('<div class="scroll"><table><thead><tr><th>Statement type</th><th class="num">Verified</th>'
      '<th class="num">Total</th><th>Result</th></tr></thead><tbody>')
    for name, t in v["rows"]:
        note = "all verified" if t["verified"] == t["total"] else ", ".join(x for x in [
            t["failed"] and "%d failed" % t["failed"], t["unchecked"] and "%d not checked" % t["unchecked"],
            t["missing"] and "%d without a credential" % t["missing"]] if x)
        a("<tr><td>%s</td><td class='num'>%d</td><td class='num'>%d</td><td>%s</td></tr>"
          % (E(name), t["verified"], t["total"], _pill(_tally_cls(t), note)))
    a("</tbody></table></div>")

    # (2) one group per kind of problem
    if v["issues"]:
        a("<h3>Problems</h3>")
        groups = OrderedDict()
        for sev, kind, text in v["issues"]:
            groups.setdefault(kind, (sev, []))[1].append(text)
        for kind, (sev, lines) in groups.items():
            cls, word = SEV[sev]
            a('<p class="issue">%s <strong>%s</strong> · %d</p>' % (_pill(cls, word), E(kind), len(lines)))
            if kind in PROBLEM_HELP:
                means, settles = PROBLEM_HELP[kind]
                a('<div class="plain"><b>What it means</b><span>%s</span><b>What would settle it</b><span>%s</span></div>'
                  % (E(means), E(settles)))
            if kind == SM.DRIFT:
                n = len(lines)
                a('<details><summary>%d registration%s not hash to %s @id; %s</summary>'
                  '<ul class="ids">%s</ul></details>' % (
                      n, " does" if n == 1 else "s do", "its" if n == 1 else "their",
                      "its credential verifies" if n == 1 else "each one's credential verifies",
                      "".join("<li>%s</li>" % E(x) for x in lines)))
                a('<p class="note">%s</p>' % E(SM.DRIFT_NOTE))
            else:
                a(_ids_html(lines))
    else:
        a('<p>%s</p>' % _pill("ok", "no problems found"))

    # (3) signers
    a("<h3>Signers</h3>")
    a('<div class="scroll"><table><thead><tr><th>Signing DID</th><th class="num">Valid</th>'
      '<th class="num">Total</th><th>Result</th></tr></thead><tbody>')
    for did, t in v["signers"]:
        note = "all valid" if t["verified"] == t["total"] else ", ".join(x for x in [
            t["failed"] and "%d failed" % t["failed"], t["unchecked"] and "%d not checked" % t["unchecked"]] if x)
        a("<tr><td class='did'>%s</td><td class='num'>%d</td><td class='num'>%d</td><td>%s</td></tr>"
          % (E(did), t["verified"], t["total"], _pill(_tally_cls(t), note)))
    a("</tbody></table></div>")
    ia = v["issuer_agreement"]
    if ia["total"]:
        a("<p>%s</p>" % _pill("ok" if ia["agree"] == ia["total"] else "bad",
                              "%d/%d credentials: issuer is the registration's signer" % (ia["agree"], ia["total"])))

    # (4) hashes
    c = v["content"]
    a("<h3>Hashes</h3>")
    if c.get("status") != "verified":
        a("<p>%s</p>" % _pill("unk", "not checked — %s" % c.get("detail")))
    else:
        attached = c["total"] - len(c["missing"]) - len(c.get("by_reference") or {})
        a("<p>%d hashes · %d have pre-images attached · <strong>%d / %d verified</strong></p>"
          % (c["total"], attached, c["matched"], attached))
        a(_hash_bar(c))
        urn = lambda xs: ["urn:cid:" + x for x in xs]
        for title, xs in [("Tampering detected", c["mismatched"]),
                          ("Pre-image not valid base64", c.get("invalid_base64") or []),
                          ("Missing pre-image blobs", c["missing"])]:
            if xs:
                a('<p class="issue"><strong>%s</strong> · %d</p>' % (E(title), len(xs)))
                a(_ids_html(urn(xs)))
        br = c.get("by_reference") or {}
        if br:
            a('<p class="issue"><strong>By reference</strong> · %d — %s</p>' % (len(br), E(SM.BY_REFERENCE_NOTE)))
            a(_ids_html(["urn:cid:%s — %s%s%s" % (k, d.get("name") or "unnamed",
                                                 " (%s)" % d["reason"] if d.get("reason") else "",
                                                 "; obtain from: %s" % d["obtain_from"] if d.get("obtain_from") else "")
                         for k, d in sorted(br.items())]))

    # (5) execution environment
    hw, links = v["hardware"], v["executed_on"]
    if hw["items"] or links or hw["status"] != "run":
        a("<h3>Execution environment — evidence</h3>")
        if links:
            ok = sum(1 for l in links if l["present"])
            a("<p>%s</p>" % _pill("ok" if ok == len(links) else "bad",
                                  "%d/%d executedOn links name a system present in this manifest" % (ok, len(links))))
        if hw["status"] != "run":
            a("<p>%s</p>" % _pill("unk", "hardware evidence not checked — %s" % hw["detail"]))
        if hw["items"]:
            a('<div class="scroll"><table><thead><tr><th>Environment</th><th>Report signature</th>'
              '<th>Vendor chain</th><th>Hardware binding</th><th>Key binding</th></tr></thead><tbody>')
            for i in hw["items"]:
                chain = (_pill("ok", "not needed — key bound to verified hardware")
                         if i.get("chain_via_binding") else _pill(*STATE[i["chain"]]))
                binding = _pill(*STATE[i["binding"]]) if i.get("has_binding") else "—"
                cls, txt = STATE[i.get("key_binding")]
                key = _pill(cls, txt + (" — %s" % i["key_binding_via"] if i.get("key_binding_via") else ""))
                if SM.nothing_to_check(i):
                    a("<tr><td class='did'>%s</td><td colspan='3'>%s</td><td>%s</td></tr>" % (
                        E(i["label"]), _pill("unk", "nothing to check — " + SM.nothing_to_check(i)), key))
                    continue
                a("<tr><td class='did'>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                    E(i["label"]), _pill(*STATE[i["signature"]]), chain, binding, key))
            a("</tbody></table></div>")
            if any(i.get("key_binding") is None for i in hw["items"]):
                a('<p class="note">%s</p>' % E(SM.KEY_BINDING_GAP))
    return "\n".join(h)


def _narrative_html(text):
    """Deliberately minimal markdown: paragraphs, headings, bullets, ```fenced
    blocks``` (a lineage narrative is usually describing a graph), `code`, **bold**.
    Anything richer belongs in the manifest, not in this renderer."""
    import re
    out, block = [], []

    def flush():
        if not block:
            return
        if block[0].lstrip().startswith(("- ", "* ")):
            # Only a line that opens with a marker starts a new <li>; every
            # other line is a continuation of the item above it and must be
            # joined, not re-stripped -- slicing [2:] off a continuation line
            # eats two characters of the author's prose, silently.
            items = []
            for b in block:
                if b.lstrip().startswith(("- ", "* ")):
                    items.append(b.lstrip()[2:].strip())
                elif items:
                    items[-1] += " " + b.strip()
                else:
                    items.append(b.strip())
            out.append("<ul>%s</ul>"
                       % "".join("<li>%s</li>" % _inline(i) for i in items))
        elif block[0].lstrip().startswith("#"):
            level = len(block[0]) - len(block[0].lstrip("#"))
            text = " ".join(block).lstrip("#").strip()
            out.append("<h%d>%s</h%d>" % (min(level + 2, 6), _inline(text),
                                          min(level + 2, 6)))
        else:
            out.append("<p>%s</p>" % _inline(" ".join(block)))
        block.clear()

    def _inline(s):
        s = E(s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
        return s

    fence, fenced = False, []
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            if fence:
                out.append("<pre>%s</pre>" % E("\n".join(fenced)))
                fenced = []
            else:
                flush()
            fence = not fence
        elif fence:
            fenced.append(line)
        elif not line.strip():
            flush()
        elif line.lstrip().startswith(("- ", "* ")) and block and \
                not block[0].lstrip().startswith(("- ", "* ")):
            flush()
            block.append(line)
        elif line.lstrip().startswith("#"):
            flush()
            block.append(line.strip())
            flush()
        else:
            block.append(line if line.lstrip().startswith(("- ", "* "))
                         else line.strip())
    if fenced:                      # unterminated fence: keep the content
        out.append("<pre>%s</pre>" % E("\n".join(fenced)))
    flush()
    return "\n".join(out)


def _legacy_trust(a, tr, rows):
    """The per-check table, kept for --no-verify, where the summary (which
    needs signatures) is not computed."""
    a("<h2>Trust — four independent checks</h2>")
    a('<p class="note">These four checks fail independently, so they are reported '
      'separately and never merged into a single verdict. A green row says nothing '
      'about the other three, and a check that could not run is shown as '
      '<em>not checked</em> rather than omitted.</p>')
    a('<div class="scroll"><table><thead><tr><th>Check</th><th>What it proves</th>'
      "<th>Result</th></tr></thead><tbody>")
    proves = [
        ("Content addresses",
         "the bytes in the file still hash to the CIDs naming them — catches a payload "
         "edited after signing, which leaves every signature intact"),
        ("Credential signatures",
         "each statement was signed by the key it names, checked offline against "
         "bundled JSON-LD contexts"),
        # `rows` above carries FOUR results; this list carried three names, and
        # zip() silently dropped the last pair -- which rendered the statement
        # integrity result under the "Execution environment" label and left
        # attestation out of the table entirely. A misattributed trust number is
        # the one thing this report exists not to do.
        ("Statement integrity",
         "each statement still hashes to its own <code>@id</code> — catches a "
         "credential lifted verbatim onto a different statement, which keeps a "
         "valid signature"),
        ("Execution environment",
         "what environment each step <em>claims</em>, and separately whether its TEE "
         "evidence verifies against pinned vendor roots"),
    ]
    assert len(rows) == len(proves), (
        "%d trust results and %d row labels: zip() would silently drop a check "
        "or mislabel one" % (len(rows), len(proves)))
    for (cls, text), (name, why) in zip(rows, proves):
        a("<tr><td><strong>%s</strong></td><td>%s</td><td>%s</td></tr>"
          % (E(name), why, _pill(cls, text)))
    a("</tbody></table></div>")

    s = tr["signatures"]
    if s.get("status") == "run" and s.get("total"):
        detail = []
        if s.get("key_types"):
            detail.append("Key types: " + ", ".join("%s (%d)" % (k, v)
                          for k, v in sorted(s["key_types"].items())))
        if s.get("context_sources"):
            detail.append("JSON-LD contexts resolved from: " + ", ".join(
                "%s (%d)" % (k, v) for k, v in sorted(s["context_sources"].items())))
        if s.get("reasons"):
            detail.append("Unverifiable reasons: " + ", ".join(
                "%s (%d)" % (k, v) for k, v in sorted(s["reasons"].items())))
        if detail:
            a("<p>%s</p>" % "<br>".join(E(d) for d in detail))

# ------------------------------------------------------- page sections
# Layout: header (title, authored session sentence, computed manifest
# sentence, stat strip, separate pills) -> what this run was -> verification
# summary -> horizontal timeline -> step cards (inputs/outputs emphasised) ->
# statement composition donut -> signing identities (hardware first; the
# emphasis follows the verification result) -> glossary (only terms the page
# uses) -> provenance.
#
# Every state colour on the page is read from the summary dict / content check
# computed above. Nothing here decides a trust result of its own: it only
# joins those results onto steps, CIDs and DIDs.

FRIENDLY_TYPES = {
    "CredentialRegistration": "Credential seals",
    "MetadataRegistration": "Metadata registrations",
    "DataRegistration": "Data registrations",
    "ComputationRegistration": "Computation registrations",
    "DidRegistration": "DID registrations",
}
DONUT_COLORS = ["var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)", "var(--c6)", "var(--c7)"]
STEP_CARD_LIMIT = 40          # cards beyond this sit behind a <details>
CHIP_LIMIT = 6                # inputs / outputs shown per side before "+N more"


def _did(v):
    if isinstance(v, dict):
        v = v.get("id")
    return v.split("#")[0] if isinstance(v, str) else None


def _date(ts):
    dt = _parse_ts(ts)
    return dt.strftime("%b ") + str(dt.day) + dt.strftime(", %Y") if dt else None


def _executed_date(start, end):
    a, b = _parse_ts(start), _parse_ts(end)
    if not a:
        return "—"
    if b and b.date() != a.date():
        if (a.year, a.month) == (b.year, b.month):
            return "%s %d–%d, %d" % (a.strftime("%b"), a.day, b.day, a.year)
        return "%s – %s" % (_date(start), _date(end))
    return _date(start)


def _elapsed(secs):
    if secs < 120:
        return "%ds" % round(secs)
    if secs < 7200:
        return "%gm" % round(secs / 60, 1)
    return "%gh" % round(secs / 3600, 1)


def _join_state(mo):
    """Join the computed trust results onto CIDs, DIDs and steps."""
    tr = mo["trust"]
    v = tr.get("verification")
    c = tr["content"] or {}
    checked = c.get("status") == "verified"
    tampered = set(c.get("mismatched") or [])
    bad64 = set(c.get("invalid_base64") or [])
    missing = set(c.get("missing") or [])
    byref = set((c.get("by_reference") or {}).keys())

    def cid_state(cid):
        k = P.strip_urn(cid)
        if k in tampered:
            return "bad", "✗ tampered"
        if k in bad64:
            return "bad", "✗ invalid base64"
        if k in missing:
            return "warn", "missing pre-image"
        if k in byref:
            return "unk", "by reference"
        if not checked:
            return "unk", "not checked"
        if k not in mo["blob_keys"]:
            return "unk", "not checked"
        return "ok", "✓ hash verified"

    links = (v or {}).get("executed_on") or []
    present = {}
    for l in links:
        present[l["did"]] = present.get(l["did"], True) and l["present"]

    hw = {}
    for i in ((v or {}).get("hardware") or {}).get("items") or []:
        d = _did(i.get("did"))
        if not (d and d.startswith("did:")):
            d = next((t for t in str(i.get("label", "")).split() if t.startswith("did:")), None)
        if d:
            hw.setdefault(d, []).append(i)

    def hw_state(did):
        items = hw.get(did) or []
        if not items:
            return "none", "no hardware evidence in this manifest"
        if any(i["signature"] is False or i["chain"] is False or i.get("key_binding") is False
               or (i.get("has_binding") and i.get("binding") is False) for i in items):
            return "bad", "hardware check FAILED"
        if all(i["signature"] is True and (i["chain"] is True or i.get("chain_via_binding")) for i in items):
            return "ok", "hardware evidence verified"
        if all(i.get("nothing_checked") for i in items):
            return "warn", "hardware evidence not in manifest — nothing to check"
        return "warn", "hardware claimed, not verified"

    sig = (tr.get("signatures") or {})
    by_subject = sig.get("by_subject") or {}
    sig_ran = sig.get("status") == "run"

    states = {}
    for st in mo["steps"]:
        did = _did(st.get("executedOn"))
        link = present.get(did) if (v and did) else None
        io = [cid_state(x["cid"])[0] for x in st["inputs"] + st["outputs"]]
        s = by_subject.get(st["statement_id"]) if sig_ran else None
        reasons = []
        if s is False:
            reasons.append("signature failed")
        if link is False:
            reasons.append("executedOn names a system not in this manifest")
        if "bad" in io:
            reasons.append("an input or output fails its hash")
        if reasons:
            cls = "bad"
        elif not v:
            cls = "unk"
            reasons.append("verification not run")
        elif (sig_ran and s is None) or "warn" in io:
            cls = "warn"
            if sig_ran and s is None:
                reasons.append("no credential names this step directly")
            if "warn" in io:
                reasons.append("an input or output has no pre-image")
        else:
            cls = "ok"
        states[st["n"]] = {"cls": cls, "reasons": reasons, "did": did, "link": link}
    return {"cid_state": cid_state, "present": present, "hw": hw, "hw_state": hw_state,
            "states": states, "links": links, "verified": bool(v)}


def _hero_html(mo, J):
    src, hl, me, tr = mo["source"], mo["headline"], mo["metrics"], mo["trust"]
    h = []
    a = h.append
    title = mo.get("title") or hl["kind"]
    a('<header class="hero">')
    a('<div class="eyebrow">VERIFIABLE EXECUTION REPORT</div>')
    a("<h1>%s</h1>" % E(title))
    if mo.get("session"):
        a("<p>%s</p>" % E(mo["session"]))
    # The second sentence is about the manifest itself, and is computed.
    stimes = sorted(s["timestamp"] for s in mo["steps"] if s.get("timestamp"))
    rng = [stimes[0], stimes[-1]] if stimes else [hl["start"], hl["end"]]
    wall = _span(rng[0], rng[1]) if rng[0] else None
    same_day = bool(_parse_ts(rng[0]) and _parse_ts(rng[1]) and _parse_ts(rng[0]).date() == _parse_ts(rng[1]).date())
    fmt = (lambda t: _clock(t)) if same_day else (lambda t: "%s %s" % (_date(t), _clock(t)))
    span = (" between %s and %s" % (E(fmt(rng[0])), E(fmt(rng[1])))) if rng[0] and rng[1] and rng[0] != rng[1] else ""
    a("<p>This report reads <code>%s</code>, a signed lineage manifest of %d statements and %d "
      "content-addressed blobs from %d signing identit%s. It records %d computation step%s%s%s, "
      "and every figure below is computed from that file.</p>" % (
          E(src["file"]), me["statements"], me["blobs"], me["signers"],
          "y" if me["signers"] == 1 else "ies", me["steps"], "" if me["steps"] == 1 else "s", span,
          (" (%s)" % E(wall)) if wall and span else ""))
    a('<div class="strip">')
    a("<div><b>%s</b><span>executed on</span></div>" % E(_executed_date(rng[0], rng[1])))
    for label, val in [("statements", me["statements"]), ("blobs", me["blobs"]),
                       ("signers", me["signers"]), ("steps", me["steps"])]:
        a("<div><b>%s</b><span>%s</span></div>" % (val, E(label)))
    a('<div class="%s"><b>%s</b><span>orphaned blobs</span></div>' % (
        "flag" if me["orphaned_blobs"] else "", me["orphaned_blobs"]))
    a("</div></header>")
    return "\n".join(h)


def _timeline_html(mo, J):
    steps = mo["steps"]
    n = len(steps)
    h = ['<section class="card"><h2>Timeline</h2>']
    a = h.append
    if not n:
        a('<p class="empty">This manifest records no ComputationRegistration, so there is no timeline.</p></section>')
        return "\n".join(h)
    times = [_parse_ts(s["timestamp"]) for s in steps]
    t0 = next((t for t in times if t), None)
    secs = [((t - t0).total_seconds() if (t and t0) else None) for t in times]
    known = [s for s in secs if s is not None]
    span = max(known) if known else 0
    raw = []
    for i, s in enumerate(secs):
        if span and s is not None:
            raw.append(100.0 * s / span)
        else:
            raw.append(100.0 * i / (n - 1) if n > 1 else 50.0)
    # Steps that share a second would sit on top of each other: keep a
    # minimum gap, then rescale. Order is preserved; spacing stays roughly
    # proportional to elapsed time.
    gap = min(3.2, 100.0 / max(n - 1, 1))
    pos = raw[:]
    for i in range(1, n):
        pos[i] = max(pos[i], pos[i - 1] + gap)
    if n > 1 and pos[-1] > 100:
        lo = pos[0]
        pos = [lo + (p - lo) * (100 - lo) / (pos[-1] - lo) for p in pos]
    st = J["states"]
    counts = Counter(st[s["n"]]["cls"] for s in steps)
    a('<p class="sub">%d step%s over %s. Dot colour is each step\'s verification state.</p>' % (
        n, "" if n == 1 else "s", _span(steps[0]["timestamp"], steps[-1]["timestamp"]) or "an unknown span"))
    a('<div class="tl"><div class="rail"></div>')
    small = n > 40
    for s, p in zip(steps, pos):
        a('<div class="dot %s%s" style="left:%.2f%%" title="%d · %s · %s">%s</div>' % (
            st[s["n"]]["cls"] if st[s["n"]]["cls"] != "ok" else "", " sm" if small else "", p, s["n"],
            E(s["name"]), E(_clock(s["timestamp"])), "" if small else s["n"]))
    # Labels: first, last, then the earliest non-green steps, kept apart.
    picks = [0, n - 1] + [i for i, s in enumerate(steps) if st[s["n"]]["cls"] in ("bad", "warn")]
    if n > 2:
        picks += [round((n - 1) * k / 3) for k in (1, 2)]
    chosen = []
    for i in picks:
        if i not in chosen and all(abs(pos[i] - pos[j]) >= 16 for j in chosen) and len(chosen) < 5:
            chosen.append(i)
    for k, i in enumerate(sorted(chosen, key=lambda i: pos[i])):
        s = steps[i]
        edge = " l0" if pos[i] < 8 else " lN" if pos[i] > 92 else ""
        a('<div class="lbl %s%s" style="left:%.2f%%"><small>%s</small>%d · %s</div>' % (
            "up" if k % 2 == 0 else "down", edge, pos[i], E(_clock(s["timestamp"])), s["n"], E(s["name"][:28])))
    a("</div>")
    if span:
        a('<div class="axis">%s</div>' % "".join(
            '<span style="left:%g%%">%s</span>' % (q * 25, _elapsed(span * q / 4)) for q in range(5)))
    a('<div class="tkey"><span><i style="border-color:var(--ok)"></i>all checks verified</span>'
      '<span><i style="border-color:var(--warn)"></i>verified with gaps</span>'
      '<span><i style="border-color:var(--bad)"></i>a check failed</span>'
      '<span><i style="border-color:var(--unk)"></i>not checked</span></div>')
    if counts.get("bad") or counts.get("warn"):
        why = Counter(r for s in steps for r in st[s["n"]]["reasons"] if st[s["n"]]["cls"] in ("bad", "warn"))
        a('<div class="callout">%s</div>' % E("; ".join(
            "%d step%s: %s" % (c, "" if c == 1 else "s", r) for r, c in why.most_common()) + "."))
    ru = mo["rollup"]
    if len(ru["phases"]) > 1:
        a('<details style="margin-top:14px"><summary>%d phases, grouped by %s</summary>' % (
            len(ru["phases"]), "the manifest's own <code>%s</code> field" % E(ru["grouping_key"])
            if ru["grouping_key"] else "contiguous timestamps (derived, not stated)"))
        for ph in ru["phases"]:
            a('<div class="phase"><h3><span>%s</span><em>%s · %d step%s</em></h3><p class="chain">%s</p></div>' % (
                E(ph["label"]), E(_clock(ph["start"])), ph["step_count"],
                "" if ph["step_count"] == 1 else "s", E(ph["chain"])))
        a("</details>")
    a("</section>")
    return "\n".join(h)


def _chip(J, x):
    cls, word = J["cid_state"](x["cid"])
    return ('<div class="chip">%s<span class="cid"><span>%s</span><span class="h-%s">%s</span></span></div>'
            % (E(x["label"]), E(_short(P.strip_urn(x["cid"]), 16)), cls, E(word)))


def _chips(J, xs):
    if not xs:
        return '<div class="more">none recorded</div>'
    out = "".join(_chip(J, x) for x in xs[:CHIP_LIMIT])
    if len(xs) > CHIP_LIMIT:
        out += '<div class="more">+%d more</div>' % (len(xs) - CHIP_LIMIT)
    return out


def _env_line(mo, J, st):
    s = J["states"][st["n"]]
    did = s["did"]
    if not did:
        return '<div class="env">%s</div>' % _pill("unk", "no executedOn claimed")
    name = J["names"].get(did) or _short(did, 22)
    if not J["verified"]:
        return '<div class="env">%s<span class="did">→ %s</span></div>' % (
            _pill("unk", "executedOn not checked (--no-verify)"), E(name))
    if s["link"] is False:
        return '<div class="env">%s<span class="did">→ %s (not in this manifest)</span></div>' % (
            _pill("bad", "executedOn unresolved"), E(_short(did, 22)))
    hcls, htxt = J["hw_state"](did)
    return '<div class="env">%s%s<span class="did">→ %s</span></div>' % (
        _pill("ok", "executedOn resolved"), _pill("unk" if hcls == "none" else hcls, htxt), E(name))


def _steps_html(mo, J):
    steps = mo["steps"]
    h = ['<section class="card long"><h2>Compute steps</h2>']
    a = h.append
    if not steps:
        a('<p class="empty">No computation steps recorded.</p></section>')
        return "\n".join(h)
    a('<p class="sub">Each step is its own signed <code>ComputationRegistration</code>. Inputs on the left, '
      'outputs on the right; each one carries its own content-address result.</p>')
    notes = mo.get("step_notes") or {}
    prev = None
    cards = []
    for st in steps:
        s = J["states"][st["n"]]
        t = _parse_ts(st["timestamp"])
        delta = (" · +%s" % _elapsed((t - prev).total_seconds())) if (t and prev) else ""
        prev = t or prev
        tag = " · ".join(x for x in [st.get("site"), st.get("type")] if x)
        what = notes.get(st["statement_id"]) or notes.get(str(st["n"]))
        c = []
        c.append('<div class="step"><div class="snum n-%s">%d</div><div class="sbody %s">' % (
            s["cls"] if s["cls"] != "ok" else "", st["n"], ("s-" + s["cls"]) if s["cls"] in ("bad", "warn") else ""))
        c.append('<div class="shead"><b>%s</b><span class="t">%s%s</span>%s</div>' % (
            E(st["name"]), E(_clock(st["timestamp"])), E(delta),
            ('<span class="tag">%s</span>' % E(tag)) if tag else ""))
        if what:
            c.append('<p class="what">%s</p>' % E(what))
        c.append(_env_line(mo, J, st))
        if s["cls"] in ("bad", "warn") and s["reasons"]:
            c.append('<div class="env">%s</div>' % "".join(_pill(s["cls"], r) for r in s["reasons"]
                                                           if "executedOn" not in r))
        c.append('<div class="io"><div class="in"><h4>CONSUMED</h4>%s</div><div class="arrow">→</div>'
                 '<div class="out"><h4>PRODUCED</h4>%s</div></div>' % (_chips(J, st["inputs"]), _chips(J, st["outputs"])))
        c.append("</div></div>")
        cards.append("".join(c))
    a('<div class="steps">%s</div>' % "".join(cards[:STEP_CARD_LIMIT]))
    if len(cards) > STEP_CARD_LIMIT:
        a('<details><summary>%d more steps</summary><div class="steps">%s</div></details>' % (
            len(cards) - STEP_CARD_LIMIT, "".join(cards[STEP_CARD_LIMIT:])))
    a("</section>")
    return "\n".join(h)


def _composition_html(mo):
    counts = sorted(mo["metrics"]["statement_types"].items(), key=lambda kv: (-kv[1], kv[0]))
    total = sum(v for _, v in counts) or 1
    h = ['<section class="card"><h2>Statement composition</h2>',
         '<p class="sub">How the %d signed statements break down by type.</p>' % total,
         '<div class="comp"><svg width="190" height="190" viewBox="0 0 42 42" role="img" aria-label="%s">' % E(
             ", ".join("%d %s" % (v, k) for k, v in counts))]
    a = h.append
    cum = 0.0
    for i, (k, v) in enumerate(counts):
        pct = 100.0 * v / total
        # circumference 100 at r=15.915; offset 25 starts the ring at 12 o'clock
        a('<circle cx="21" cy="21" r="15.915" fill="none" stroke="%s" stroke-width="6" '
          'stroke-dasharray="%.3f %.3f" stroke-dashoffset="%.3f"><title>%d %s</title></circle>' % (
              DONUT_COLORS[min(i, len(DONUT_COLORS) - 1)], pct, 100 - pct, 25 - cum, v, E(k)))
        cum += pct
    a('<text x="21" y="21.5" text-anchor="middle" font-size="7" font-weight="700" fill="currentColor">%d</text>' % total)
    a('<text x="21" y="26.5" text-anchor="middle" font-size="2.6" fill="currentColor" opacity=".6">statements</text></svg>')
    a('<div class="clegend">')
    for i, (k, v) in enumerate(counts):
        a('<div><i style="background:%s"></i>%s <small>%s</small><span>%d</span></div>' % (
            DONUT_COLORS[min(i, len(DONUT_COLORS) - 1)], E(FRIENDLY_TYPES.get(k, k)),
            E(k) if k in FRIENDLY_TYPES else "", v))
    a("</div></div></section>")
    return "\n".join(h)


def _identity_desc(info):
    bits = []
    if info.get("pod"):
        bits.append("Pod <code>%s</code>%s" % (E(info["pod"].get("name") or "?"),
                    (", namespace <code>%s</code>" % E(info["pod"]["namespace"])) if info["pod"].get("namespace") else ""))
    if info.get("containers"):
        bits.append("container%s %s" % ("" if len(info["containers"]) == 1 else "s",
                                         ", ".join("<code>%s</code>" % E(c) for c in info["containers"])))
    return ". ".join(bits)


def _identities_html(mo, J):
    ids = mo["identities"]
    parts = {p["did"]: p for p in mo["participants"] if p.get("did")}
    v = mo["trust"].get("verification")
    tally = {d: t for d, t in (v or {}).get("signers", [])}
    refs = Counter(l["did"] for l in J["links"])
    missing = {d for d, ok in J["present"].items() if not ok}
    targets = set(refs)
    systems = (set(J["hw"]) | {d for d, i in ids.items() if i.get("registered") or i.get("evidence_types")}
               | missing | (targets & set(ids)))
    everyone = set(ids) | set(parts) | {d for d in tally if str(d).startswith("did:")}
    operators = {s.get("operatedBy") for s in mo["steps"] if s.get("operatedBy")}
    workloads = sorted(everyone - systems, key=lambda d: -(parts.get(d) or {}).get("statements", 0))
    op_only = sorted(operators - everyone - systems)

    h = ['<section class="card"><h2>Signing identities</h2>',
         '<p class="sub">Hardware-rooted identities first. The solid purple style appears only when the '
         'hardware evidence actually verified; a claim the verifier could not check is never styled as verified.</p>',
         '<div class="stylekey"><span style="border:2px solid var(--hw);background:var(--hw-bg)">hardware verified</span>'
         '<span style="border:2px dashed var(--warn);background:var(--warn-bg)">claimed, not verified</span>'
         '<span style="border:2px solid var(--bad);background:var(--bad-bg)">failed, or referenced but not in manifest</span></div>']
    a = h.append

    def counts(d):
        p = parts.get(d) or {}
        t = tally.get(d)
        out = "%d statement%s" % (p.get("statements", 0), "" if p.get("statements", 0) == 1 else "s")
        if refs.get(d):
            out += " · %d executedOn reference%s" % (refs[d], "" if refs[d] == 1 else "s")
        return out, t

    def cred_pill(t):
        if not t:
            return ""
        if t["verified"] == t["total"]:
            return _pill("ok", "%d/%d credentials valid" % (t["verified"], t["total"]))
        return _pill(_tally_cls(t), "%d/%d credentials valid" % (t["verified"], t["total"]))

    if systems:
        a('<div class="grp">Hardware-rooted / host identities</div>')
    for d in sorted(systems, key=lambda d: (d not in missing, -(refs.get(d) or 0))):
        info = ids.get(d) or {}
        cnt, t = counts(d)
        title = " + ".join(info.get("types") or []) or "System"
        if d in missing and not info and d not in J["hw"]:
            a('<div class="id missing"><div class="top"><b>%s</b>%s<span class="cnt">%s</span></div>'
              '<div class="did">%s</div><p>Named as <code>executedOn</code> %d time%s, but this manifest gives it '
              'no identity: no DID registration, no identity attestation, no hardware evidence. Nothing about '
              'this system can be checked from this file.</p></div>' % (
                  "Unidentified system", _pill("bad", "not in manifest"), E(cnt), E(d),
                  refs.get(d, 0), "" if refs.get(d, 0) == 1 else "s"))
            continue
        hcls, htxt = J["hw_state"](d) if J["verified"] else ("none", "hardware not checked (--no-verify)")
        box = {"ok": "hw-ok", "warn": "hw-claim", "bad": "missing"}.get(hcls, "")
        a('<div class="id %s"><div class="top"><b>%s</b>%s%s<span class="cnt">%s</span></div><div class="did">%s</div>' % (
            box, E(title), _pill("unk" if hcls == "none" else hcls, htxt), cred_pill(t), E(cnt), E(d)))
        for i in J["hw"].get(d, []):
            if SM_nothing(i):
                a('<span class="ev warn">%s · nothing to check — %s</span>' % (E(i.get("format") or "evidence"), E(SM_nothing(i))))
                continue
            st = lambda x: {True: "verified", False: "FAILED", None: "not checked"}[x]
            worst = ("bad" if False in (i["signature"], i["chain"], i.get("key_binding")) else
                     "ok" if i["signature"] is True and (i["chain"] is True or i.get("chain_via_binding")) else "warn")
            a('<span class="ev %s">%s · signature %s · chain %s · key %s</span>' % (
                worst, E(i.get("format") or "evidence"), st(i["signature"]),
                "via binding" if i.get("chain_via_binding") else st(i["chain"]), st(i.get("key_binding"))))
        desc = _identity_desc(info)
        if desc:
            a("<p>%s.</p>" % desc)
        if d in missing:
            a("<p>Some <code>executedOn</code> links name this DID, but the manifest carries no identity for it.</p>")
        a("</div>")

    if workloads:
        a('<div class="grp">Workload identities</div>')
    for d in workloads:
        info = ids.get(d) or {}
        cnt, t = counts(d)
        p = parts.get(d) or {}
        title = " + ".join(info.get("types") or []) or (p.get("role") or "Signer")
        a('<div class="id"><div class="top"><b>%s</b>%s<span class="cnt">%s</span></div><div class="did">%s</div>' % (
            E(title), cred_pill(t), E(cnt), E(d)))
        desc = _identity_desc(info)
        lines = [desc] if desc else []
        for host in info.get("executedOn") or []:
            hn = J["names"].get(host) or _short(host, 22)
            if not J["verified"]:
                lines.append("Claims to run on <code>%s</code> (not checked)" % E(hn))
            elif J["present"].get(host) is False:
                lines.append("Claims to run on <code>%s</code>, %s" % (E(_short(host, 22)), _pill("bad", "not in this manifest")))
            else:
                hcls, htxt = J["hw_state"](host)
                lines.append("Bound to <code>%s</code> via <code>executedOn</code> %s" % (
                    E(hn), _pill("unk" if hcls == "none" else hcls, htxt)))
        if p.get("steps_operated"):
            lines.append("Operates %d step%s" % (p["steps_operated"], "" if p["steps_operated"] == 1 else "s"))
        if lines:
            a("<p>%s.</p>" % ". ".join(lines))
        a("</div>")

    for d in op_only:
        n = sum(1 for s in mo["steps"] if s.get("operatedBy") == d)
        a('<div class="id" style="border-style:dashed"><div class="top"><b>Operator only</b><span class="cnt">0 statements</span></div>'
          '<div class="did">%s</div><p>Appears as <code>operatedBy</code> on %d step%s but signs nothing in this manifest; '
          'nothing here vouches for it on its own.</p></div>' % (E(d), n, "" if n == 1 else "s"))
    a('<p class="note" style="margin-top:14px">A claimed environment is not a proven one. Even verified hardware '
      'evidence establishes genuine vendor hardware holding the signing key, not which code ran on it: '
      'measurements are not compared with reference values.</p>')
    a("</section>")
    return "\n".join(h)


def SM_nothing(i):
    try:
        import summary as SM
        return SM.nothing_to_check(i)
    except Exception:  # noqa: BLE001
        return None


def _provenance_html(mo):
    src = mo["source"]
    rows = [("Manifest file", src["file"]), ("Manifest SHA-256", src.get("sha256") or "—"),
            ("Manifest version", str(src["manifest_version"])), ("Report generated", src["generated"]),
            ("Generator", "eqty-manifest/report.py"),
            ("Verification", "run at generation time" if src["verified"] else "SKIPPED (--no-verify)"),
            ("Timestamps", "as written by each signer, on their own clocks; shown in the zone each one carries (Z = UTC)"),
            ("Reproduce", "uv run eqty-manifest/summary.py %s" % src["file"])]
    return ('<section class="card"><h2>Provenance</h2><p class="sub">What you need to rerun every check above '
            'independently.</p><div class="prov">%s</div></section>' % "".join(
                "<b>%s</b><span>%s</span>" % (E(k), E(v)) for k, v in rows))


# Glossary: a term is listed only when its trigger pattern appears in the
# visible text of THIS report (computed after every other section renders),
# so a report with no TDX evidence carries no TDX entry. Definitions are
# general, never about any particular manifest.
GLOSSARY = [
    ("CID", r"urn:cid:|\bCIDs?\b|content-address",
     "Content identifier: an address computed from a hash of the data itself. The same bytes always give the same CID; change one byte and the CID changes."),
    ("Blob", r"\bblobs?\b",
     "A piece of content (a prompt, file, model output or piece of evidence) carried in the manifest, base64-encoded and named by its CID."),
    ("Pre-image", r"pre-image",
     "The actual bytes behind a CID. A missing pre-image is a CID the manifest references but does not carry, so its hash cannot be recomputed."),
    ("Orphaned blob", r"[Oo]rphaned",
     "A blob carried in the manifest that no statement refers to, even after following collections."),
    ("By reference", r"by reference",
     "Content the signer declared as committed by CID only. Its bytes were left out on purpose, so it cannot be checked without the original file."),
    ("Statement", r"\bstatements?\b",
     "One signed entry in the manifest, identified by the hash of its own content (its @id)."),
    ("did:key", r"did:key",
     "A self-certifying decentralized identifier: the public key is encoded in the identifier itself, so signatures can be checked offline."),
    ("Verifiable Credential (VC)", r"[Cc]redential",
     "A signed claim in the W3C format. Here it signs a statement or attests an identity; a valid signature shows who signed and that the claim is unchanged."),
    ("Credential seal", r"Credential seals|CredentialRegistration",
     "A CredentialRegistration: the statement that carries a credential signing another statement."),
    ("MetadataRegistration", r"MetadataRegistration|Metadata registrations",
     "Attaches a human-readable label or description to a CID, a statement or an identity."),
    ("DataRegistration", r"DataRegistration|Data registrations",
     "Registers a piece of data, by its CID, as part of the record."),
    ("ComputationRegistration", r"ComputationRegistration|Computation registrations",
     "One compute step: what it consumed, what it produced, who operated it, and where it claims to have run."),
    ("DidRegistration", r"DidRegistration|DID registrations",
     "Registers a DID together with a description of the environment it belongs to."),
    ("IdentityAttestation", r"IdentityAttestation",
     "A credential stating what a DID is (a machine, a confidential VM, a pod), which may carry hardware evidence for it."),
    ("Sigstore attestation", r"Sigstore",
     "A signed in-toto/DSSE bundle about an artifact outside the manifest, checked against the did:key it names."),
    ("executedOn", r"executedOn",
     "The DID of the environment a step or workload claims to have run on. A claim, not proof: only verified hardware evidence for that DID backs it."),
    ("operatedBy", r"operatedBy|[Oo]perator",
     "The DID of the component that ran a step. An operator that signs nothing is vouched for only by whatever attests where it runs."),
    ("Registration drift", r"not hash to (its|their) @id|does not re-hash",
     "A registration whose credential verifies but whose own bytes no longer hash to its @id. From the manifest alone, tampering and drift from an older emitter look the same."),
    ("Not checked", r"not checked|nothing to check",
     "The check could not run (missing evidence, unsupported type, missing dependency). Unknown: never a pass, and not a failure."),
    ("Confidential VM", r"[Cc]onfidential|TDX|IntelTdx|SevSnp|AmdSev|CoCoPod",
     "A virtual machine whose memory the CPU encrypts and isolates from the host and hypervisor, and which can produce signed evidence of its state."),
    ("Intel TDX", r"TDX|IntelTdx|intel-tdx",
     "Intel Trust Domain Extensions: CPU-level VM isolation with per-VM memory encryption and remotely verifiable measurements."),
    ("MRTD / RTMR", r"mrtd|rtmr|MRTD|RTMR",
     "TDX measurement registers: digests of the VM's initial image (MRTD) and of later runtime events (RTMRs)."),
    ("NVIDIA CC", r"NvidiaCc|nvidia-cc|NVIDIA CC|NVIDIA [Cc]onfidential",
     "NVIDIA confidential-computing mode: GPU memory encryption and isolation, with a per-device certificate chain and a signed GPU attestation report."),
    ("SPDM", r"spdm|SPDM",
     "Security Protocol and Data Model: the protocol an NVIDIA GPU uses to produce its signed attestation report."),
    ("AMD SEV-SNP", r"AmdSev|SevSnp|SEV|sev-snp",
     "AMD Secure Encrypted Virtualization with Secure Nested Paging: AMD's confidential-VM technology, with reports signed by a chip-specific key that chains to AMD."),
    ("TPM quote", r"Tpm|TPM",
     "A Trusted Platform Module's signed statement of its measurement registers. Trusted here only when its attestation key is bound to a verified hardware report."),
    ("CCEL", r"ccel|CCEL",
     "Confidential Computing Event Log: the append-only log of measured launch events for a confidential VM."),
    ("Report signature", r"[Rr]eport signature|· signature ",
     "Whether a hardware report is signed by the key its evidence names."),
    ("Vendor chain", r"[Vv]endor chain|· chain ",
     "Whether that key's certificate chain reaches a vendor root (Intel, AMD, NVIDIA) pinned in the verifier."),
    ("Hardware binding", r"[Hh]ardware binding",
     "A key or claim tied to another hardware report that itself verified, e.g. a TPM attestation key bound to an AMD report."),
    ("Key binding", r"[Kk]ey binding|· key ",
     "Whether the signed hardware evidence carries the public key of the DID it vouches for: whether that hardware vouches for that identity."),
    ("Confidential pod", r"CoCoPod",
     "A Kubernetes pod run as its own confidential VM (Confidential Containers). Its identity names its containers and the host it runs on."),
]


def _visible_text(fragment):
    import re
    t = re.sub(r"<style.*?</style>|<title>.*?</title>", " ", fragment, flags=re.S)
    return html.unescape(re.sub(r"<[^>]+>", " ", t))


def _glossary_html(body):
    import re
    text = _visible_text(body)
    rows = [(term, meaning) for term, pat, meaning in GLOSSARY if re.search(pat, text)]
    if not rows:
        return ""
    return ('<section class="card"><h2>Glossary</h2><p class="sub">Only the terms that appear in this report.</p>'
            '<div class="scroll"><table><thead><tr><th>Term</th><th>Meaning here</th></tr></thead><tbody>%s'
            '</tbody></table></div></section>' % "".join(
                "<tr><td class='mono' style='white-space:nowrap'>%s</td><td>%s</td></tr>" % (E(t), E(m)) for t, m in rows))


def render_html(mo):
    src, me, tr = mo["source"], mo["metrics"], mo["trust"]
    J = _join_state(mo)
    J["names"] = {}
    for d, info in (mo.get("identities") or {}).items():
        if info.get("types"):
            J["names"][d] = "%s %s" % (" + ".join(info["types"]), _short(d, 18))
    h = []
    a = h.append
    a("<title>%s — EQTY verifiable execution report</title>" % E(mo.get("title") or src["file"]))
    a('<meta name="viewport" content="width=device-width,initial-scale=1">')
    a("<style>%s</style>" % CSS)
    a('<div class="wrap">')
    a(_hero_html(mo, J))

    vsum = tr.get("verification")
    rows = [_content_row(tr["content"]), _sig_row(tr["signatures"]),
            _stmt_row(tr.get("statement_integrity") or {}), _att_row(tr["attestation"])]
    pills = _summary_pills(vsum) if vsum else [
        (name, cls, text) for (cls, text), name in
        zip(rows, ["content", "signatures", "statement integrity", "attestation"])]

    # --- verification ----------------------------------------------------
    a('<section class="card">')
    if vsum:
        a(_verification_html(vsum, pills))
    else:
        a('<div class="pills">%s</div>' % "".join(_pill(cls, "%s: %s" % (n, t)) for n, cls, t in pills))
        _legacy_trust(a, tr, rows)
    a('<p class="gen">Generated %s from manifest version %s by <code>eqty-manifest/report.py</code>.%s</p>'
      % (E(src["generated"]), E(str(src["manifest_version"])),
         "" if src["verified"] else " <strong>Verification was skipped (--no-verify).</strong>"))
    a("</section>")

    # --- what this run was -----------------------------------------------
    a('<section class="card"><h2>What this run was</h2>')
    if mo.get("narrative"):
        a('<div class="narr">%s</div>' % _narrative_html(mo["narrative"]))
    else:
        a('<p class="empty">No narrative was supplied. Everything else on this page is computed directly '
          'from the manifest; what the run <em>meant</em> is the one thing the script cannot derive — pass it '
          'with <code>--narrative &lt;file&gt;</code>.</p>')
    a("</section>")

    a(_timeline_html(mo, J))
    a(_steps_html(mo, J))
    a(_composition_html(mo))
    a(_identities_html(mo, J))
    a(_glossary_html("\n".join(h)))
    a(_provenance_html(mo))
    a("<footer>Self-contained report: no external assets, no scripts, no network access. Every figure was "
      "computed from <code>%s</code> at generation time. Read it as a technical walkthrough, not a compliance "
      "attestation in itself.</footer>" % E(src["file"]))
    a("</div>")
    return "\n".join(h)



SESSION_OPENERS = ("The session", "This session")
SESSION_MAX_SENTENCES = 2


def session_problem(text):
    """The header's session line: one or two sentences, opening with "The
    session" or "This session", so it reads as the work the run did and never
    as a description of the manifest (the computed sentence after it does that).
    Returns why the text is refused, or None."""
    import re
    t = (text or "").strip()
    if not t.startswith(SESSION_OPENERS):
        return 'must start with "The session" or "This session"'
    # a sentence ends at . ! or ? followed by whitespace and a capital letter;
    # abbreviations like "e.g." or "Inc. and" do not end one
    n = len(re.findall(r"[.!?][\"')\]]*\s+(?=[A-Z])", t)) + 1
    if n > SESSION_MAX_SENTENCES:
        return "must be at most %d sentences (got %d)" % (SESSION_MAX_SENTENCES, n)
    return None


def main():
    ap = argparse.ArgumentParser(
        description="Render an EQTY lineage manifest as a self-contained HTML report.")
    ap.add_argument("manifest")
    ap.add_argument("out", help="path to write the .html report to")
    ap.add_argument("--narrative", metavar="FILE",
                    help="markdown/plain-text prose for the 'What this run was' section, "
                         "authored by the agent that interpreted the manifest")
    ap.add_argument("--title", metavar="TEXT",
                    help="headline for the report; defaults to the run kind the manifest's metadata implies")
    ap.add_argument("--session", metavar="TEXT",
                    help="one or two sentences on what the session did, starting 'The session' or "
                         "'This session', every word backed by the manifest; "
                         "the second header sentence (about the manifest itself) is computed")
    ap.add_argument("--step-notes", metavar="FILE",
                    help="JSON object mapping a step's statement id (or its 1-based number) to one "
                         "manifest-backed line saying what that step does")
    ap.add_argument("--no-verify", action="store_true",
                    help="skip signature and attestation-evidence verification; the report "
                         "then says the checks were skipped rather than implying they passed")
    ap.add_argument("--json", metavar="FILE",
                    help="also write the report model as JSON (report-shaped, unlike "
                         "parse_manifest.py's export debug dump)")
    args = ap.parse_args()

    narrative = None
    if args.narrative:
        with open(args.narrative) as f:
            narrative = f.read()

    if args.session is not None:
        problem = session_problem(args.session)
        if problem:
            ap.error("--session %s" % problem)

    step_notes = None
    if args.step_notes:
        with open(args.step_notes) as f:
            step_notes = {str(k): str(v) for k, v in json.load(f).items()}

    model = build_model(args.manifest, narrative, verify=not args.no_verify,
                        title=args.title, session=args.session, step_notes=step_notes)
    with open(args.out, "w") as f:
        f.write(render_html(model))
    if args.json:
        with open(args.json, "w") as f:
            json.dump(model, f, indent=2, default=str)

    print("Wrote %s" % args.out)
    if args.json:
        print("  model JSON:   %s" % args.json)
    if args.no_verify:
        t = model["trust"]
        print("  content:      %s" % _content_row(t["content"])[1])
        print("  signatures:   %s" % _sig_row(t["signatures"])[1])
        print("  statements:   %s" % _stmt_row(t.get("statement_integrity") or {})[1])
        print("  attestation:  %s" % _att_row(t["attestation"])[1])
    else:
        # The same block summary.py prints, so the terminal and a later
        # `summary.py` run can never disagree. Its exit code stays there:
        # this script's job is writing the report, and it did.
        import summary
        print()
        print(summary.render_text(model["trust"]["verification"]))


if __name__ == "__main__":
    main()
