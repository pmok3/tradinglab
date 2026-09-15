"""AST inventory of project-owned windows, without importing application code."""
from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WindowSite:
    window_id: str
    path: str
    kind: str


ABSTRACT_WINDOWS = {
    "tradinglab.gui._modal_base.BaseModalDialog": "Lifecycle base; only concrete dialogs are opened.",
    "tradinglab.gui._modal_base.BaseEditorDialog": "Editor footer base; only concrete editors are opened.",
}


def discover_windows(package: Path) -> dict[str, WindowSite]:
    """Resolve imports, assignment aliases and transitive window inheritance.

    Raw constructors use ``qualified.scope@assigned_target`` IDs. Repeated
    targets (or unassigned calls) get a construction ordinal, not a source
    line, so adding another popup cannot inherit an existing probe silently.
    """
    aliases: dict[str, str] = {}
    classes: dict[str, tuple[str, list[str]]] = {}
    calls: list[tuple[str, str, str, str]] = []

    def expression(node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return f"{expression(node.value)}.{node.attr}"
        return ""

    for path in sorted(package.rglob("*.py")):
        parts = list(path.relative_to(package.parent).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
            package_name = ".".join(parts)
        else:
            package_name = ".".join(parts[:-1])
        module = ".".join(parts)
        relative = str(path.relative_to(package.parent))

        class Visitor(ast.NodeVisitor):
            def __init__(self, module, package_name, relative):
                self.module = module
                self.package_name = package_name
                self.relative = relative
                self.scope: list[str] = []
                self.names: dict[str, str] = {}
                self.targets: dict[int, str] = {}

            def resolve(self, node: ast.AST) -> str:
                value = expression(node)
                if not value:
                    return ""
                first, *rest = value.split(".")
                return ".".join([self.names.get(first, f"{self.module}.{first}"), *rest])

            def visit_Import(self, node):
                for name in node.names:
                    self.names[name.asname or name.name.split(".")[0]] = (
                        name.name if name.asname else name.name.split(".")[0]
                    )

            def visit_ImportFrom(self, node):
                base = node.module or ""
                if node.level:
                    parent = self.package_name.split(".")
                    base = ".".join(parent[:len(parent) - node.level + 1] + ([base] if base else []))
                for name in node.names:
                    if name.name != "*":
                        key = name.asname or name.name
                        value = f"{base}.{name.name}"
                        self.names[key] = value
                        if not self.scope:
                            aliases[f"{self.module}.{key}"] = value

            def visit_ClassDef(self, node):
                qualified = ".".join([self.module, *self.scope, node.name])
                classes[qualified] = (self.relative, [self.resolve(base) for base in node.bases])
                self.names[node.name] = qualified
                self.nested(node)

            def nested(self, node):
                saved = self.names.copy()
                self.scope.append(node.name)
                for statement in node.body:
                    self.visit(statement)
                self.scope.pop()
                self.names = saved

            visit_FunctionDef = nested
            visit_AsyncFunctionDef = nested

            def assignment(self, node, target):
                value = node.value
                if isinstance(value, ast.Call):
                    self.targets[id(value)] = expression(target)
                elif isinstance(target, ast.Name) and value is not None:
                    resolved = self.resolve(value)
                    if resolved:
                        self.names[target.id] = resolved
                        if not self.scope:
                            aliases[f"{self.module}.{target.id}"] = resolved
                self.generic_visit(node)

            def visit_Assign(self, node):
                self.assignment(node, node.targets[0])

            def visit_AnnAssign(self, node):
                self.assignment(node, node.target)

            def visit_Call(self, node):
                calls.append((
                    ".".join([self.module, *self.scope]),
                    self.targets.get(id(node), "") or "call",
                    self.resolve(node.func),
                    self.relative,
                ))
                self.generic_visit(node)

        Visitor(module, package_name, relative).visit(
            ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path)),
        )

    def canonical(name: str) -> str:
        seen: set[str] = set()
        while name in aliases and name not in seen:
            seen.add(name)
            name = aliases[name]
        return name

    window_types = {"tkinter.Toplevel", "tkinter.Tk"}
    found: dict[str, WindowSite] = {}
    while True:
        added = {
            name for name, (_, bases) in classes.items()
            if name not in window_types and any(canonical(base) in window_types for base in bases)
        }
        if not added:
            break
        window_types.update(added)
        for name in added:
            found[name] = WindowSite(name, classes[name][0], "class")

    raw_types = {"tkinter.Toplevel", "tkinter.Tk"} | (ABSTRACT_WINDOWS.keys() & window_types)
    raw = [(scope, target, path) for scope, target, callee, path in calls
           if canonical(callee) in raw_types]
    totals = Counter((scope, target) for scope, target, _ in raw)
    counts: Counter = Counter()
    for scope, target, path in raw:
        key = (scope, target)
        counts[key] += 1
        suffix = f"[{counts[key]}]" if totals[key] > 1 or target == "call" else ""
        window_id = f"{scope}@{target}{suffix}"
        found[window_id] = WindowSite(window_id, path, "constructor")
    return dict(sorted(found.items()))


def assert_registered_windows(
    discovered: dict[str, WindowSite],
    registrations: list[dict[str, str]],
    exemptions: dict[str, str] | None = None,
) -> None:
    exemptions = exemptions or {}
    ids = [case["window_id"] for case in registrations]
    invalid = set(ids) - discovered.keys()
    stale = exemptions.keys() - discovered.keys()
    missing = discovered.keys() - set(ids) - exemptions.keys()
    duplicates = [node for node, count in Counter(case["nodeid"] for case in registrations).items() if count > 1]
    assert not duplicates, f"Duplicate width registrations on test items: {duplicates}"
    assert not invalid, f"Width probes reference unknown/stale windows: {sorted(invalid)}"
    assert not stale, f"Stale abstract window exceptions: {sorted(stale)}"
    assert not set(ids) & exemptions.keys(), "Abstract exceptions must not also have concrete probes"
    assert all(reason.strip() for reason in exemptions.values()), "Window exceptions need precise reasons"
    assert not missing, "Add a mapped behavioral width case for:\n" + "\n".join(
        f"{name} ({discovered[name].path})" for name in sorted(missing)
    )
