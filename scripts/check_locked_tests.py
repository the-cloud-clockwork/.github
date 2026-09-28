#!/usr/bin/env python3
"""The protected test list: tests/LOCKED_MANIFEST.json.

Every listed test must still exist, carry no skip or xfail (decorator, module
`pytestmark`, or a skip call in its body), keep at least its recorded number of
asserts, and keep its literals (the expected values, parametrize rows and
inputs) byte-identical to the recorded fingerprint. A change to any of these
lands only with the manifest changed in the same PR, and the manifest is owned
by the operator (.github/CODEOWNERS). The check reads source with `ast` and runs
no test, so it holds whatever the rest of the suite shows.

    python3 scripts/check_locked_tests.py              # check (CI: ci-lambdas)
    python3 scripts/check_locked_tests.py --update     # re-record the listed tests
    python3 scripts/check_locked_tests.py --add FILE   # lock every test in FILE
    python3 scripts/check_locked_tests.py --base-root B --head-root H   # CI: gate-required

The gate mode is the authority (gate-required.yml, pull_request_target): the
base's copy of this file reads the PR's merge result H against its parent B,
runs nothing from H, and is red when a test B locks is deleted, skipped or
weakened in H (a helper, fixture or constant it reaches, transitively, counts
as the test), when H's manifest drops or alters an entry B holds, when H edits
or deletes a gate file B holds (FROZEN_GLOBS), or when H adds a collection hook
or a skip to a conftest.py over a locked test, or changes the pytest settings
there. H may add locked tests and new gate or rule files.

Mutation record (2026-09-28), each a draft PR on test_orders_rest_on_the_tick.py,
ci-lambdas' Locked tests step:
  a locked test deleted                   -> RED (run 36473828214)
  @pytest.mark.skip added                 -> RED (run 36473838128)
  one assert removed                      -> RED (run 36473848787)
  one expected literal loosened 9.77->9.7 -> RED (run 36473859580)
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_REL = Path("tests") / "LOCKED_MANIFEST.json"
MANIFEST = ROOT / MANIFEST_REL
FROZEN_GLOBS = (
    "scripts/check_locked_tests.py",
    "scripts/gate_*.py",
    "scripts/semgrep_to_sonar.py",
    ".github/workflows/gate-*.yml",
    ".semgrep/**/*",
)
PYTEST_CONFIGS = ("pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini")
HOOK_RE = re.compile(
    r"\bpytest_(collection_modifyitems|ignore_collect|collect_file|pycollect_makeitem|runtest_\w+|generate_tests|plugins)\b"
)
MIN_LOCKED = 1142
SKIP_RE = re.compile(r"\b(skip|skipif|xfail|expectedFailure|SkipTest|importorskip)\b")
ASSERT_CALL_RE = re.compile(r"^(assert\w*|raises|warns)$")


def _call_name(node: ast.Call) -> str:
    fn = node.func
    if isinstance(fn, ast.Attribute):
        return fn.attr
    if isinstance(fn, ast.Name):
        return fn.id
    return ""


def _body_without_docstring(fn: ast.AST) -> list[ast.stmt]:
    body = list(fn.body)
    first = body[0] if body else None
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
        body = body[1:]
    return body


def _module_skips(tree: ast.Module) -> list[str]:
    found = []
    for stmt in tree.body:
        if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if "pytestmark" in names and stmt.value is not None and SKIP_RE.search(ast.unparse(stmt.value)):
                found.append("module pytestmark")
        elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            if SKIP_RE.search(_call_name(stmt.value)):
                found.append(f"module-level {_call_name(stmt.value)}()")
    return found


def _find(tree: ast.Module, parts: list[str]) -> tuple[ast.AST | None, list[ast.ClassDef]]:
    scope: list[ast.stmt] = tree.body
    classes: list[ast.ClassDef] = []
    for i, name in enumerate(parts):
        last = i == len(parts) - 1
        match = None
        for node in scope:
            kind = (ast.FunctionDef, ast.AsyncFunctionDef) if last else ast.ClassDef
            if isinstance(node, kind) and node.name == name:
                match = node
        if match is None:
            return None, classes
        if not last:
            classes.append(match)
            scope = match.body
    return match, classes


def _defs(tree: ast.Module) -> dict[str, ast.AST]:
    out: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[node.name] = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name):
                    out[t.id] = node
    return out


def _is_helper_file(path: Path) -> bool:
    return path.name.startswith("test_") or path.name == "conftest.py" or "tests" in path.parts


class _Scope:
    """One test-side module: its definitions, the helper names it imports, and its conftest.py chain."""

    def __init__(self, rel: Path):
        self.rel = rel
        tree = ast.parse((ROOT / rel).read_text(), filename=str(rel))
        self.defs = _defs(tree)
        self.imports: dict[str, tuple[Path, str]] = {}
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module:
                target = self._resolve_module(node.module, node.level)
                if target is not None:
                    for alias in node.names:
                        self.imports[alias.asname or alias.name] = (target, alias.name)
        chain = []
        d = rel.parent
        while True:
            conftest = d / "conftest.py"
            if conftest != rel and (ROOT / conftest).is_file():
                chain.append(conftest)
            if d == Path("."):
                break
            d = d.parent
        self.conftests = chain

    def _resolve_module(self, module: str, level: int) -> Path | None:
        parts = module.split(".")
        bases = [self.rel.parent]
        if level == 0:
            d = self.rel.parent
            while d != Path("."):
                d = d.parent
                bases.append(d)
        for base in bases:
            for cand in (base.joinpath(*parts).with_suffix(".py"), base.joinpath(*parts, "__init__.py")):
                if (ROOT / cand).is_file() and _is_helper_file(cand):
                    return cand
        return None


def _scope(rel: Path, scopes: dict[Path, _Scope | None]) -> _Scope | None:
    if rel not in scopes:
        scopes[rel] = _Scope(rel) if (ROOT / rel).is_file() else None
    return scopes[rel]


def _lookup(name: str, scope: _Scope, scopes: dict[Path, _Scope | None], depth: int = 0):
    if name in scope.defs:
        return scope.defs[name], scope
    if name in scope.imports and depth < 8:
        target, attr = scope.imports[name]
        other = _scope(target, scopes)
        if other is not None:
            return _lookup(attr, other, scopes, depth + 1)
    for conftest in scope.conftests:
        other = _scope(conftest, scopes)
        if other is not None and name in other.defs:
            return other.defs[name], other
    return None


def helpers(fn: ast.AST, classes: list[ast.ClassDef], scope: _Scope, scopes: dict[Path, _Scope | None]) -> str:
    """Hash of every definition the test reaches by name, transitively: module-level functions, classes and
    constants in its file, the helper modules it imports from, and its conftest.py chain (fixtures included)."""
    class_defs = {n.name: n for c in classes for n in c.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    seen: dict[str, str] = {}
    stack: list[tuple[ast.AST, _Scope]] = [(fn, scope)]
    while stack:
        node, sc = stack.pop()
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names |= {a.arg for a in node.args.args + node.args.kwonlyargs}
        hits = [_lookup(name, sc, scopes) for name in sorted(names)]
        for attr in sorted(
            {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
        ):
            if attr in class_defs:
                hits.append((class_defs[attr], sc))
        for hit in hits:
            if hit is None or hit[0] is fn:
                continue
            target, owner = hit
            key = f"{owner.rel}:{getattr(target, 'name', None) or ast.unparse(target)[:40]}"
            if key not in seen:
                seen[key] = hashlib.sha256(ast.dump(target).encode()).hexdigest()[:16]
                stack.append((target, owner))
    return hashlib.sha256(json.dumps(sorted(seen.items())).encode()).hexdigest()[:16]


def fingerprint(fn: ast.AST) -> dict:
    asserts = 0
    literals = []
    nodes = [*fn.decorator_list, *_body_without_docstring(fn)]
    for top in nodes:
        for node in ast.walk(top):
            if isinstance(node, ast.Assert) or (isinstance(node, ast.Call) and ASSERT_CALL_RE.match(_call_name(node))):
                asserts += 1
            elif isinstance(node, ast.Constant):
                literals.append(f"{type(node.value).__name__}:{node.value!r}")
    digest = hashlib.sha256(json.dumps(sorted(literals)).encode()).hexdigest()[:16]
    return {"asserts": asserts, "literals": digest}


def skips(fn: ast.AST, classes: list[ast.ClassDef], tree: ast.Module) -> list[str]:
    found = _module_skips(tree)
    for owner in [*classes, fn]:
        for dec in owner.decorator_list:
            if SKIP_RE.search(ast.unparse(dec)):
                found.append(f"@{ast.unparse(dec)}")
    for node in ast.walk(ast.Module(body=_body_without_docstring(fn), type_ignores=[])):
        if isinstance(node, ast.Call) and SKIP_RE.search(_call_name(node)):
            found.append(f"{_call_name(node)}() in the body")
        elif isinstance(node, ast.Raise) and node.exc is not None and "SkipTest" in ast.unparse(node.exc):
            found.append("raise SkipTest in the body")
    return found


def inspect(test_id: str, cache: dict) -> tuple[dict | None, list[str]]:
    path, *parts = test_id.split("::")
    if path not in cache:
        file = ROOT / path
        cache[path] = ast.parse(file.read_text(), filename=path) if file.is_file() else None
    tree = cache[path]
    if tree is None:
        return None, [f"{path} is gone"]
    fn, classes = _find(tree, parts)
    if fn is None:
        return None, ["the test is gone"]
    rec = fingerprint(fn)
    scopes = cache.setdefault("__scopes__", {})
    rec["helpers"] = helpers(fn, classes, _scope(Path(path), scopes), scopes)
    return rec, skips(fn, classes, tree)


def load() -> dict:
    return json.loads(MANIFEST.read_text())


def save(doc: dict) -> None:
    doc["tests"] = dict(sorted(doc["tests"].items()))
    MANIFEST.write_text(json.dumps(doc, indent=1) + "\n")


def _judge(test_id: str, rec: dict, cache: dict[str, ast.Module | None]) -> list[str]:
    got, skipped = inspect(test_id, cache)
    if got is None:
        return [f"{test_id}: deleted ({skipped[0]})"]
    breaks = [f"{test_id}: skipped by {s}" for s in skipped]
    if got["asserts"] < rec["asserts"]:
        breaks.append(f"{test_id}: asserts dropped {rec['asserts']} -> {got['asserts']}")
    if got["literals"] != rec["literals"]:
        breaks.append(f"{test_id}: literals changed ({rec['literals']} -> {got['literals']})")
    if "helpers" in rec and got["helpers"] != rec["helpers"]:
        breaks.append(
            f"{test_id}: a helper, fixture or constant it reaches changed ({rec['helpers']} -> {got['helpers']})"
        )
    return breaks


def check() -> int:
    tests = load()["tests"]
    cache: dict[str, ast.Module | None] = {}
    breaks = []
    if len(tests) < MIN_LOCKED:
        breaks.append(f"the manifest lists {len(tests)} tests, under the floor of {MIN_LOCKED}")
    for test_id, rec in tests.items():
        breaks += _judge(test_id, rec, cache)
    for line in breaks:
        print(f"LOCKED {line}")
    print(f"locked tests: {len(tests)} read, {len(breaks)} breaks")
    return 1 if breaks else 0


def _frozen(root: Path) -> set[str]:
    return {str(f.relative_to(root)) for pattern in FROZEN_GLOBS for f in root.glob(pattern) if f.is_file()}


def _bytes(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


def _pytest_config(path: Path) -> str | None:
    if not path.is_file():
        return None
    text = path.read_text()
    if path.name == "pytest.ini":
        return text
    header = {"pyproject.toml": "[tool.pytest", "setup.cfg": "[tool:pytest]", "tox.ini": "[pytest]"}[path.name]
    if header not in text:
        return None
    section = text[text.index(header) :]
    nxt = section.find("\n[", 1)
    return section if nxt < 0 else section[:nxt]


def _collection_hooks(path: Path) -> int:
    if not path.is_file():
        return 0
    text = path.read_text()
    return len(HOOK_RE.findall(text)) + len(SKIP_RE.findall(text))


def _runner_breaks(base: Path, head: Path, test_ids: set[str]) -> list[str]:
    dirs: set[Path] = set()
    for test_id in test_ids:
        d = Path(test_id.split("::")[0]).parent
        while True:
            dirs.add(d)
            if d == Path("."):
                break
            d = d.parent
    breaks = []
    for d in sorted(dirs):
        conftest = d / "conftest.py"
        if _collection_hooks(head / conftest) > _collection_hooks(base / conftest):
            breaks.append(f"{conftest}: adds a collection hook or a skip over locked tests")
        for name in PYTEST_CONFIGS:
            if _pytest_config(base / d / name) != _pytest_config(head / d / name):
                breaks.append(f"{d / name}: pytest settings changed over locked tests")
    return breaks


def gate(base: Path, head: Path) -> int:
    global ROOT
    breaks = [
        f"{rel}: the gate's own file changed"
        for rel in sorted(_frozen(base))
        if _bytes(base / rel) != _bytes(head / rel)
    ]
    base_tests = json.loads((base / MANIFEST_REL).read_text())["tests"]
    head_doc = head / MANIFEST_REL
    head_tests = json.loads(head_doc.read_text())["tests"] if head_doc.is_file() else {}
    breaks += [f"{t}: manifest entry removed or altered" for t, rec in base_tests.items() if head_tests.get(t) != rec]
    ROOT = head
    cache: dict[str, ast.Module | None] = {}
    ids = set(base_tests) | set(head_tests)
    for test_id in sorted(ids):
        breaks += _judge(test_id, base_tests.get(test_id) or head_tests[test_id], cache)
    breaks += _runner_breaks(base, head, ids)
    for line in breaks:
        print(f"LOCKED {line}")
    added = len(set(head_tests) - set(base_tests))
    print(f"locked tests: {len(base_tests)} locked on the base, {added} added, {len(breaks)} breaks")
    return 1 if breaks else 0


def update() -> int:
    doc = load()
    cache: dict[str, ast.Module | None] = {}
    for test_id in doc["tests"]:
        got, skipped = inspect(test_id, cache)
        if got is None or skipped:
            print(f"cannot record {test_id}: {skipped}")
            return 1
        doc["tests"][test_id] = got
    save(doc)
    return 0


def add(files: list[str]) -> int:
    doc = load()
    for rel in files:
        tree = ast.parse((ROOT / rel).read_text(), filename=rel)
        ids = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                ids.append(f"{rel}::{node.name}")
            elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name.startswith("test"):
                        ids.append(f"{rel}::{node.name}::{sub.name}")
        cache: dict[str, ast.Module | None] = {}
        for test_id in ids:
            got, skipped = inspect(test_id, cache)
            if skipped:
                print(f"not locked, already skipped: {test_id} {skipped}")
                continue
            doc["tests"][test_id] = got
    save(doc)
    return 0


def _configure(path: str | None, section: str) -> None:
    """Override this module's upper-case settings from a project's code-exam JSON (section `locked_tests`)."""
    import json as _json

    cfg = Path(path) if path else Path(".github") / "code-exam.json"
    if not cfg.is_file():
        return
    for key, value in _json.loads(cfg.read_text()).get(section, {}).items():
        if key not in globals() or not key.isupper():
            raise SystemExit(f"code-exam config: {section}.{key} is not a setting of this check")
        current = globals()[key]
        if isinstance(current, Path):
            value = Path(value)
        elif isinstance(current, re.Pattern):
            value = re.compile(value, current.flags)
        elif isinstance(current, (tuple, set)):
            value = type(current)(value)
        globals()[key] = value


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", help="project code-exam JSON (default .github/code-exam.json)")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--add", nargs="+", metavar="FILE")
    ap.add_argument("--base-root", type=Path)
    ap.add_argument("--head-root", type=Path)
    args = ap.parse_args()
    _configure(args.config, "locked_tests")
    global MANIFEST
    MANIFEST = ROOT / MANIFEST_REL
    if args.base_root and args.head_root:
        return gate(args.base_root.resolve(), args.head_root.resolve())
    if args.add:
        return add(args.add)
    if args.update:
        return update()
    return check()


if __name__ == "__main__":
    sys.exit(main())
