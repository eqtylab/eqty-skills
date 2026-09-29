#!/usr/bin/env python3
"""merge_flow.py — Mode 2: join the blind CFG with the data-flow report. Nothing runs.

    python3 <skill-dir>/merge_flow.py <cfg-dir> <flow.json> [--out merged.md] [--pick BOX,BOX,...]

<cfg-dir> is isolated_cfg.py's output (L2.md; L1.md is not needed). <flow.json> is
static_flow.py's --out. Run it only after both exist: the CFG agents never see
the data-flow report, which would bend their boxes.

Each L2 box is joined to the function its "where it lives" names, by file and line
range (or `file:function`). The merged view then has:

- the joined table: box, function, and what the data-flow report knows about that
  function (whether a bare @compute works, the §6.11 recipes it needs, loop body);
- cross-checks: functions that ran through the data flow with no box (the CFG
  missed them), and boxes that name no reached function (dead code, dispatch the
  source can't follow, or a box that names nothing real);
- the L2 diagram, annotated: each function's box marked with what it needs, and
  the hand-offs the CFG didn't draw added as dashed arrows, including values
  changed in place (`train() changes the model` → the arrow into save_adapter
  starts at train, not at the step that first built the model);
- the prediction for auto (every function that is a node), and with --pick, for
  a HITL selection: the picked boxes' functions plus the ones the data flow needs
  to connect them, each listed with why.

Stdlib only.
"""
import argparse
import json
import os
import re
from collections import deque

NODE = re.compile(r'^\s*([A-Za-z_][\w]*)\s*[\[\(\{]+"(.*?)"[\]\)\}]+\s*$')
EDGE = re.compile(r'^\s*([A-Za-z_][\w]*)\s*(?:-->|-\.->|==>)\s*(?:\|"(.*?)"\|\s*)?([A-Za-z_][\w]*)\s*$')
WHERE = re.compile(r'`?(?:\./)?(?:repo/)?([\w./-]+\.py)(?::(\d+)(?:\s*[-–]\s*(\d+))?|:([A-Za-z_][\w.]*))?')


def parse_cfg(path):
    text = open(path).read()
    run = next((l.split(":", 1)[1].strip() for l in text.splitlines()
                if l.startswith(("Path:", "Run:"))), "")   # Run: in CFGs drawn before the rename
    diagram = re.search(r"```mermaid\s*\n(.*?)```", text, re.S)
    nodes, edges = {}, []
    for line in (diagram[1] if diagram else "").splitlines():
        if m := EDGE.match(line):
            edges.append((m[1], m[3], m[2] or ""))
        elif m := NODE.match(line):
            nodes[m[1]] = m[2]
    rows = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) >= 4 and cells[0] in nodes:
            rows[cells[0]] = {"label": cells[1], "where": cells[2], "what": cells[3]}
    return run, nodes, edges, rows


def locate(where, functions):
    """The function a box's "where it lives" points into: (name, whole) or (None, reason)."""
    m = WHERE.search(where or "")
    if not m:
        return None, "no file named"
    file, a, b, name = m[1], m[2], m[3], m[4]
    in_file = [f for f in functions if f["file"] == file or f["file"].endswith("/" + file)]
    if not in_file:
        return None, f"{file} is not in the data-flow report"
    if name:
        hit = next((f for f in in_file if f["name"] == name or f["name"].endswith("." + name)), None)
        return (hit["name"], True) if hit else (None, f"no function {name} in {file}")
    if not a:
        return None, "no line given"
    a, b = int(a), int(b or a)
    around = [f for f in in_file if f["lines"][0] <= a <= f["lines"][1]]
    if not around:
        return None, f"{file}:{a} is at module level"
    f = min(around, key=lambda f: f["lines"][1] - f["lines"][0])   # innermost
    return f["name"], a <= f["lines"][0] and b >= f["lines"][1] - 1


def recipes(breaks):
    return sorted(set(re.findall(r"recipe ([\w-]+)", breaks or "")))


