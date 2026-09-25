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


# ------------------------------------------------------------- the model

def build_model(path, narrative=None, verify=True):
    data = P.load(path)
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
        },
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
  --bg:#fbfbf9; --fg:#1c1b19; --muted:#6b6862; --rule:#e2e0da; --panel:#ffffff;
  --ok:#1f7a4d; --ok-bg:#e8f5ee; --warn:#8a5a00; --warn-bg:#fdf3e0;
  --bad:#a41f1f; --bad-bg:#fdecec; --unk:#5a5f6b; --unk-bg:#eef0f3;
  --accent:#2c4a7c;
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#16171a; --fg:#e7e5e1; --muted:#9b978f; --rule:#2e3034; --panel:#1d1f23;
    --ok:#63c795; --ok-bg:#16301f; --warn:#e0b060; --warn-bg:#332616;
    --bad:#f08a8a; --bad-bg:#341a1a; --unk:#a8adb8; --unk-bg:#23262b;
    --accent:#8fb3e8;
  }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font:15px/1.6 ui-sans-serif,-apple-system,"Segoe UI",Helvetica,Arial,sans-serif;}
.wrap{max-width:920px;margin:0 auto;padding:40px 24px 96px}
h1{font-size:26px;margin:0 0 4px;letter-spacing:-.01em}
h2{font-size:19px;margin:44px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--rule)}
h3{font-size:15px;margin:26px 0 8px}
p{margin:0 0 12px}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.88em}
pre{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.82em;
  line-height:1.45;background:var(--panel);border:1px solid var(--rule);border-radius:6px;
  padding:.7rem .9rem;overflow-x:auto;white-space:pre}
.sub{color:var(--muted);margin:0 0 2px}
.gen{color:var(--muted);font-size:12.5px;margin:14px 0 0}
.strip{display:flex;flex-wrap:wrap;gap:0;margin:22px 0 0;border:1px solid var(--rule);
  border-radius:8px;background:var(--panel);overflow:hidden}
.strip div{flex:1 1 110px;padding:12px 14px;border-right:1px solid var(--rule)}
.strip div:last-child{border-right:0}
.strip b{display:block;font-size:20px;font-weight:600;letter-spacing:-.02em}
.strip span{display:block;color:var(--muted);font-size:11.5px;text-transform:uppercase;
  letter-spacing:.06em;margin-top:2px}
.pills{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0 0}
.pill{display:inline-flex;align-items:center;gap:6px;padding:5px 11px;border-radius:999px;
  font-size:12.5px;font-weight:500}
.ok{background:var(--ok-bg);color:var(--ok)} .warn{background:var(--warn-bg);color:var(--warn)}
.bad{background:var(--bad-bg);color:var(--bad)} .unk{background:var(--unk-bg);color:var(--unk)}
table{width:100%;border-collapse:collapse;margin:8px 0 16px;font-size:14px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--rule);vertical-align:top}
th{font-size:11.5px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:600}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
.phase{border:1px solid var(--rule);border-radius:8px;background:var(--panel);
  padding:14px 16px;margin:0 0 12px}
.phase h3{margin:0 0 6px;display:flex;justify-content:space-between;gap:12px;align-items:baseline}
.phase h3 em{font-style:normal;color:var(--muted);font-size:12.5px;font-weight:400}
.chain{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px;
  color:var(--accent);margin:0 0 8px;word-break:break-word}
.made{margin:0;color:var(--muted);font-size:13px}
.made b{color:var(--fg);font-weight:500}
.note{border-left:3px solid var(--rule);padding:2px 0 2px 14px;margin:0 0 14px;
  color:var(--muted);font-size:13.5px}
.empty{border:1px dashed var(--rule);border-radius:8px;padding:16px;color:var(--muted);
  font-size:13.5px;background:var(--panel)}
.narr p:last-child{margin-bottom:0}
ul{margin:0 0 12px;padding-left:22px}
.did{word-break:break-all;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  font-size:12px;color:var(--muted)}
.bar{display:flex;height:14px;border-radius:7px;overflow:hidden;background:var(--unk-bg);
  margin:6px 0 6px}
.bar span{display:block;height:100%;min-width:3px}
.bar .ok,.key.ok{background:var(--ok)} .bar .bad,.key.bad{background:var(--bad)}
.bar .warn,.key.warn{background:var(--warn)} .bar .unk,.key.unk{background:var(--unk)}
.legend{color:var(--muted);font-size:12.5px}
.key{display:inline-block;width:9px;height:9px;border-radius:2px;margin:0 5px 0 0}
.issue{margin:14px 0 4px}
ul.ids{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;
  margin:0 0 8px;overflow-wrap:anywhere}
details{margin:0 0 10px} summary{cursor:pointer;color:var(--accent);font-size:13.5px}
footer{margin-top:48px;padding-top:14px;border-top:1px solid var(--rule);
  color:var(--muted);font-size:12.5px}
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


