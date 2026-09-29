#!/usr/bin/env python3
"""static_flow.py — Mode 2: the data-flow report, from the source alone. Nothing runs.

    python3 <skill-dir>/static_flow.py <repo> --entry <script.py> [--out flow.json]

Reads every function under <repo> reachable from the entry script's module-level
code and reports how data moves between them, with a confidence on each fact:

- **certain**: follows from the code as written, e.g. `b = f(a)` hands f's return
  to b's next use; a function with no `return <value>` returns None.
- **likely**: rests on the catalogue below, e.g. `Trainer.train()` changes the
  model the trainer was built from.
- **unknown**: a call it can't resolve, or a value whose type it can't tell. These
  are what a guarded partial run, or the user, has to settle.

Types come from the library call that made each value (CALLS, METHODS); the
recipe for each type is the one in references/eqtysdk.md §6.11. Stdlib only. Limits: methods of the repo's own classes resolve only
through `self`; dispatch through getattr, registries or config strings is not
followed; branches are merged, not told apart.
"""
import argparse
import ast
import builtins
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict

# (top-level module, class name) -> recipe id in references/eqtysdk.md §6.11
RECIPES = {
    ("peft", "PeftModel"): "peft-model",
    ("peft", "PeftConfig"): "config-with-sets",
    ("transformers", "Trainer"): "hf-trainer",
    ("transformers", "TrainingArguments"): "hf-training-args",
    ("transformers", "PreTrainedTokenizerBase"): "hf-tokenizer",
    ("transformers", "PreTrainedModel"): "hf-base-weights",
    ("datasets", "Dataset"): "hf-dataset",
    ("datasets", "DatasetDict"): "hf-dataset",
    ("torch", "Module"): "torch-module",
    ("numpy", "ndarray"): "numpy-array",
    ("pandas", "DataFrame"): "pandas-frame",
    ("builtins", "tuple"): "tuple-return",
    ("builtins", "set"): "set",
    ("builtins", "frozenset"): "set",
    ("builtins", "bytes"): "bytes",
}

# Resolved library call -> the type it returns. Keys are dotted after resolving imports.
CALLS = {
    "datasets.load_dataset": "datasets.DatasetDict",
    "datasets.Dataset.from_dict": "datasets.Dataset",
    "datasets.Dataset.from_pandas": "datasets.Dataset",
    "peft.get_peft_model": "peft.PeftModel",
    "peft.PeftModel.from_pretrained": "peft.PeftModel",
    "peft.LoraConfig": "peft.PeftConfig",
    "transformers.Trainer": "transformers.Trainer",
    "transformers.TrainingArguments": "transformers.TrainingArguments",
    "json.load": "json", "json.loads": "json",
    "pandas.read_csv": "pandas.DataFrame", "pandas.read_parquet": "pandas.DataFrame",
    "pandas.DataFrame": "pandas.DataFrame",
    "numpy.load": "numpy.ndarray", "numpy.array": "numpy.ndarray",
    "builtins.dict": "builtins.dict", "builtins.list": "builtins.list", "builtins.set": "builtins.set",
    "builtins.len": "builtins.int", "builtins.str": "builtins.str",
    "argparse.ArgumentParser": "argparse.ArgumentParser",
    "transformers.DataCollatorWithPadding": "transformers.DataCollator",
}
# (type, method) -> the type it returns
METHODS = {
    ("datasets.DatasetDict", "__getitem__"): "datasets.Dataset",
    ("datasets.Dataset", "train_test_split"): "datasets.DatasetDict",
    ("transformers.Trainer", "evaluate"): "builtins.dict",
    ("transformers.Trainer", "predict"): "transformers.PredictionOutput",
    ("builtins.dict", "__getitem__"): None,
    ("json", "__getitem__"): "json",
    ("argparse.ArgumentParser", "parse_args"): "argparse.Namespace",
}
SAME_TYPE = {  # methods that return a new value of their receiver's type
    "datasets.Dataset": {"shuffle", "select", "map", "filter", "sort", "remove_columns", "rename_column",
                         "with_format", "flatten_indices", "cast"},
    "datasets.DatasetDict": {"map", "filter", "shuffle", "remove_columns", "rename_column", "with_format"},
    "pandas.DataFrame": {"copy", "dropna", "fillna", "drop", "merge", "rename", "sort_values", "reset_index", "query"},
}
# Methods that change their receiver, or what it was built from (Trainer(model=m).train() changes m).
MUTATING = {"append", "extend", "insert", "update", "add", "pop", "remove", "clear", "sort", "setdefault",
            "fit", "train", "step", "backward", "zero_grad", "partial_fit", "load_state_dict"}
# (type, method) -> the slots of the receiver's constructor that the call changes
MUTATES_VIA = {("transformers.Trainer", "train"): ("model", "0")}
CONTAINERS = {"builtins.list", "builtins.dict", "builtins.set"}
RANK = {"likely": 1, "certain": 2}
WRITERS = {"save_pretrained": 0, "to_parquet": 0, "to_csv": 0, "to_json": 0, "savefig": 0}   # method -> path arg
FUNC_WRITERS = {"torch.save": 1, "numpy.save": 0, "json.dump": 1}
FUNC_READERS = {"pandas.read_csv": 0, "pandas.read_parquet": 0, "numpy.load": 0, "torch.load": 0}
HUB = ("from_pretrained", "load_dataset")          # read from the Hugging Face hub or its cache
HASHABLE = {"builtins.dict", "builtins.list", "builtins.str", "builtins.int", "builtins.float",
            "builtins.bool", "json", "builtins.NoneType", "argparse.value"}