def merge(cfg_dir, flow):
    run, nodes, cedges, rows = parse_cfg(os.path.join(cfg_dir, "L2.md"))
    functions = flow["functions"]
    steps = {s["name"]: s for s in flow["steps"]}
    box_fn, why_not = {}, {}
    for box in nodes:
        fn, whole = locate(rows.get(box, {}).get("where"), functions)
        if fn:
            box_fn[box] = (fn, whole)
        else:
            why_not[box] = whole
    boxes_of = {}
    for box, (fn, whole) in box_fn.items():
        boxes_of.setdefault(fn, []).append(box)
    order = {b: i for i, b in enumerate(nodes)}
    # the box that stands for a function: the one covering it whole, else its first box for inputs, last for outputs
    rep_in, rep_out = {}, {}
    for fn, bs in boxes_of.items():
        whole = [b for b in bs if box_fn[b][1]]
        bs = sorted(bs, key=order.get)
        rep_in[fn] = whole[0] if whole else bs[0]
        rep_out[fn] = whole[0] if whole else bs[-1]
    return run, nodes, cedges, rows, box_fn, why_not, boxes_of, rep_in, rep_out, steps


def folded_links(flow, selected):
    """Hand-offs between the selected functions, a helper's counted for the node that calls it:
    write_index -> read_index via .git/index is add -> commit when add and commit are the nodes."""
    callers = {}
    for st in flow["steps"]:
        for c in st.get("calls", ()):
            callers.setdefault(c, set()).add(st["name"])

    def owners(n, seen=frozenset()):
        if n in selected:
            return {n}
        out = set()
        for c in callers.get(n, set()) - seen:
            out |= owners(c, seen | {n})
        return out
    links = set()
    for e in flow["edges"]:   # a value handed on inside one function belongs to that function's node
        at = owners(e.get("at", e["from"]))
        src = {e["from"]} if e["from"] in selected else at
        dst = {e["to"]} if e["to"] in selected else at
        links |= {(a, b, e["via"]) for a in src for b in dst if a != b}
    for f in flow["file_edges"]:   # a file crosses functions: its writer's node to its reader's
        links |= {(a, b, "file " + f["via"]) for a in owners(f["from"]) for b in owners(f["to"]) if a != b}
    return links


def components_of(fns, links):
    parent = {f: f for f in fns}

    def find(n):
        while parent[n] != n:
            n = parent[n]
        return n
    for a, b, _ in links:
        if a in parent and b in parent:
            parent[find(a)] = find(b)
    comps = {}
    for f in fns:
        comps.setdefault(find(f), []).append(f)
    return sorted(comps.values(), key=len, reverse=True)


def chain(picked_fns, flow):
    """The functions needed to connect the picks through the data flow, with why."""
    adj = {}
    for e in flow["edges"] + [{"from": f["from"], "to": f["to"], "via": "file " + f["via"]} for f in flow["file_edges"]]:
        adj.setdefault(e["from"], []).append((e["to"], e["via"]))
        adj.setdefault(e["to"], []).append((e["from"], e["via"]))
    picked = sorted(picked_fns)
    keep, added = set(picked), {}
    joined = {f: i for i, comp in enumerate(components_of(picked, folded_links(flow, keep))) for f in comp}
    for a in picked:
        for b in picked:
            if a >= b or joined[a] == joined[b]:   # already connected, through the helpers they call
                continue
            prev, q = {a: None}, deque([a])
            while q:
                n = q.popleft()
                for m, via in adj.get(n, ()):
                    if m not in prev:
                        prev[m] = (n, via)
                        q.append(m)
            if b in prev:
                n = b
                while prev[n]:
                    p, via = prev[n]
                    if p not in keep:
                        added[p] = f"carries {via} between {a} and {b}"
                        keep.add(p)
                    n = p
    return keep, added


def predict(fns, flow):
    steps = {s["name"]: s for s in flow["steps"]}
    fns = [f for f in fns if f in steps and not steps[f]["loop_body"]]
    comps = components_of(fns, folded_links(flow, set(fns)))
    return sum(max(steps[f]["call_sites"], 1) for f in fns), comps


