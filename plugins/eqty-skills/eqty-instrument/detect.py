#!/usr/bin/env python3
"""Scan a repo for Mode 1 instrumentation points and emit an edit plan.

Mode 1 is a Python repo already built on LangChain, LangGraph or DeepAgents:
the framework decided where the node boundaries are, EQTY ships the handlers,
and the edit is the same edit every time. This script finds where that edit
goes.

Deliberately **stdlib only** (``ast``, ``json``, ``pathlib``), the same
discipline ``eqty-manifest/parse_manifest.py`` keeps, so it can be pointed at a
target repo without installing anything into it -- including the target's own
dependencies, which it never imports and never needs.

Detection is **per module, not per repo**. A real codebase is LangGraph in one
place and plain Python for data prep and eval everywhere else, so a module that
matches nothing is reported as a Mode 2 candidate rather than as a failure.

    python3 detect.py <path>            # human summary
    python3 detect.py <path> --json     # the edit plan, machine-readable

Exit codes -- the point of them is that "cannot be instrumented" is loud:

    0   an edit plan was produced
    1   Mode 1 applies but there is no attachable call site (see NO CALL SITE)
    2   no Mode 1 modules here; everything is a Mode 2 candidate
    64  usage error
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

EXIT_OK = 0
EXIT_NO_CALL_SITE = 1
EXIT_NOT_MODE_1 = 2
EXIT_USAGE = 64

# Directories that are never the repo's own source.
SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".venv", "venv", "env", ".env",
    "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
    "build", "dist", "site-packages", ".eqty_sdk", ".idea", ".vscode",
}

# Framework detection, most specific first. DeepAgents wins over LangGraph
# wins over LangChain, because EqtyDeepAgentsHandler subclasses
# EqtyCallbackHandler -- picking the more specific one never loses anything.
FRAMEWORKS = (
    ("deepagents", ("deepagents",)),
    ("langgraph", ("langgraph",)),
    ("langchain", ("langchain", "langchain_core")),
)

HANDLERS = {
    "deepagents": ("EqtyDeepAgentsHandler", "eqty_lineage.deepagents"),
    "langgraph": ("EqtyCallbackHandler", "eqty_lineage.langchain"),
    "langchain": ("EqtyCallbackHandler", "eqty_lineage.langchain"),
}

# Calls whose result is a compiled graph / agent -- the thing a handler attaches to.
GRAPH_BUILDERS = {
    "create_deep_agent",
    "async_create_deep_agent",
    "create_async_deep_agent",
    "create_agent",
    "create_react_agent",
    "create_supervisor",
    "create_swarm",
}
# ``StateGraph(...).compile()`` and friends.
GRAPH_METHODS = {"compile"}

# Calls whose result is a chat model. Recorded so that ``model.invoke(...)``
# inside a node is never mistaken for an attach point: passing callbacks there
# would record one model call and nothing else.
MODEL_BUILDERS = {"init_chat_model", "ChatOpenAI", "ChatAnthropic", "ChatGoogleGenerativeAI",
                  "ChatVertexAI", "ChatBedrock", "ChatOllama", "ChatMistralAI", "ChatFireworks",
                  "ChatGroq", "ChatCohere", "AzureChatOpenAI", "FakeListChatModel",
                  "GenericFakeChatModel", "FakeMessagesListChatModel"}
MODEL_METHODS = {"bind_tools", "with_structured_output"}

# Methods that carry a ``config=`` and therefore a ``callbacks`` list.
INVOKE_METHODS = {"invoke", "ainvoke", "stream", "astream", "batch", "abatch"}

# Passthroughs: the kind of the receiver survives.
PASSTHROUGH_METHODS = {"with_config", "with_retry", "with_fallbacks", "with_types"}

BACKEND_CLASSES = {
    "StateBackend": ("A", "default StateBackend -- the filesystem is in graph state"),
    "FilesystemBackend": ("B", "FilesystemBackend -- files never enter graph state"),
    "StoreBackend": ("B", "StoreBackend -- files never enter graph state"),
    "CompositeBackend": ("C", "CompositeBackend -- routes some paths to a sandbox"),
}

COMPLETENESS = {
    "A": "everything, including the full file version chain",
    "B": "files via tool arguments; records the virtual path the agent saw",
    "C": "as B, plus `execute` as an ordinary tool computation (cache-drop caveat)",
}

# Decorators that mean "this function is served per request", so a handler
# built at module scope would be shared across concurrent runs.
REQUEST_DECORATOR_ATTRS = {"get", "post", "put", "patch", "delete", "route", "websocket",
                           "head", "options", "api_route"}

PATH_HINTS = ("path", "file", "dir", "folder")
STR_ANNOTATIONS = {"str", "Optional[str]", "list[str]", "List[str]", "str | None",
                   "Optional[List[str]]", "Optional[list[str]]", "list[str] | None"}


# --------------------------------------------------------------------------
# small ast helpers
# --------------------------------------------------------------------------

def dotted(node: ast.AST) -> Optional[str]:
    """Render ``a.b.c`` from an expression, or None if it isn't a dotted name."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def unparse(node: Optional[ast.AST]) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - unparse handles everything we pass it
        return "<expr>"