def _verification_html(v):
    import summary as SM
    h = []
    a = h.append
    a("<h2>Verification summary</h2>")
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

def render_html(mo):
    src, hl, me, tr = mo["source"], mo["headline"], mo["metrics"], mo["trust"]
    h = []
    a = h.append

    subtitle = " · ".join(x for x in [hl["kind"], hl["framework"],
                                      hl["wall_clock"] and hl["wall_clock"] + " wall clock"] if x)
    a("<title>%s — EQTY lineage report</title>" % E(src["file"]))
    a("<style>%s</style>" % CSS)
    a('<div class="wrap">')
    a("<h1>%s</h1>" % E(src["file"]))
    a('<p class="sub">%s</p>' % E(subtitle))

    a('<div class="strip">')
    for label, val in [("statements", me["statements"]), ("blobs", me["blobs"]),
                       ("signers", me["signers"]), ("steps", me["steps"]),
                       ("orphaned blobs", me["orphaned_blobs"])]:
        a("<div><b>%s</b><span>%s</span></div>" % (val, E(label)))
    a("</div>")

    vsum = tr.get("verification")
    rows = [_content_row(tr["content"]), _sig_row(tr["signatures"]),
            _stmt_row(tr.get("statement_integrity") or {}), _att_row(tr["attestation"])]
    pills = _summary_pills(vsum) if vsum else [
        (name, cls, text) for (cls, text), name in
        zip(rows, ["content", "signatures", "statement integrity", "attestation"])]
    a('<div class="pills">')
    for name, cls, text in pills:
        a(_pill(cls, "%s: %s" % (name, text)))
    a("</div>")
    a('<p class="gen">Generated %s from manifest version %s by <code>eqty-manifest/report.py</code>.%s</p>'
      % (E(src["generated"]), E(str(src["manifest_version"])),
         "" if src["verified"] else " <strong>Verification was skipped (--no-verify).</strong>"))

    # --- what this run was ------------------------------------------------
    a("<h2>What this run was</h2>")
    if mo.get("narrative"):
        a('<div class="narr">%s</div>' % _narrative_html(mo["narrative"]))
    else:
        a('<p class="empty">No narrative was supplied. Everything below is computed '
          'directly from the manifest; the interpretation of <em>what the run meant</em> '
          'is the one thing this script cannot derive — pass it with '
          '<code>--narrative &lt;file&gt;</code>.</p>')
    a("<table><tbody>")
    for k, v in [("Kind", hl["kind"]), ("Framework", hl["framework"]),
                 ("Started", hl["start"]), ("Ended", hl["end"]),
                 ("Wall clock", hl["wall_clock"]),
                 ("Statement types", ", ".join("%d %s" % (v2, k2) for k2, v2 in
                                               sorted(me["statement_types"].items(),
                                                      key=lambda kv: -kv[1])))]:
        if v:
            a("<tr><th>%s</th><td>%s</td></tr>" % (E(k), E(str(v))))
    a("</tbody></table>")

    # --- trust ------------------------------------------------------------
    if vsum:
        a(_verification_html(vsum))
    else:
        _legacy_trust(a, tr, rows)

    att = tr["attestation"]
    a("<h3>Execution environments — claims</h3>" if vsum else "<h3>Execution environments</h3>")
    if att["steps_claiming_environment"]:
        a("<p>%d of %d steps name an environment (%s). %d name none.</p>" % (
            att["steps_claiming_environment"], att["steps_total"],
            E(", ".join("%s × %d" % (k, v) for k, v in sorted(att["claimed_types"].items())
                        if k)) or "type unstated", att["steps_without_claim"]))
        a('<p class="note">A claimed environment is not a proven one. Even where TEE '
          'evidence verifies, that establishes authentic vendor hardware and an intact '
          'report — <strong>not</strong> which code ran, since reference measurements '
          'are not checked.</p>')
    else:
        a('<p class="empty">No step in this manifest declares an <code>executedOn</code> '
          'environment. That is not the same as running somewhere untrusted, and it is '
          'not the same as attested: the manifest simply asserts nothing about where '
          'these steps ran.</p>')

    if tr["problems"] and not vsum:
        a("<h3>Integrity problems</h3><div class='scroll'><table><thead><tr><th>Reason</th>"
          "<th>Issue</th><th>Subject</th><th>Detail</th></tr></thead><tbody>")
        order = list(PROBLEM_REASONS)
        for p in sorted(tr["problems"], key=lambda p: order.index(p.get("issue"))
                        if p.get("issue") in order else len(order)):
            cls, reason = PROBLEM_REASONS.get(p.get("issue"), ("unk", "other"))
            a("<tr><td>%s</td><td><code>%s</code></td><td class='did'>%s</td><td>%s</td></tr>" % (
                _pill(cls, reason), E(str(p.get("issue"))), E(str(p.get("cid") or "—")),
                E(str(p.get("detail") or ""))))
        a("</tbody></table></div>")

    # --- participants -----------------------------------------------------
    a("<h2>Participants</h2>")
    a('<div class="scroll"><table><thead><tr><th>Role</th><th>Identity</th>'
      "<th class='num'>Statements</th><th class='num'>Steps</th><th>Signed</th>"
      "</tr></thead><tbody>")
    for p in mo["participants"]:
        a("<tr><td>%s</td><td class='did'>%s%s</td><td class='num'>%d</td>"
          "<td class='num'>%d</td><td>%s</td></tr>" % (
              E(p["role"] or "signer"), E(str(p["did"])),
              " <em>(has entity metadata)</em>" if p["has_entity_metadata"] else "",
              p["statements"], p["steps_operated"],
              E(", ".join(p["step_names"]) or "—")))
    a("</tbody></table></div>")
    if len(mo["participants"]) == 1:
        a('<p class="note">A single key signed every statement, so this manifest carries '
          'no separation of signing responsibility — one compromised key would account '
          'for all of it.</p>')

    # --- how the run unfolded --------------------------------------------
    ru = mo["rollup"]
    a("<h2>How the run unfolded</h2>")
    a('<p class="note">%d step%s grouped into %d phase%s by %s.</p>' % (
        me["steps"], "" if me["steps"] == 1 else "s",
        len(ru["phases"]), "" if len(ru["phases"]) == 1 else "s", ru["basis"]))
    for ph in ru["phases"]:
        a('<div class="phase"><h3><span>%s</span><em>%s · %d step%s%s</em></h3>' % (
            E(ph["label"]), E(_clock(ph["start"])), ph["step_count"],
            "" if ph["step_count"] == 1 else "s",
            " · " + E(ph["duration"]) if ph["duration"] and ph["duration"] != "<1 s" else ""))
        a('<p class="chain">%s</p>' % E(ph["chain"]))
        if ph["operators"]:
            a('<p class="made">Run by <b>%s</b></p>' % E(", ".join(ph["operators"])))
        if ph["produced"]:
            shown = ph["produced"][:6]
            a('<p class="made">Produced <b>%s</b>%s</p>' % (
                "</b>, <b>".join(E(x) for x in shown),
                " and %d more" % (len(ph["produced"]) - 6) if len(ph["produced"]) > 6 else ""))
        a("</div>")

    # --- appendix ---------------------------------------------------------
    a("<h2>Appendix — every step</h2>")
    a('<div class="scroll"><table><thead><tr><th class="num">#</th><th>Time</th><th>Step</th>'
      "<th>Type</th><th>Operator</th><th>Environment</th><th>Signed</th>"
      "</tr></thead><tbody>")
    by_subject = (tr["signatures"].get("by_subject") or {})
    for st in mo["steps"]:
        env = st["environment"] or {}
        envtxt = (env.get("type") or "claimed, type unstated") if env.get("environment_claimed") \
            else "none claimed"
        sig = by_subject.get(st["statement_id"])
        sigtxt = {True: "✓", False: "✗ FAILED", None: "—"}.get(sig, "—")
        a("<tr><td class='num'>%d</td><td>%s</td><td><strong>%s</strong></td><td>%s</td>"
          "<td>%s</td><td>%s</td><td>%s</td></tr>" % (
              st["n"], E(_clock(st["timestamp"])), E(st["name"]), E(st["type"] or "—"),
              E(st["site"] or _short(st["operatedBy"], 12) or "—"), E(envtxt), sigtxt))
    a("</tbody></table></div>")
    a('<p class="note">The <em>Signed</em> column reports the credential whose '
      '<code>credentialSubject.id</code> is that step. A dash means no credential '
      'names this statement directly — not that a check failed.</p>')

    a("<h3>Inputs and outputs</h3>")
    a('<div class="scroll"><table><thead><tr><th class="num">#</th><th>Step</th>'
      "<th>In</th><th>Out</th></tr></thead><tbody>")
    for st in mo["steps"]:
        a("<tr><td class='num'>%d</td><td><strong>%s</strong></td><td>%s</td><td>%s</td></tr>" % (
            st["n"], E(st["name"]),
            "<br>".join(E(x["label"]) for x in st["inputs"]) or "—",
            "<br>".join(E(x["label"]) for x in st["outputs"]) or "—"))
    a("</tbody></table></div>")

    a("<footer>Self-contained report — no external assets, no scripts, no network access. "
      "Every figure above was computed from <code>%s</code> at generation time; "
      "nothing is transcribed from a conversation.</footer>" % E(src["file"]))
    a("</div>")
    return "\n".join(h)


def main():
    ap = argparse.ArgumentParser(
        description="Render an EQTY lineage manifest as a self-contained HTML report.")
    ap.add_argument("manifest")
    ap.add_argument("out", help="path to write the .html report to")
    ap.add_argument("--narrative", metavar="FILE",
                    help="markdown/plain-text prose for the 'What this run was' section, "
                         "authored by the agent that interpreted the manifest")
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

    model = build_model(args.manifest, narrative, verify=not args.no_verify)
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
