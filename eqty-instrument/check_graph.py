#!/usr/bin/env python3
"""check_graph.py — the Mode 2 step-6 checks, as a script.

Prints every node as a reader sees it, then sorts findings into two tiers,
following `eqty-instrument/references/mode2.md`:

- **checks** fail the run: a computation with no `computation_type`, and a
  computation count or component count that differs from the prediction written
  down before the run.
- **reported** items never fail: generated names, `Custom` types, extra labels,
  path-text content, multiple producers and cycles. They are honest records of
  code that was not restructured to avoid them; the patch lists each one as a gap.

This is the write side's post-run check. `eqty-manifest/parse_manifest.py` still
owns statement counts, blob integrity and orphans; nothing here duplicates it.

    python3 eqty-instrument/check_graph.py <manifest.json> [--expect-computations N] [--expect-components N]

Exit code is 0 when every check passes, whatever is reported.
"""

from __future__ import annotations

import base64
import json
import re
import sys

ANON = re.compile(r"[A-Za-z_]+-[0-9a-z]{4}\Z")


def load(path):
    with open(path) as f:
        return json.load(f)


def blob_bytes(manifest, cid):
    raw = manifest["blobs"].get(str(cid).replace("urn:cid:", ""))
    if raw is None:
        return None
    try:
        return base64.b64decode(raw)
    except Exception:
        return None


def build(manifest):
    """Return (nodes, computations, edges).

    nodes: cid -> {"names": set, "type": str|None, "kind": "data"|"compute"}
    computations: statement id -> {"inputs": [cid], "outputs": [cid], "meta": dict}
    """
    statements = manifest["statements"]
    nodes, computations = {}, {}

    def node(cid, kind):
        return nodes.setdefault(cid, {"names": set(), "type": None, "kind": kind})

    for sid, s in statements.items():
        if s.get("@type") == "ComputationRegistration":
            inputs = s.get("input") or []
            outputs = s.get("output") or []
            inputs = inputs if isinstance(inputs, list) else [inputs]
            outputs = outputs if isinstance(outputs, list) else [outputs]
            computations[sid] = {"inputs": inputs, "outputs": outputs, "meta": {}}
            node(sid, "compute")
            for cid in inputs + outputs:
                node(cid, "data")

    for sid, s in statements.items():
        if s.get("@type") != "MetadataRegistration":
            continue
        meta = json.loads(blob_bytes(manifest, s["metadata"]) or b"{}")
        subject = s["subject"]
        n = node(subject, "compute" if subject in computations else "data")
        if meta.get("name"):
            n["names"].add(meta["name"])
        if meta.get("assetType"):
            n["type"] = meta["assetType"]
        if subject in computations:
            computations[subject]["meta"].update(meta)

    return nodes, computations