def kwarg(call: ast.Call, name: str) -> Optional[ast.expr]:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def root_module(name: str) -> str:
    return name.split(".", 1)[0]


# --------------------------------------------------------------------------
# per-module scan
# --------------------------------------------------------------------------

class ModuleScan:
    """One module's findings. Kinds tracked: 'graph', 'model', or unknown."""

    def __init__(self, relpath: str, tree: ast.Module) -> None:
        self.relpath = relpath
        self.tree = tree

        self.imports: Dict[str, str] = {}      # local name -> module it came from
        self.framework: Optional[str] = None
        self.already_instrumented = False
        self.imports_eqty_sdk = False

        self.module_bindings: Dict[str, str] = {}
        self.factories: Dict[str, Any] = {}    # function name -> kind or [kinds]

        self.agents: List[dict] = []
        self.attach_points: List[dict] = []
        self.other_invokes: List[dict] = []
        self.tools: List[dict] = []
        self.init_calls: List[dict] = []
        self.path_state: List[dict] = []
        self.warnings: List[str] = []

        self._graph_bound_at_module_scope = False
        self._agents_seen: set = set()

    # -- imports ----------------------------------------------------------

    def scan_imports(self) -> None:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.imports[alias.asname or root_module(alias.name)] = alias.name
                    self._note_module(alias.name)
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if node.level:  # relative import: says nothing about frameworks
                    continue
                self._note_module(mod)
                for alias in node.names:
                    self.imports[alias.asname or alias.name] = f"{mod}.{alias.name}"
                    if alias.name in ("EqtyCallbackHandler", "EqtyDeepAgentsHandler"):
                        self.already_instrumented = True

    def _note_module(self, module: str) -> None:
        root = root_module(module)
        if root == "eqty_sdk":
            self.imports_eqty_sdk = True
        if root == "eqty_lineage":
            self.already_instrumented = True
        for name, roots in FRAMEWORKS:
            if root in roots:
                # first match wins, and FRAMEWORKS is ordered most specific first
                if self.framework is None or _specificity(name) < _specificity(self.framework):
                    self.framework = name

    # -- function return kinds (one interprocedural step, within the module)

    def scan_factories(self) -> None:
        """``def build_graph(): ... return graph.compile()`` binds a graph too.

        One level, inside one module. That is enough for the shape both worked
        examples use and stops well short of pretending to do dataflow.
        """
        for node in ast.walk(self.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            local: Dict[str, str] = {}
            kinds: List[Any] = []
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assign) and len(sub.targets) == 1:
                    kind = self.classify(sub.value, local)
                    name = dotted(sub.targets[0])
                    if name and isinstance(kind, str):
                        local[name] = kind
                elif isinstance(sub, ast.Return) and sub.value is not None:
                    if isinstance(sub.value, ast.Tuple):
                        kinds.append([self.classify(e, local) for e in sub.value.elts])
                    else:
                        kinds.append(self.classify(sub.value, local))
            for kind in kinds:
                if kind and (isinstance(kind, str) or any(kind)):
                    self.factories[node.name] = kind
                    break

    # -- classification ---------------------------------------------------

    def classify(self, node: Optional[ast.AST], local: Dict[str, str]) -> Any:
        """'graph', 'model', a list of those for a tuple, or None."""
        if node is None:
            return None
        if isinstance(node, (ast.Name, ast.Attribute)):
            name = dotted(node)
            if not name:
                return None
            return local.get(name) or self.module_bindings.get(name)
        if isinstance(node, ast.Tuple):
            return [self.classify(e, local) for e in node.elts]
        if isinstance(node, ast.Await):
            return self.classify(node.value, local)
        if not isinstance(node, ast.Call):
            return None

        # Take the called name from the AST, not from a dotted rendering: the
        # receiver is often not a dotted name at all (``ChatOpenAI(...).bind_tools(t)``,
        # ``builders[k]().compile()``), and rendering it would lose the method.
        if isinstance(node.func, ast.Attribute):
            last = node.func.attr
        elif isinstance(node.func, ast.Name):
            last = node.func.id
        else:
            last = ""

        if last in GRAPH_BUILDERS or last in GRAPH_METHODS:
            return "graph"
        if last in MODEL_BUILDERS:
            return "model"
        if last in MODEL_METHODS:
            if isinstance(node.func, ast.Attribute):
                return self.classify(node.func.value, local) or "model"
            return "model"
        if last in PASSTHROUGH_METHODS and isinstance(node.func, ast.Attribute):
            return self.classify(node.func.value, local)
        if last in self.factories:
            return self.factories[last]
        return None

    # -- the main walk ----------------------------------------------------

    def scan(self) -> None:
        self.scan_imports()
        self.scan_factories()
        self.walk_body(self.tree.body, local={}, ctx=frozenset(), fn=None, module_scope=True)
        self.sweep_agents()
        self.finish()

    def sweep_agents(self) -> None:
        """Catch builder calls the assignment walk never saw.

        ``def build_agent(): return create_deep_agent(...)`` binds nothing, and a
        backend passed there is exactly as load-bearing as one passed to an
        assignment. The walk records the ones it can name; this records the rest.
        """
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call) or id(node) in self._agents_seen:
                continue
            if isinstance(node.func, ast.Attribute):
                last = node.func.attr
            elif isinstance(node.func, ast.Name):
                last = node.func.id
            else:
                continue
            if last in GRAPH_BUILDERS:
                self.record_agent(node, [], module_scope=False)

    def walk_body(self, stmts: Iterable[ast.stmt], local: Dict[str, str],
                  ctx: frozenset, fn: Optional[str], module_scope: bool) -> None:
        for stmt in stmts:
            self.walk_stmt(stmt, local, ctx, fn, module_scope)

    def walk_stmt(self, stmt: ast.stmt, local: Dict[str, str],
                  ctx: frozenset, fn: Optional[str], module_scope: bool) -> None:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self.record_tool(stmt)
            inner_ctx = set(ctx)
            if isinstance(stmt, ast.AsyncFunctionDef):
                inner_ctx.add("async def")
            if self.is_request_handler(stmt):
                inner_ctx.add("request handler")
            # a function body sees module bindings, plus its own
            self.walk_body(stmt.body, dict(local), frozenset(inner_ctx), stmt.name, False)
            return

        if isinstance(stmt, ast.ClassDef):
            self.record_state_class(stmt)
            self.walk_body(stmt.body, dict(local), ctx, fn, False)
            return

        if isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
            inner = frozenset(set(ctx) | {"loop"})
            for expr in ast.iter_child_nodes(stmt):
                if isinstance(expr, ast.stmt):
                    self.walk_stmt(expr, local, inner, fn, module_scope)
                else:
                    self.scan_calls(expr, local, inner, fn, module_scope)
            return

        if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            value = stmt.value
            kind = self.classify(value, local)
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            self.bind(targets, kind, local, module_scope)
            if isinstance(value, ast.Call):
                self.record_agent(value, targets, module_scope)
            if value is not None:
                self.scan_calls(value, local, ctx, fn, module_scope)
            return

        # everything else: recurse into nested statements, scan expressions
        for child in ast.iter_child_nodes(stmt):
            if isinstance(child, ast.stmt):
                self.walk_stmt(child, local, ctx, fn, module_scope)
            elif isinstance(child, ast.expr):
                self.scan_calls(child, local, ctx, fn, module_scope)

    def bind(self, targets: List[ast.expr], kind: Any,
             local: Dict[str, str], module_scope: bool) -> None:
        table = self.module_bindings if module_scope else local
        for target in targets:
            if isinstance(target, ast.Tuple) and isinstance(kind, list):
                for elt, k in zip(target.elts, kind):
                    name = dotted(elt)
                    if name and isinstance(k, str):
                        table[name] = k
                        if module_scope and k == "graph":
                            self._graph_bound_at_module_scope = True
                continue
            name = dotted(target)
            if name and isinstance(kind, str):
                table[name] = kind
                if module_scope and kind == "graph":
                    self._graph_bound_at_module_scope = True

    # -- individual findings ----------------------------------------------

    def record_agent(self, call: ast.Call, targets: List[ast.expr], module_scope: bool) -> None:
        if isinstance(call.func, ast.Attribute):
            last = call.func.attr
        elif isinstance(call.func, ast.Name):
            last = call.func.id
        else:
            return
        if last not in GRAPH_BUILDERS or id(call) in self._agents_seen:
            return
        self._agents_seen.add(id(call))
        backend = kwarg(call, "backend")
        cls, note = self.backend_class(backend)
        middleware = self.name_list(kwarg(call, "middleware"))
        name = dotted(targets[0]) if targets else None
        agent = {
            "name": name,
            "builder": last,
            "line": call.lineno,
            "module_scope": module_scope,
            "backend": unparse(backend) or "default (StateBackend)",
            "completeness_class": cls,
            "completeness": COMPLETENESS.get(cls, "unknown"),
            "backend_note": note,
            "middleware": middleware,
            "interrupt_on": kwarg(call, "interrupt_on") is not None,
            "skills": kwarg(call, "skills") is not None,
            "subagents": kwarg(call, "subagents") is not None,
        }
        self.agents.append(agent)

        if last in GRAPH_BUILDERS and last.endswith("deep_agent"):
            if not any(m.endswith("TodoListMiddleware") for m in middleware):
                self.warnings.append(
                    f"line {call.lineno}: no TodoListMiddleware on {last}(...) -- "
                    "write_todos does not exist and no plan asset will appear. "
                    "It is not part of the default stack; it comes from langchain."
                )
        if cls == "B":
            self.warnings.append(
                f"line {call.lineno}: backend keeps files out of graph state -- writes are "
                "recorded from tool arguments and the manifest carries the virtual path "
                "the agent saw, not the real one."
            )
        if cls == "C":
            self.warnings.append(
                f"line {call.lineno}: sandbox/composite backend -- `execute` is recorded as an "
                "ordinary tool computation, and a successful `execute` drops the reconstruction "
                "caches. Prefer the file tools over shell redirection when you want writes attested."
            )
        if cls == "unknown" and backend is not None:
            self.warnings.append(
                f"line {call.lineno}: unrecognised backend {unparse(backend)!r} -- "
                "determine its completeness class by hand before promising the file chain."
            )

    def backend_class(self, backend: Optional[ast.expr]) -> Tuple[str, str]:
        if backend is None:
            return "A", BACKEND_CLASSES["StateBackend"][1]
        text = unparse(backend)
        for cls_name, (cls, note) in BACKEND_CLASSES.items():
            if cls_name in text:
                return cls, note
        if "Sandbox" in text or "sandbox" in text:
            return "C", "sandbox backend"
        return "unknown", "unrecognised backend"

    def name_list(self, node: Optional[ast.expr]) -> List[str]:
        if node is None:
            return []
        elts = node.elts if isinstance(node, (ast.List, ast.Tuple)) else [node]
        out = []
        for elt in elts:
            if isinstance(elt, ast.Call):
                out.append(dotted(elt.func) or unparse(elt))
            else:
                out.append(dotted(elt) or unparse(elt))
        return out

    def record_tool(self, fn: ast.AST) -> None:
        names = [dotted(d.func) if isinstance(d, ast.Call) else dotted(d)
                 for d in getattr(fn, "decorator_list", [])]
        names = [n or "" for n in names]
        short = {n.rsplit(".", 1)[-1] for n in names}
        if "tool" not in short:
            return
        self.tools.append({
            "name": fn.name,
            "line": fn.lineno,
            "decorators": names,
            "has_eqty_tool": "eqty_tool" in short,
        })

    def record_state_class(self, cls: ast.ClassDef) -> None:
        bases = {(dotted(b) or "").rsplit(".", 1)[-1] for b in cls.bases}
        looks_like_state = bool(bases & {"TypedDict", "AgentState", "DeepAgentState",
                                         "MessagesState", "BaseModel"}) or cls.name.endswith("State")
        if not looks_like_state:
            return
        for stmt in cls.body:
            if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
                continue
            key = stmt.target.id
            annotation = unparse(stmt.annotation)
            if any(hint in key.lower() for hint in PATH_HINTS) and \
                    ("str" in annotation and "Path" not in annotation):
                self.path_state.append({
                    "class": cls.name,
                    "key": key,
                    "annotation": annotation,
                    "line": stmt.lineno,
                })

    def is_request_handler(self, fn: ast.AST) -> bool:
        for dec in getattr(fn, "decorator_list", []):
            target = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(target, ast.Attribute) and target.attr in REQUEST_DECORATOR_ATTRS:
                return True
        return False

    def scan_calls(self, node: ast.AST, local: Dict[str, str],
                   ctx: frozenset, fn: Optional[str], module_scope: bool) -> None:
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            self.record_init(sub, ctx, fn)
            if not isinstance(sub.func, ast.Attribute) or sub.func.attr not in INVOKE_METHODS:
                continue
            target = dotted(sub.func.value) or unparse(sub.func.value)
            kind = self.classify(sub.func.value, local)
            entry = {
                "line": sub.lineno,
                "target": target,
                "method": sub.func.attr,
                "in_function": fn,
                "context": sorted(ctx),
            }
            if kind == "graph":
                entry["binding"] = "graph"
            elif kind == "model":
                self.other_invokes.append(dict(entry, kind="chat model call -- not an attach point"))
                continue
            elif self.framework is None:
                continue
            else:
                entry["binding"] = "unresolved"

            config = kwarg(sub, "config")
            entry["config_present"] = config is not None
            entry["config"] = unparse(config) if config is not None else None
            entry["config_has_callbacks"] = bool(
                config is not None and "callbacks" in unparse(config))
            self.attach_points.append(entry)

    def record_init(self, call: ast.Call, ctx: frozenset, fn: Optional[str]) -> None:
        name = dotted(call.func) or ""
        short = name.rsplit(".", 1)[-1]
        if short != "init":
            return
        origin = self.imports.get(name.split(".", 1)[0], "")
        if not (root_module(origin) == "eqty_sdk" or name.startswith("eqty_sdk.")):
            return
        per_run = bool(ctx & {"loop", "request handler", "async def"})
        self.init_calls.append({
            "line": call.lineno,
            "in_function": fn,
            "context": sorted(ctx),
            "per_run": per_run,
        })

    # -- wrap up ----------------------------------------------------------

    def finish(self) -> None:
        for point in self.attach_points:
            if point["config_present"] and not point["config_has_callbacks"]:
                point["edit"] = "merge a callbacks key into the existing config= dict"
            elif point["config_has_callbacks"]:
                point["edit"] = "append the handler to the existing callbacks list"
            else:
                point["edit"] = 'add config={"callbacks": [<handler>()]}'
            if point["context"]:
                point["handler_lifetime"] = (
                    "construct the handler at this call site (inside the "
                    + ", ".join(point["context"]) + "), never once at module scope"
                )
            else:
                point["handler_lifetime"] = "one handler per invocation"

        for call in self.init_calls:
            if call["per_run"]:
                self.warnings.append(
                    f"line {call['line']}: eqty_sdk.init() inside {', '.join(call['context'])} -- "
                    "init() is process-global and a second call is a silent no-op "
                    "(`Config already initialized`). A per-run init() looks like isolation "
                    "and gives none. Move it to startup."
                )

        undecorated = [t["name"] for t in self.tools if not t["has_eqty_tool"]]
        if undecorated:
            self.warnings.append(
                "tools without @eqty_tool: " + ", ".join(undecorated) +
                " -- a callback only ever receives a tool's name, so each registers from a "
                "name/description stub instead of its source."
            )

        if self.path_state:
            keys = ", ".join(f"{p['class']}.{p['key']}" for p in self.path_state)
            self.warnings.append(
                f"filesystem paths held as str in state ({keys}) -- pathlib.Path is the opt-in "
                "for Dataset.from_path; a string is never registered as its own asset."
            )

        if self.already_instrumented:
            self.warnings.append(
                "this module already imports an EQTY handler -- review before editing rather "
                "than adding a second one."
            )

        if self._graph_bound_at_module_scope and not self.attach_points:
            self.warnings.append(
                "module-level graph with no in-process invoke -- there is nothing here to pass "
                "config={'callbacks': [...]} to. Import it and drive it from a script that has "
                "a call site."
            )

    def to_dict(self) -> dict:
        handler, module = HANDLERS.get(self.framework, (None, None))
        return {
            "framework": self.framework,
            "mode": 1 if self.framework else 2,
            "handler": handler,
            "handler_import": f"from {module} import {handler}" if handler else None,
            "already_instrumented": self.already_instrumented,
            "agents": self.agents,
            "attach_points": self.attach_points,
            "non_attach_invokes": self.other_invokes,
            "tools": self.tools,
            "init_calls": self.init_calls,
            "path_state": self.path_state,
            "warnings": self.warnings,
        }