def recipe_of(t):
    if not t or t in HASHABLE:
        return ""
    if t.startswith("tuple("):
        return " → recipe tuple-return"
    mod, _, cls = t.rpartition(".")
    rid = RECIPES.get((mod.split(".")[0], cls))
    if not rid and t.startswith("transformers.") and "Tokenizer" in cls:
        rid = RECIPES[("transformers", "PreTrainedTokenizerBase")]
    return f" → recipe {rid}" if rid else ""


def hashable(t):
    """True, False, or None when the type is not known."""
    if not t:
        return None
    if t in HASHABLE:
        return True
    if t.startswith("tuple("):
        parts = [hashable(x) for x in t[6:-1].split(",")]
        return None if None in parts else all(parts)
    return False if recipe_of(t) or t == "builtins.set" else None


# -- pyright: types read from the source by a type checker, nothing executed ------------------

REVEAL = re.compile(r'Type of "(.+?)" is "(.*)"$')
BY_CLASS = {cls: f"{mod}.{cls}" for mod, cls in RECIPES}
# pyright prints short class names; these are the ones this script gives a meaning to
PYRIGHT_NAMES = {"Namespace": "argparse.Namespace", "ArgumentParser": "argparse.ArgumentParser",
                 "LoraConfig": "peft.PeftConfig", "TrainOutput": "transformers.TrainOutput"}


def find_pyright(explicit=None):
    """(node, pyright's index.js), or None. Only the npm build: the pip wrapper is itself Python."""
    js = explicit or shutil.which("pyright")
    node = shutil.which("node")
    if not js or not node:
        return None
    js = os.path.realpath(js)
    return (node, js) if js.endswith(".js") else None


def venv_config(repo, venv):
    """pyrightconfig for the project's environment, read from disk so pyright needs no interpreter."""
    venv = venv or next((os.path.join(repo, d) for d in (".venv", "venv", "env")
                         if os.path.exists(os.path.join(repo, d, "pyvenv.cfg"))), None)
    cfg = {"pythonPlatform": {"darwin": "Darwin", "win32": "Windows"}.get(sys.platform, "Linux"),
           "pythonVersion": "3.12", "reportMissingImports": False}
    if venv:
        venv = os.path.realpath(venv)
        m = re.search(r"^version(?:_info)?\s*=\s*(\d+\.\d+)", open(os.path.join(venv, "pyvenv.cfg")).read(), re.M)
        cfg.update(venvPath=os.path.dirname(venv), venv=os.path.basename(venv))
        if m:
            cfg["pythonVersion"] = m[1]
    return cfg, venv