def analyse(manifest):
    nodes, computations = build(manifest)

    succ = {cid: set() for cid in nodes}
    pred = {cid: set() for cid in nodes}
    for sid, c in computations.items():
        for cid in c["inputs"]:
            succ[cid].add(sid)
            pred[sid].add(cid)
        for cid in c["outputs"]:
            succ[sid].add(cid)
            pred[cid].add(sid)

    # connected components, undirected
    seen, components = set(), []
    for start in nodes:
        if start in seen:
            continue
        stack, group = [start], []
        seen.add(start)
        while stack:
            cur = stack.pop()
            group.append(cur)
            for nxt in succ[cur] | pred[cur]:
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        components.append(group)

    # cycles, directed (iterative DFS with colours)
    WHITE, GREY, BLACK = 0, 1, 2
    colour = dict.fromkeys(nodes, WHITE)
    cycles = []
    for start in nodes:
        if colour[start] != WHITE:
            continue
        stack = [(start, iter(sorted(succ[start])))]
        colour[start] = GREY
        path = [start]
        while stack:
            cur, children = stack[-1]
            nxt = next(children, None)
            if nxt is None:
                colour[cur] = BLACK
                stack.pop()
                path.pop()
                continue
            if colour[nxt] == GREY:
                cycles.append(path[path.index(nxt):] + [nxt])
            elif colour[nxt] == WHITE:
                colour[nxt] = GREY
                path.append(nxt)
                stack.append((nxt, iter(sorted(succ[nxt]))))

    produced_by = {}
    for sid, c in computations.items():
        for cid in c["outputs"]:
            produced_by.setdefault(cid, []).append(sid)

    data = {c: n for c, n in nodes.items() if n["kind"] == "data"}
    return {
        "nodes": nodes,
        "data": data,
        "computations": computations,
        "components": components,
        "cycles": cycles,
        "roots": sorted(c for c in data if not pred[c]),
        "leaves": sorted(c for c in data if not succ[c]),
        "multi_producer": {c: v for c, v in produced_by.items() if len(v) > 1},
    }


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    path = sys.argv[1]
    def flag(name):
        return int(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else None
    expect = flag("--expect-computations")
    expect_components = flag("--expect-components")

    manifest = load(path)
    a = analyse(manifest)
    nodes, data, comps = a["nodes"], a["data"], a["computations"]

    print(f"== {path}")
    print(f"{len(comps)} computations, {len(data)} data nodes\n")

    print("-- every node as a reader sees it")
    for cid, n in sorted(nodes.items(), key=lambda kv: (kv[1]["kind"], kv[1]["type"] or "", sorted(kv[1]["names"]))):
        kind = n["type"] or ("COMPUTE" if n["kind"] == "compute" else "!! no type")
        content = blob_bytes(manifest, cid) or b""
        print(f"  {kind:16} {', '.join(sorted(n['names'])) or '!! unnamed':34} {content[:44]!r}")

    failures = []

    anonymous = [c for c, n in data.items() if any(ANON.match(x) for x in n["names"])]
    unnamed = [c for c, n in data.items() if not n["names"]]
    custom = [c for c, n in data.items() if n["type"] in (None, "Custom")]
    multilabel = [c for c, n in nodes.items() if len(n["names"]) > 1]
    pathish = [c for c, n in data.items() if (blob_bytes(manifest, c) or b"").startswith(b"/")]
    untyped_compute = [s for s, c in comps.items() if not c["meta"].get("computation_type")]

    reported = [
        ("generated data node names (Custom-a1b2)", anonymous),
        ("unnamed data nodes", unnamed),
        ("Custom / untyped data nodes", custom),
        ("nodes carrying two or more labels", multilabel),
        ("nodes whose content starts with '/' (path text, not a file)", pathish),
        ("data nodes with two or more producers", list(a["multi_producer"])),
        ("cycles", a["cycles"]),
    ]

    def label_of(item):
        if isinstance(item, list):                       # a cycle, as a path
            return " -> ".join(label_of(x) for x in item)
        if isinstance(item, str) and item in nodes:
            return ", ".join(sorted(nodes[item]["names"])) or item[:14]
        return str(item)

    print("\n-- reported (list each in the patch as a gap; never a failure)")
    for label, found in reported:
        detail = ("  e.g. " + "; ".join(label_of(c) for c in list(found)[:3])) if found else ""
        print(f"  [{'note' if found else 'none'}] {len(found):3} {label}{detail}")

    print("\n-- checks")
    if untyped_compute:
        failures.append("computations with no computation_type")
    print(f"  [{'FAIL' if untyped_compute else 'ok  '}] {len(untyped_compute):3} computations with no computation_type")

    ncomp = len(a["components"])
    sizes = [len(c) for c in a["components"]]
    if expect_components is None:
        print(f"  [note] {ncomp:3} connected components (sizes {sizes}); pass --expect-components to check the prediction")
    elif ncomp == expect_components:
        print(f"  [ok  ] {ncomp:3} connected components, as predicted")
    else:
        failures.append("component count differs from the prediction")
        print(f"  [FAIL] {ncomp:3} connected components (sizes {sizes}), predicted {expect_components}")

    if expect is not None:
        if len(comps) == expect:
            print(f"  [ok  ] {len(comps):3} computations, as predicted")
        else:
            failures.append("computation count differs from the prediction")
            print(f"  [FAIL] {len(comps):3} computations, predicted {expect}")

    print(f"\n-- roots ({len(a['roots'])}), data nodes nothing produced")
    for cid in a["roots"]:
        print(f"  {nodes[cid]['type'] or '?':16} {', '.join(sorted(nodes[cid]['names'])) or '!! unnamed'}")
    print(f"\n-- leaves ({len(a['leaves'])}), data nodes nothing consumed")
    for cid in a["leaves"]:
        print(f"  {nodes[cid]['type'] or '?':16} {', '.join(sorted(nodes[cid]['names'])) or '!! unnamed'}")

    print("\n-- computations, in the order they were registered")
    for sid, c in comps.items():
        meta = c["meta"]
        print(f"  {meta.get('name', '?'):24} {meta.get('computation_type', '!! none'):12} "
              f"{len(c['inputs'])} in -> {len(c['outputs'])} out")

    print()
    if failures:
        print("FAILED: " + "; ".join(failures))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