def _specificity(name: str) -> int:
    for i, (fw, _) in enumerate(FRAMEWORKS):
        if fw == name:
            return i
    return len(FRAMEWORKS)


# --------------------------------------------------------------------------
# repo walk
# --------------------------------------------------------------------------

def python_files(root: Path) -> List[Path]:
    if root.is_file():
        return [root] if root.suffix == ".py" else []
    out = []
    for path in sorted(root.rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        out.append(path)
    return out


def scan_repo(root: Path) -> dict:
    files = python_files(root)
    modules: Dict[str, dict] = {}
    unparsable: List[dict] = []

    for path in files:
        rel = str(path.relative_to(root) if root.is_dir() else path.name)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError) as exc:
            unparsable.append({"module": rel, "error": f"{type(exc).__name__}: {exc}"})
            continue
        scan = ModuleScan(rel, tree)
        scan.scan()
        modules[rel] = scan.to_dict()

    mode1 = {k: v for k, v in modules.items() if v["framework"]}
    mode2 = sorted(k for k, v in modules.items() if not v["framework"])
    attach_total = sum(len(v["attach_points"]) for v in mode1.values())

    repo_warnings: List[str] = []
    deployment = sorted(
        str(p.relative_to(root)) for name in ("langgraph.json", "agent.json")
        for p in (root.rglob(name) if root.is_dir() else [])
        if not any(part in SKIP_DIRS for part in p.relative_to(root).parts)
    )
    if deployment:
        repo_warnings.append(
            "deployment manifest(s) present (" + ", ".join(deployment) + ") -- a graph run by "
            "`langgraph dev`, by LangSmith's platform or in a container has no in-process call "
            "site, so callbacks cannot reach it. That is a deployment-model limit, not a handler "
            "one: instrument a script that imports and drives the graph instead."
        )
    for item in unparsable:
        repo_warnings.append(f"{item['module']}: not parsed ({item['error']})")

    if not files:
        code, reason = EXIT_USAGE, "no Python files found"
    elif not mode1:
        code, reason = EXIT_NOT_MODE_1, (
            f"no LangChain / LangGraph / DeepAgents module found; "
            f"{len(mode2)} module(s) are Mode 2 candidates")
    elif attach_total == 0:
        code, reason = EXIT_NO_CALL_SITE, (
            "framework imports but NO ATTACHABLE CALL SITE -- nothing to pass "
            "config={'callbacks': [...]} to. Mode 1 cannot cover this repo as it stands")
    else:
        code, reason = EXIT_OK, f"{attach_total} attach point(s) across {len(mode1)} module(s)"

    return {
        "root": str(root),
        "generated_by": "eqty-instrument/detect.py",
        "verdict": {"exit_code": code, "reason": reason},
        "counts": {
            "modules": len(modules),
            "mode1_modules": len(mode1),
            "mode2_candidates": len(mode2),
            "attach_points": attach_total,
        },
        "repo_warnings": repo_warnings,
        "modules": {k: v for k, v in modules.items() if v["framework"]},
        "mode2_candidates": mode2,
    }