def pyright_types(modules, tool, cfg):
    """Types pyright infers for every assigned name and every function, keyed
    (file, line, name) and (file, function) -> type string. It type-checks a copy
    of the source with reveal_type() added after each assignment; nothing runs."""
    tmp = tempfile.mkdtemp(prefix="static-flow-pyright-")
    where = {}   # (copy file, 0-based line) -> key
    try:
        for m in modules.values():
            lines = open(m.path).read().splitlines()
            after = defaultdict(list)   # original end line -> reveal lines
            for st in ast.walk(m.tree):
                if isinstance(st, (ast.Assign, ast.AnnAssign)) and not lines[st.lineno - 1][:st.col_offset].strip():
                    pad = lines[st.lineno - 1][:st.col_offset]
                    targets = st.targets if isinstance(st, ast.Assign) else [st.target]
                    for n in (x for t in targets for x in ast.walk(t) if isinstance(x, ast.Name)):
                        after[st.end_lineno].append((f"{pad}reveal_type({n.id})", ("name", m.rel, n.lineno, n.id)))
            tail = []
            for n in m.tree.body:
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    tail.append((f"reveal_type({n.name})", ("fn", m.rel, n.name)))
                elif isinstance(n, ast.ClassDef):
                    tail += [(f"reveal_type({n.name}.{f.name})", ("fn", m.rel, f"{n.name}.{f.name}"))
                             for f in n.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))]
            out = []
            for i, line in enumerate(lines, 1):
                out.append(line)
                for text, k in after.get(i, ()):
                    where[(m.rel, len(out))] = k
                    out.append(text)
            for text, k in tail:
                where[(m.rel, len(out))] = k
                out.append(text)
            dest = os.path.join(tmp, m.rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            open(dest, "w").write("\n".join(out) + "\n")
        json.dump(cfg, open(os.path.join(tmp, "pyrightconfig.json"), "w"))
        empty = os.path.join(tmp, ".no-interpreter")
        os.mkdir(empty)
        node, js = tool
        proc = subprocess.run([node, js, "--outputjson"], cwd=tmp, capture_output=True, text=True, timeout=600,
                              env={"PATH": empty, "HOME": os.path.expanduser("~")})   # no python reachable
        report = json.loads(proc.stdout)
        found = {}
        for d in report.get("generalDiagnostics", []):
            hit = REVEAL.match(d.get("message", ""))
            k = where.get((os.path.relpath(d.get("file", ""), os.path.realpath(tmp)), d["range"]["start"]["line"]))
            if hit and k:
                found[k[1:]] = hit[2]
        return found, report.get("version")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def split_top(t, sep):
    parts, depth, cur = [], 0, ""
    for ch in t:
        depth += ch in "[(" and 1 or ch in "])" and -1 or 0
        if ch == sep and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    return parts + [cur.strip()]


def from_pyright(t):
    """A pyright type string as this script's type, or None when it says nothing usable."""
    t = (t or "").strip()
    if t.startswith("(") and t.endswith(")") and "->" not in t:   # a parenthesised union: (str | None)
        return from_pyright(t[1:-1])
    if "->" in t and t.startswith("("):   # a function: only what it returns matters
        return from_pyright(t.rsplit("->", 1)[1])
    if not t or "Unknown" in t or t in ("Any", "object") or t.startswith(("type[", "Module(")):
        return None
    if len(split_top(t, "|")) > 1:   # a top-level union; a | inside brackets is part of one type
        parts = {from_pyright(p) for p in split_top(t, "|") if p != "None"}
        if len(parts) == 1:
            return parts.pop()
        known = {p for p in parts if p}
        return next(iter(sorted(known))) if known and len(known) == len(parts) and all(hashable(p) is False for p in known) else None
    base = t.split("[", 1)[0]
    if base == "tuple" and "[" in t:
        items = [from_pyright(x) for x in split_top(t[6:-1], ",")]
        return None if None in items or "..." in t else f"tuple({','.join(items)})"
    if base in ("dict", "list", "str", "int", "float", "bool", "set", "frozenset", "bytes"):
        return f"builtins.{base}"
    if base == "None":
        return "builtins.NoneType"
    if base in BY_CLASS:
        return BY_CLASS[base]
    if base in PYRIGHT_NAMES:
        return PYRIGHT_NAMES[base]
    if "PeftModel" in base:
        return "peft.PeftModel"
    if "Tokenizer" in base:
        return "transformers.PreTrainedTokenizerBase"
    return f"pyright.{base}"   # a known class with no recipe: whether @compute can hash it is unknown


class Val:
    def __init__(self, kind="unknown", producer=None, type=None, conf="unknown", derived=False,
                 holds=None, path_of=None, elements=None):
        self.kind, self.producer, self.type, self.conf = kind, producer, type, conf
        self.derived, self.path_of, self.elements = derived, path_of, elements
        self.lit = None   # the path it names, when the code spells it out: '.git/index' 
        self.holds = dict(holds or {})   # slot (keyword or position) -> variable it was built from


class Fn:
    def __init__(self, name, node, module):
        self.name, self.node, self.module = name, node, module
        self.where = f"{module.rel}:{node.lineno}"
        a = node.args
        self.params = [p.arg for p in a.posonlyargs + a.args]
        self.kwonly = [p.arg for p in a.kwonlyargs]
        self.call_sites = 0
        self.in_loop = False
        self.ret_types = set()     # this round's
        self.ret_prev = set()      # the last round's, which callers read: a callee may be walked after them
        self.returns_value = any(isinstance(n, ast.Return) and n.value is not None for n in walk_own(node))
        self.generator = any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in walk_own(node))
        self.param_types = defaultdict(set)   # param -> types seen at positional call sites
        self.mutates = {}                     # param -> confidence
        self.path_params = set()
        self.writes = set()                   # params written to (or under)
        self.reads = set()
        self.io = set()                       # (place, "r"|"w", how)
        self.unproduced = set()
        self.calls = set()                    # repo functions this one calls


def walk_own(fn_node):
    """ast.walk over a function body, not descending into nested functions or classes."""
    stack = list(fn_node.body)
    while stack:
        n = stack.pop()
        yield n
        for c in ast.iter_child_nodes(n):
            if not isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                stack.append(c)