def render(cfg_dir, flow, picks):
    run, nodes, cedges, rows, box_fn, why_not, boxes_of, rep_in, rep_out, steps = merge(cfg_dir, flow)
    out = []
    say = out.append
    auto = [n for n in steps if not n.startswith("<") and not steps[n]["loop_body"] and n in boxes_of]
    n_auto, comps_auto = predict(auto, flow)
    unknown = [e for e in flow["edges"] if e["decorator_edge"] is None]
    in_run, todo = set(boxes_of), list(boxes_of)   # what the boxed functions call, transitively: the run
    while todo:
        for c in steps.get(todo.pop(), {}).get("calls", ()):
            if c not in in_run:
                in_run.add(c)
                todo.append(c)
    missed = [n for n in steps if n in in_run and n not in boxes_of and not n.startswith("<") and not steps[n]["loop_body"]]
    other = sorted(n for n in steps if n not in in_run and not n.startswith("<"))
    module_boxes = {b for b, why in why_not.items() if why.endswith("is at module level")}
    inplace = [e for e in flow["edges"] if e["channel"] == "in place" and rep_out.get(e["from"]) and rep_in.get(e["to"])]
    inline = {(u.split(":")[0], u) for s in steps.values() for u in s["unproduced"]}
    n_checks = len(missed) + len(set(why_not) - module_boxes) + len(inplace) + len({v for v, _ in inline})
    say(f"# CFG + data flow\n\n*{len(auto)} function{'s' if len(auto) != 1 else ''} boxed, {n_auto} computations, "
        f"{len(comps_auto)} component{'s' if len(comps_auto) != 1 else ''}; {len(unknown)} hand-off"
        f"{'s' if len(unknown) != 1 else ''} unknown, {n_checks} cross-check{'s' if n_checks != 1 else ''} to review.*\n")
    say(f"Path (from L1): {run}\n\nTypes: {flow.get('types_from', '?')}. Nothing was run.\n")

    say("## Annotated L2 diagram\n")
    say("Solid arrows are the CFG's own. Dashed arrows are hand-offs the data-flow report found that the CFG "
        "did not draw. Box colours: amber = a bare `@compute` raises here (the recipe is named), green = it "
        "works, grey = a loop body, not a node; dashed border = the box names no function the entry reaches.\n")
    say("```mermaid\nflowchart TD")
    for box, label in nodes.items():
        fn = box_fn.get(box, (None,))[0]
        extra = ""
        if fn in steps and box == rep_out.get(fn):
            r = recipes(steps[fn]["breaks"])
            extra = (" · needs " + ", ".join(r)) if r else (" · @compute works" if not steps[fn]["loop_body"] else "")
        say(f'    {box}["{label}{extra}"]')
    drawn = {(a, b) for a, b, _ in cedges}
    fn_of = {b: f for b, (f, _) in box_fn.items()}
    drawn_fns = {(fn_of.get(a), fn_of.get(b)) for a, b, _ in cedges}   # the CFG already shows this hand-off somewhere
    for a, b, lab in cedges:
        say(f'    {a} -->|"{lab}"| {b}' if lab else f"    {a} --> {b}")
    added = []
    for e in flow["edges"]:
        a, b = rep_out.get(e["from"]), rep_in.get(e["to"])
        if not a or not b or a == b:
            continue
        note = f'{e["via"]}: {e["type"].split(".")[-1]}' + (", changed in place" if e["channel"] == "in place" else "")
        if (e["from"], e["to"]) not in drawn_fns and (a, b) not in drawn:
            say(f'    {a} -. "{note}" .-> {b}')
            added.append((e, a, b))
    for f in flow["file_edges"]:
        a, b = rep_out.get(f["from"]), rep_in.get(f["to"])
        if a and b and (f["from"], f["to"]) not in drawn_fns:
            say(f'    {a} -. "file via {f["via"]}" .-> {b}')
    say("    classDef needs fill:#fdecc8,stroke:#b7791f")
    say("    classDef works fill:#d7f0dd,stroke:#2f855a")
    say("    classDef loop fill:#e2e8f0,stroke:#718096")
    say("    classDef nofn stroke-dasharray:4 3")
    for box in nodes:
        fn = box_fn.get(box, (None,))[0]
        if fn is None:
            say(f"    class {box} nofn")
        elif fn in steps and box == rep_out.get(fn):
            s = steps[fn]
            say(f"    class {box} {'loop' if s['loop_body'] else 'needs' if s['breaks'] else 'works'}")
    say("```\n")

    say("## Boxes joined to functions\n")
    say("| box | label | function | data flow |\n|---|---|---|---|")
    for box, label in nodes.items():
        fn, whole = box_fn.get(box, (None, None))
        if fn is None:
            say(f"| {box} | {label[:60]} | — | {why_not[box]} |")
            continue
        s = steps.get(fn)
        facts = []
        if s is not None and box != rep_out.get(fn):
            say(f"| {box} | {label[:60]} | `{fn}` (inside) | see box {rep_out[fn]} |")
            continue
        if s is None:
            facts.append("not reached from the entry")
        else:
            if s["loop_body"]:
                facts.append("loop body")
            if s["breaks"]:
                facts.append("bare @compute raises; needs " + (", ".join(recipes(s["breaks"])) or "a builder"))
            if s["mutated"]:
                facts.append("changes in place: " + ", ".join(s["mutated"]))
            if s["path_args"]:
                facts.append("path parameters: " + ", ".join(s["path_args"]))
            if s["unproduced"]:
                facts.append("receives, made inline by its caller: " + ", ".join(u.split(" (")[0] for u in s["unproduced"]))
        say(f"| {box} | {label[:60]} | `{fn}`{'' if whole else ' (inside)'} | {'; '.join(facts) or '—'} |")

    say("\n## Cross-checks\n")
    for n in missed:
        say(f"- **No box for `{n}`**: the run calls it, the CFG didn't draw it. Review it as a candidate node.")
    for box, why in why_not.items():
        if box in module_boxes:
            continue
        say(f"- **Box {box} names no reached function** ({why}): dead code, dispatch the source can't follow, "
            "or a box naming nothing real. Check before selecting it.")
    for e, a, b in added:
        if e["channel"] == "in place":
            say(f"- **{e['via']} reaches `{e['to']}` changed in place by `{e['from']}`** — drawn {a} ⇢ {b}. "
                "Arrows into it from where it was first built describe the object, not the version recorded.")
    seen = set()
    for n, s in steps.items():
        for u in s["unproduced"]:
            value, caller = u.split(":")[0], re.search(r"\(from ([^)]+)\)", u)
            caller = caller[1] if caller else "?"
            if (value, caller) in seen:
                continue
            seen.add((value, caller))
            users = sorted(m for m, t in steps.items() for x in t["unproduced"]
                           if x.split(":")[0] == value and f"(from {caller})" in x)
            inside = [b for b in boxes_of.get(caller, ()) if b != rep_out.get(caller)]
            say(f"- **{value} is made inline in `{caller}`** and handed to {', '.join(f'`{x}`' for x in users)}: no "
                f"function of its own produces it, so it is recorded only where a receiving node records it"
                + (f" (CFG box{'es' if len(inside) > 1 else ''} {', '.join(inside)} inside `{caller}`)." if inside else "."))
    if flow.get("unresolved_calls"):
        say(f"- **Unknown to the source pass:** {', '.join(flow['unresolved_calls'])}. Facts that rest on these are assumptions.")
    for e in unknown:
        say(f"- **Unknown:** {e['from']} → {e['to']} ({e['why']}). Auto assumes the recipe that doesn't depend on it; HITL may ask.")

    n, comps = n_auto, comps_auto
    if module_boxes or other:
        say("\n## Not part of this run\n")
        for b in sorted(module_boxes):
            say(f"- Box {b} is module-level code, the entry block: SDK start-up and export go here, not a node.")
        if other:
            say(f"- Reached from the entry but not called on this run (other commands or entry points, "
                f"uninstrumented as runs): {', '.join(f'`{o}`' for o in other)}.")
    say("\n## Prediction\n")
    say(f"- **Every box** (before step 3's filter; auto's selection is this minus what `mode2ideas.md` drops, "
        f"and `--pick` gives its numbers): {len(auto)} functions, "
        f"{n} computations counting call sites, {len(comps)} component(s) with builders at every ✗: "
        + " | ".join(", ".join(c) for c in comps))
    if picks:
        fns = {box_fn[b][0] for b in picks if b in box_fn}
        keep, added_fns = chain(fns, flow)
        n, comps = predict(keep, flow)
        say(f"- **HITL** (picked {', '.join(picks)} → {', '.join(sorted(fns))}): {len(keep)} functions, "
            f"{n} computations, {len(comps)} component(s)")
        for f, why in added_fns.items():
            say(f"  - added `{f}`: {why}")
        for b in picks:
            if b not in box_fn:
                say(f"  - box {b} names no function, so it can't be a node: {why_not.get(b, 'unknown box')}")
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("cfg_dir")
    ap.add_argument("flow")
    ap.add_argument("--out")
    ap.add_argument("--pick", help="HITL: comma-separated L2 box ids")
    a = ap.parse_args()
    text = render(a.cfg_dir, json.load(open(a.flow)), [p.strip() for p in a.pick.split(",")] if a.pick else [])
    if a.out:
        open(a.out, "w").write(text)
    print(text)


if __name__ == "__main__":
    main()