# --------------------------------------------------------------------------
# human summary
# --------------------------------------------------------------------------

def summarize(plan: dict) -> str:
    out: List[str] = []
    add = out.append
    counts = plan["counts"]
    add(f"eqty-instrument · detect · {plan['root']}")
    add("=" * 72)
    add(f"{counts['modules']} module(s) scanned · {counts['mode1_modules']} Mode 1 · "
        f"{counts['mode2_candidates']} Mode 2 candidate(s) · "
        f"{counts['attach_points']} attach point(s)")

    for warning in plan["repo_warnings"]:
        add("")
        add(f"  ! {warning}")

    for name, mod in plan["modules"].items():
        add("")
        add(f"{name}")
        add(f"  framework:  {mod['framework']}  ->  {mod['handler']}")
        add(f"  import:     {mod['handler_import']}")

        for agent in mod["agents"]:
            add(f"  agent:      {agent['name'] or '<unnamed>'} = {agent['builder']}(...) "
                f"at line {agent['line']}")
            add(f"              backend {agent['backend']} -> class "
                f"{agent['completeness_class']}: {agent['completeness']}")
            if agent["middleware"]:
                add(f"              middleware: {', '.join(agent['middleware'])}")

        if mod["attach_points"]:
            for point in mod["attach_points"]:
                flag = "" if point["binding"] == "graph" else "  [unresolved binding — confirm]"
                add(f"  attach:     line {point['line']}: {point['target']}."
                    f"{point['method']}(...){flag}")
                add(f"              {point['edit']}")
                if point["config_present"]:
                    add(f"              existing config= is {point['config']} — MERGE, "
                        "do not replace")
                add(f"              {point['handler_lifetime']}")
        else:
            add("  attach:     none found in this module")

        for other in mod["non_attach_invokes"]:
            add(f"  skipped:    line {other['line']}: {other['target']}.{other['method']}(...) "
                f"— {other['kind']}")

        if mod["tools"]:
            for tool in mod["tools"]:
                mark = "ok" if tool["has_eqty_tool"] else "needs @eqty_tool"
                add(f"  tool:       line {tool['line']}: {tool['name']} — {mark}")

        for call in mod["init_calls"]:
            where = ", ".join(call["context"]) or (
                f"in {call['in_function']}()" if call["in_function"] else "module scope")
            add(f"  init():     line {call['line']} ({where})")

        for warning in mod["warnings"]:
            add(f"  ! {warning}")

    if plan["mode2_candidates"]:
        add("")
        add("Mode 2 candidates (arbitrary Python — report, do not improvise an edit):")
        for name in plan["mode2_candidates"]:
            add(f"  - {name}")

    add("")
    add("-" * 72)
    verdict = plan["verdict"]
    label = {
        EXIT_OK: "OK",
        EXIT_NO_CALL_SITE: "CANNOT INSTRUMENT",
        EXIT_NOT_MODE_1: "NOT MODE 1",
        EXIT_USAGE: "NOTHING SCANNED",
    }.get(verdict["exit_code"], "?")
    add(f"{label}: {verdict['reason']}")
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan a repo for Mode 1 (LangChain / LangGraph / DeepAgents) "
                    "instrumentation points.")
    parser.add_argument("path", help="file or directory to scan")
    parser.add_argument("--json", action="store_true", help="print the edit plan as JSON")
    args = parser.parse_args(argv)

    root = Path(args.path).resolve()
    if not root.exists():
        print(f"no such path: {args.path}", file=sys.stderr)
        return EXIT_USAGE

    plan = scan_repo(root)
    print(json.dumps(plan, indent=2) if args.json else summarize(plan))
    return plan["verdict"]["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