class Module:
    def __init__(self, path, repo):
        self.path, self.rel = path, os.path.relpath(path, repo)
        self.name = self.rel[:-3].replace(os.sep, ".").removesuffix(".__init__")
        self.tree = ast.parse(open(path).read(), path)
        self.aliases = {}      # local name -> dotted
        for n in ast.walk(self.tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    self.aliases[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
            elif isinstance(n, ast.ImportFrom) and n.module:
                base = n.module if n.level == 0 else ".".join(self.name.split(".")[:-n.level] + [n.module])
                for a in n.names:
                    self.aliases[a.asname or a.name] = f"{base}.{a.name}"


class Program:
    def __init__(self, repo, entry, pytypes=None):
        self.pytypes = pytypes or {}   # from pyright: (file, line, name) / (file, function) -> type string
        self.repo = os.path.realpath(repo)
        self.modules = {}
        for root, dirs, files in os.walk(self.repo):
            dirs[:] = [d for d in dirs if d not in ("tests", "__pycache__", ".git", ".venv", "venv")
                       and not d.startswith(".")]
            for f in files:
                if f.endswith(".py") and not f.startswith("test_") and f != "conftest.py":
                    m = Module(os.path.join(root, f), self.repo)
                    self.modules[m.name] = m
        self.entry = next(m for m in self.modules.values() if os.path.realpath(m.path) == os.path.realpath(entry))
        self.fns = {}          # dotted -> Fn
        for m in self.modules.values():
            self._collect(m, m.tree.body, m.name, "")
        self.edges = {}
        self.file_edges = set()
        self.unresolved = set()

    def _collect(self, m, body, prefix, qual):
        for n in body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                q = f"{qual}{n.name}"
                f = Fn(q, n, m)
                f.dotted = f"{prefix}.{q}"
                self.fns[f.dotted] = f
                self._collect(m, n.body, prefix, f"{q}.<locals>.")
            elif isinstance(n, ast.ClassDef):
                self._collect(m, n.body, prefix, f"{qual}{n.name}.")

    # -- resolution --------------------------------------------------------------------
    def dotted(self, expr, m):
        parts = []
        while isinstance(expr, ast.Attribute):
            parts.append(expr.attr)
            expr = expr.value
        if not isinstance(expr, ast.Name):
            return None
        head = m.aliases.get(expr.id)
        if head is None and f"{m.name}.{expr.id}" in self.fns:
            head = f"{m.name}.{expr.id}"
        if head is None and hasattr(builtins, expr.id):
            head = f"builtins.{expr.id}"
        return ".".join([head] + parts[::-1]) if head else None

    def repo_fn(self, call, m, cur):
        f = call.func
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "self" and cur:
            cls = cur.name.rsplit(".", 1)[0]
            return self.fns.get(f"{m.name}.{cls}.{f.attr}")
        if isinstance(f, ast.Name) and cur:   # a nested function of the current one
            nested = self.fns.get(f"{m.name}.{cur.name}.<locals>.{f.id}")
            if nested:
                return nested
        d = self.dotted(f, m)
        return self.fns.get(d) if d else None

    # -- the walk ----------------------------------------------------------------------
    def analyse(self, rounds=3):
        for _ in range(rounds):   # summaries (returns, mutations, paths) settle over a few passes
            self.edges, self.file_edges, self.reached = {}, set(), set()
            for f in self.fns.values():
                f.call_sites, f.in_loop, f.unproduced = 0, False, set()
                f.ret_prev, f.ret_types = f.ret_types or f.ret_prev, set()
            self.walk(self.entry, None, self.entry.tree.body, {})
            done = set()
            while self.reached - done:
                d = min(self.reached - done, key=lambda d: self.fns[d].where)
                done.add(d)
                f = self.fns[d]
                env = {p: Val("param", type=_one(f.param_types.get(p)), path_of=p, conf="certain")
                       for p in f.params + f.kwonly}
                self.walk(f.module, f, f.node.body, env)

    def walk(self, m, cur, body, env, loop=0):
        for st in body:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(st, (ast.For, ast.AsyncFor, ast.While)):
                self.expr(m, cur, st.iter if hasattr(st, "iter") else st.test, env, loop)
                self.walk(m, cur, st.body, env, loop + 1)
                self.walk(m, cur, st.orelse, env, loop)
                continue
            if isinstance(st, (ast.If,)):
                self.expr(m, cur, st.test, env, loop)
                self.walk(m, cur, st.body, env, loop)
                self.walk(m, cur, st.orelse, env, loop)
                continue
            if isinstance(st, (ast.With, ast.AsyncWith)):
                for item in st.items:
                    v = self.expr(m, cur, item.context_expr, env, loop)
                    if isinstance(item.optional_vars, ast.Name):
                        env[item.optional_vars.id] = v
                self.walk(m, cur, st.body, env, loop)
                continue
            if isinstance(st, ast.Try):
                for block in (st.body, *[h.body for h in st.handlers], st.orelse, st.finalbody):
                    self.walk(m, cur, block, env, loop)
                continue
            if (isinstance(st, ast.Assign) and isinstance(st.value, ast.Tuple)
                    and all(isinstance(t, ast.Tuple) and len(t.elts) == len(st.value.elts) for t in st.targets)):
                vs = [self.expr(m, cur, x, env, loop) for x in st.value.elts]   # a, b = f(x), g(y)
                for t in st.targets:
                    for tt, v in zip(t.elts, vs):
                        self.bind(m, cur, tt, v, env)
                continue
            if isinstance(st, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                v = self.expr(m, cur, st.value, env, loop) if st.value is not None else Val()
                targets = st.targets if isinstance(st, ast.Assign) else [st.target]
                for t in targets:
                    self.bind(m, cur, t, v, env)
                continue
            if isinstance(st, ast.Return):
                if st.value is not None and cur:
                    v = self.expr(m, cur, st.value, env, loop)
                    cur.ret_types.add(v.type or "?")
                continue
            for child in ast.iter_child_nodes(st):
                if isinstance(child, ast.expr):
                    self.expr(m, cur, child, env, loop)

    def bind(self, m, cur, target, v, env):
        if isinstance(target, ast.Name):
            if not v.type:   # the catalogue had nothing: take pyright's, if it has one
                t = from_pyright(self.pytypes.get((m.rel, target.lineno, target.id)))
                if t:
                    v = Val(v.kind, v.producer, t, "likely", v.derived, v.holds, v.path_of,
                            t[6:-1].split(",") if t.startswith("tuple(") else v.elements)
            env[target.id] = v
        elif isinstance(target, (ast.Tuple, ast.List)):
            for i, t in enumerate(target.elts):
                el = v.elements[i] if v.elements and i < len(v.elements) else None
                self.bind(m, cur, t, Val(v.kind, v.producer, el, v.conf, v.derived) if v.kind != "unknown" else Val(), env)
        elif isinstance(target, (ast.Attribute, ast.Subscript)) and cur:
            base = target.value
            while isinstance(base, (ast.Attribute, ast.Subscript)):
                base = base.value
            if isinstance(base, ast.Name) and base.id in cur.params:
                cur.mutates[base.id] = "certain"

    def expr(self, m, cur, e, env, loop):
        """Evaluate e for what it carries; record every call in it."""
        if isinstance(e, ast.Name):
            if e.id in env:
                return env[e.id]
            fn = self.fns.get(f"{m.name}.{e.id}")
            return Val("function", producer=fn.dotted) if fn else Val()
        if isinstance(e, ast.Constant):
            v = Val("constant", type=f"builtins.{type(e.value).__name__}", conf="certain")
            v.lit = e.value if isinstance(e.value, str) else None
            return v
        if isinstance(e, (ast.JoinedStr, ast.BinOp)):
            parts = [self.expr(m, cur, x, env, loop) for x in ast.iter_child_nodes(e) if isinstance(x, ast.expr)]
            same = {p.type for p in parts}
            t = ("builtins.str" if isinstance(e, ast.JoinedStr)   # a - b on sets, a + b on lists: the operands decide
                 else same.pop() if len(same) == 1 and same <= {"builtins.str", "builtins.int", "builtins.float"} else None)
            return Val("constant", type=t, conf="likely", path_of=next((p.path_of for p in parts if p.path_of), None))
        if isinstance(e, (ast.Tuple, ast.List)):
            parts = [self.expr(m, cur, x, env, loop) for x in e.elts]
            if isinstance(e, ast.List):
                return Val("constant", type="builtins.list", conf="likely")
            known = all(p.type for p in parts)   # a tuple of unknowns is unknown, not "tuple(,)"
            return Val("constant", type=f"tuple({','.join(p.type for p in parts)})" if known else None,
                       elements=[p.type for p in parts])
        if isinstance(e, (ast.Dict, ast.ListComp, ast.DictComp, ast.SetComp)):
            for x in ast.iter_child_nodes(e):
                if isinstance(x, ast.expr):
                    self.expr(m, cur, x, env, loop)
            t = {ast.Dict: "builtins.dict", ast.ListComp: "builtins.list", ast.DictComp: "builtins.dict",
                 ast.SetComp: "builtins.set"}[type(e)]
            return Val("constant", type=t, conf="certain")
        if isinstance(e, ast.Subscript):
            base = self.expr(m, cur, e.value, env, loop)
            self.expr(m, cur, e.slice, env, loop)
            t = METHODS.get((base.type, "__getitem__"), None) if base.type else None
            if base.elements and isinstance(e.slice, ast.Constant) and isinstance(e.slice.value, int):
                t = base.elements[e.slice.value]
            return Val(base.kind, base.producer, t, base.conf, derived=base.kind in ("return", "inplace"),
                       holds=base.holds)
        if isinstance(e, ast.Attribute):
            base = self.expr(m, cur, e.value, env, loop)
            if base.type == "argparse.Namespace":   # a command-line value: plain text or a number
                return Val("constant", type="argparse.value", conf="likely")
            return Val(base.kind, base.producer, None, base.conf, derived=True, holds=base.holds)
        if isinstance(e, ast.Call):
            return self.call(m, cur, e, env, loop)
        for x in ast.iter_child_nodes(e):
            if isinstance(x, ast.expr):
                self.expr(m, cur, x, env, loop)
        return Val()

    def call(self, m, cur, call, env, loop):
        here = cur.name if cur else f"<module {m.rel}>"
        target = self.repo_fn(call, m, cur)
        args = [(i, a) for i, a in enumerate(call.args)]
        vals = [(i, a, self.expr(m, cur, a, env, loop)) for i, a in args]
        kvals = [(k.arg, k.value, self.expr(m, cur, k.value, env, loop)) for k in call.keywords]
        for _, _, v in vals + kvals:   # a repo function handed to a library (map, apply): a loop body
            if v.kind == "function" and v.producer in self.fns and target is None:
                self.fns[v.producer].in_loop = True
                self.reached.add(v.producer)
        if target:
            return self.repo_call(m, cur, here, target, vals, kvals, env, loop)
        return self.library_call(m, cur, here, call, vals, kvals, env)

    def repo_call(self, m, cur, here, g, vals, kvals, env, loop):
        if cur:
            cur.calls.add(g.name)
        g.call_sites += 1
        g.in_loop = g.in_loop or loop > 0
        self.reached.add(g.dotted)
        passed = [(g.params[i] if i < len(g.params) else f"*args[{i}]", a, v, "positional") for i, a, v in vals]
        passed += [(k, a, v, "keyword") for k, a, v in kvals if k]
        for p, a, v, how in passed:
            if how == "positional" and v.type:
                g.param_types[p].add(v.type)
            if v.kind in ("return", "inplace") and v.producer and v.producer != (cur.dotted if cur else None):
                src = self.fns[v.producer].name
                edge = self.edges.setdefault((src, g.name, p), {
                    "from": src, "to": g.name, "via": p, "type": v.type or "?", "passed": how,
                    "at": cur.name if cur else here,   # the function the hand-off happens in
                    "channel": "return" if v.kind == "return" else "in place", "derived": v.derived,
                    "confidence": v.conf})
                edge["decorator_edge"], edge["why"] = self.visible(edge, v)
            elif v.kind == "inline" and cur and p not in ("self", "cls") and hashable(v.type) is False:
                g.unproduced.add(f"{p}: {v.type or '?'} (from {cur.name}){recipe_of(v.type)}")
            if isinstance(a, ast.Name) and p in g.mutates and a.id in env:
                old = env[a.id]
                env[a.id] = Val("inplace", g.dotted, old.type, g.mutates[p], holds=old.holds)
            if isinstance(a, ast.Name):   # file hand-off through the same variable
                if p in g.writes:
                    env.setdefault(f"<written {a.id}>", Val("file", g.dotted))
                    env[f"<written {a.id}>"] = Val("file", g.dotted)
                if p in g.reads and f"<written {a.id}>" in env:
                    w = self.fns[env[f"<written {a.id}>"].producer].name
                    if w != g.name:
                        self.file_edges.add((w, g.name, a.id))
            if cur and v.lit and (p in g.writes or p in g.reads):   # a literal path handed to a reader/writer
                cur.io.add((f"literal {v.lit}", "w" if p in g.writes else "r", f"{g.name}()"))
            if cur and v.path_of and v.path_of in cur.params + cur.kwonly and p in g.path_params:
                cur.path_params.add(v.path_of)
                if p in g.writes:
                    cur.writes.add(v.path_of)
                if p in g.reads:
                    cur.reads.add(v.path_of)
        rt = g.ret_types or g.ret_prev   # walked already this round, or else what the last round found
        known = sorted(t for t in rt if t not in ("?", "builtins.NoneType"))
        conf = "certain"
        if "?" in rt or not rt:   # a return the body can't type: ask pyright for the signature
            t = from_pyright(self.pytypes.get((g.module.rel, g.name)))
            types, conf = ([t] if t else []), "likely"
        else:   # "return None" beside a typed return is an Optional of that type
            types = known or (["builtins.NoneType"] if "builtins.NoneType" in rt else [])
        return Val("return", g.dotted, types[0] if len(types) == 1 else None,
                   conf if len(types) == 1 else "unknown",
                   elements=types[0][6:-1].split(",") if len(types) == 1 and types[0].startswith("tuple(") else None)

    def visible(self, e, v):
        if e["channel"] != "return":
            return False, f"changed in place by {e['from']}; no step returned this version{recipe_of(v.type)}"
        if e["derived"]:
            return False, "reached through an item or attribute, not passed as a value"
        if e["passed"] != "positional":
            return False, "passed by keyword: @compute records positional arguments only (§6.1)"
        h = hashable(v.type)
        if h is None:
            return None, "type unknown from the source"
        if not h:
            return False, f"@compute can't hash a {v.type} (§7.1){recipe_of(v.type)}"
        if v.type == "builtins.list":
            return False, "a returned list becomes N assets, a list argument one; they never match (§6.4)"
        if v.type == "json":
            return None, "a JSON value of unknown shape: if it is a list, the edge breaks (§6.4)"
        return True, None

    def library_call(self, m, cur, here, call, vals, kvals, env):
        f = call.func
        root = f
        while isinstance(root, ast.Attribute):
            root = root.value
        local = isinstance(root, ast.Name) and root.id in env and env[root.id].kind != "function"
        d = None if local else self.dotted(f, m)   # commit.decode() on a local named commit is a method call
        recv = self.expr(m, cur, f.value, env, 0) if isinstance(f, ast.Attribute) and d is None else None
        method = f.attr if isinstance(f, ast.Attribute) else None
        allv = [v for _, _, v in vals] + [v for _, _, v in kvals]
        t, conf = None, "unknown"
        if d:
            t = CALLS.get(d)
            if t is None and method == "from_pretrained" and d.startswith("transformers."):
                cls = d.split(".")[-2]
                t = "transformers.PreTrainedTokenizerBase" if "Tokenizer" in cls else "transformers.PreTrainedModel"
            conf = "likely" if t else "unknown"
            known = (d in FUNC_WRITERS or d in FUNC_READERS or d.endswith(HUB)
                     or d.split(".")[0] in sys.stdlib_module_names)   # pyright types the stdlib
            if t is None and not known and not d.startswith(("builtins.", "os.", "sys.", "argparse.", "logging.")):
                self.unresolved.add(d)
        elif recv is not None and recv.type:
            t = METHODS.get((recv.type, method))
            if t is None and method in SAME_TYPE.get(recv.type, ()):
                t = recv.type
            conf = "likely" if t else "unknown"
        # changes in place: the receiver, and what the catalogue says it was built from
        if recv is not None and cur and isinstance(f.value, ast.Name):
            changed = {}
            if method in MUTATING:
                changed[f.value.id] = "certain" if recv.type in CONTAINERS else "likely"
            for slot in MUTATES_VIA.get((recv.type, method), ()):
                if slot in recv.holds:
                    changed[recv.holds[slot]] = "likely"
            for n, c in changed.items():
                if n in cur.params and RANK[c] > RANK.get(cur.mutates.get(n), 0):   # earlier rounds knew less
                    cur.mutates[n] = c
                if n in env and n != f.value.id:
                    old = env[n]
                    env[n] = Val("inplace", cur.dotted, old.type, c, holds=old.holds)
        # files
        if d == "builtins.open" and vals:
            mode = next((a.value for i, a, _ in vals[1:2] if isinstance(a, ast.Constant)), None) \
                or next((a.value for k, a, _ in kvals if k == "mode" and isinstance(a, ast.Constant)), "r")
            self.io(cur, here, vals[0][2], vals[0][1], "w" if any(c in mode for c in "wax+") else "r", "open")
        elif d in FUNC_WRITERS and len(vals) > FUNC_WRITERS[d]:
            i, a, v = vals[FUNC_WRITERS[d]]
            if not (d == "json.dump" and v.kind == "file-handle"):
                self.io(cur, here, v, a, "w", d)
        elif d in FUNC_READERS and vals:
            self.io(cur, here, vals[0][2], vals[0][1], "r", d)
        elif method in WRITERS and (vals or kvals):
            i, a, v = (vals or kvals)[0]
            self.io(cur, here, v, a, "w", f".{method}() — native code, e.g. safetensors" if method == "save_pretrained" else f".{method}()")
        for k, a, v in kvals:
            if k == "output_dir":
                self.io(cur, here, v, a, "w", "output_dir")
        if d and (d.endswith(HUB) or method in HUB) and cur:
            cur.io.add(("Hugging Face hub or cache", "r", d.rsplit(".", 1)[-1]))
        if d in ("os.path.join", "pathlib.Path") or (d or "").endswith("Path"):
            v = Val("constant", type="builtins.str", conf="likely", path_of=next((v.path_of for v in allv if v.path_of), None))
            v.lit = "/".join(x.lit for x in allv) if allv and all(x.lit for x in allv) else None
            return v
        if d == "builtins.open":
            return Val("file-handle", conf="certain")
        holds = {str(k): a.id for k, a, _ in vals + kvals if isinstance(a, ast.Name)}
        return Val("inline", producer=cur.dotted if cur else None, type=t, conf=conf, holds=holds)

    def io(self, cur, here, v, a, rw, how):
        if not cur:
            return
        if v.path_of and v.path_of in cur.params + cur.kwonly:
            cur.path_params.add(v.path_of)
            (cur.writes if rw == "w" else cur.reads).add(v.path_of)
            cur.io.add((f"param {v.path_of}", rw, how))
        elif v.lit:
            cur.io.add((f"literal {v.lit}", rw, how))
        else:
            cur.io.add((ast.unparse(a) if a is not None else "?", rw, how))

    # -- the report ----------------------------------------------------------------------
    def report(self):
        steps = [self.fns[d] for d in sorted(self.reached, key=lambda d: self.fns[d].where)]
        # file hand-offs through a path written out the same way in the writer and the reader (likely)
        literal = [(f.name, place, rw) for f in steps for place, rw, _ in f.io if place.startswith("literal ")]
        for w, place, rw in literal:
            for r, place2, rw2 in literal:
                if rw == "w" and rw2 == "r" and place == place2 and w != r:
                    self.file_edges.add((w, r, place.removeprefix("literal ")))
        def breaks(f):
            why = []
            for p in f.params:
                for t in sorted(f.param_types.get(p, ())):
                    if p not in ("self", "cls") and hashable(t) is False:
                        why.append(f"can't hash {p}: {t} (§7.1){recipe_of(t)}")
            for t in sorted(f.ret_types):
                if hashable(t) is False:
                    why.append(f"can't hash the {t} it returns (§6.2){recipe_of(t)}")
            if not f.returns_value and not f.generator:
                why.append("returns None (§6.2) → recipe none-return")
            return "; ".join(why) or None
        loop = {f.name for f in steps if f.in_loop}
        nodes = sorted({n for e in self.edges.values() for n in (e["from"], e["to"])}
                       | {f.name for f in steps if f.io} - loop)
        nodes = [n for n in nodes if n not in loop]
        ok = {f.name: not breaks(f) for f in steps}
        deco = components(nodes, [(e["from"], e["to"]) for e in self.edges.values()
                                  if e["decorator_edge"] and ok.get(e["from"]) and ok.get(e["to"])])
        full = components(nodes, [(e["from"], e["to"]) for e in self.edges.values()]
                          + [(a, b) for a, b, _ in self.file_edges])
        return {
            "entry": self.entry.rel,
            "types_from": self.types_from,
            "functions": [{"name": f.name, "file": f.module.rel, "lines": [f.node.lineno, f.node.end_lineno],
                           "reached": d in self.reached} for d, f in sorted(self.fns.items())],
            "steps": [{"name": f.name, "where": f.where, "call_sites": f.call_sites, "loop_body": f.in_loop,
                       "returned_none": not f.returns_value and not f.generator,
                       "returns": sorted(f.ret_types), "mutated": sorted(f"{p} ({c})" for p, c in f.mutates.items()),
                       "path_args": sorted(f.path_params), "io": sorted(map(list, f.io)),
                       "breaks": breaks(f), "unproduced": sorted(f.unproduced), "calls": sorted(f.calls)}
                      for f in steps],
            "edges": list(self.edges.values()),
            "file_edges": [{"from": a, "to": b, "via": v} for a, b, v in sorted(self.file_edges)],
            "unresolved_calls": sorted(self.unresolved),
            "components": {"decorators_only": deco, "with_builders": full},
        }


def components(nodes, links):
    parent = {n: n for n in nodes}

    def find(n):
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n
    for a, b in links:
        if a in parent and b in parent:
            parent[find(a)] = find(b)
    groups = {}
    for n in nodes:
        groups.setdefault(find(n), []).append(n)
    return sorted(groups.values(), key=len, reverse=True)


def short_path(p):
    home = os.path.expanduser("~")
    return "~" + p[len(home):] if p.startswith(home) else p


def _one(types):
    types = sorted(types or ())
    return types[0] if len(types) == 1 else None


def show(r):
    say = print
    say(f"Entry: {r['entry']}  (static: nothing ran; types from {r['types_from']})\n")
    say("Steps reachable from the entry:")
    for s in r["steps"]:
        tail = [f"{s['call_sites']} call site{'s' if s['call_sites'] != 1 else ''}"]
        if s["loop_body"]:
            tail.append("loop body: not a node")
        if s["mutated"]:
            tail.append("changes in place: " + ", ".join(s["mutated"]))
        say(f"  {s['name']:<30} {s['where']:<22} {'; '.join(tail)}")
    say("\nHand-offs (✓ = a bare @compute on both gives this edge; ? = type unknown):")
    for e in r["edges"]:
        mark = {True: "✓", False: "✗", None: "?"}[e["decorator_edge"]]
        say(f"  {mark} {e['from']} → {e['to']}   {e['via']} ({e['type']}, by {e['channel']}, {e['confidence']})"
            + (f"\n      {e['why']}" if e["why"] else ""))
    for f in r["file_edges"]:
        say(f"  ✗ {f['from']} → {f['to']}   file via {f['via']} (likely)\n      record its bytes in the writer and the reader")
    say("\nFiles and hub, by step:")
    for s in r["steps"]:
        for place, rw, how in s["io"]:
            say(f"  {s['name']:<16} {'writes' if rw == 'w' else 'reads '} {place}  ({how})")
    say("\nFindings for placement:")
    for s in r["steps"]:
        if s["loop_body"]:
            continue
        if s["breaks"]:
            say(f"  {s['name']}: a bare @compute raises — {s['breaks']}")
        for p in s["path_args"]:
            say(f"  {s['name']} is handed a path in {p}: @compute hashes the text, not the file (§6.7) → recipe path-arg")
        if s["unproduced"]:
            say(f"  {s['name']} receives what no step produced: {', '.join(s['unproduced'])}")
    if r["unresolved_calls"]:
        say(f"\nUnknown: calls the catalogue doesn't know ({len(r['unresolved_calls'])}): "
            + ", ".join(r["unresolved_calls"]))
    c = r["components"]
    say(f"\nPredicted: bare @compute only {len(c['decorators_only'])} component(s); "
        f"with builders at every ✗ {len(c['with_builders'])}")


def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("repo")
    ap.add_argument("--entry", required=True)
    ap.add_argument("--out")
    ap.add_argument("--types", choices=("pyright", "ast"), default="pyright",
                    help="pyright (default) adds its type inference; ast uses the catalogue alone")
    ap.add_argument("--pyright", help="path to pyright's index.js or its npm bin, if not on PATH")
    ap.add_argument("--venv", help="the project's virtualenv, for library types (default: <repo>/.venv or venv)")
    a = ap.parse_args()
    pytypes, types_from = {}, "the catalogue only (--types ast)"
    if a.types == "pyright":
        tool = find_pyright(a.pyright)
        if not tool:
            sys.exit("pyright not found (the npm build is needed: `npm install -g pyright`). Ask the user to "
                     "install it; if they decline, rerun with --types ast.")
        cfg, venv = venv_config(a.repo, a.venv)
        probe = Program(a.repo, a.entry)
        pytypes, version = pyright_types(probe.modules, tool, cfg)
        types_from = (f"pyright {version} and the catalogue; environment "
                      + (short_path(venv) if venv else "none found, so library types stay unknown"))
    prog = Program(a.repo, a.entry, pytypes)
    prog.types_from = types_from
    prog.analyse()
    r = prog.report()
    show(r)
    if a.out:
        with open(a.out, "w") as f:
            json.dump(r, f, indent=2)


if __name__ == "__main__":
    main()
