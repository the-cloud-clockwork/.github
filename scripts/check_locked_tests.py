#!/usr/bin/env python3
"""The protected test list: a JSON manifest of locked pytest tests.

Every listed test must still exist, carry no skip or xfail (decorator, module
`pytestmark`, or a skip call in its body), keep at least its recorded number of
asserts, and keep its literals (the expected values, parametrize rows and
inputs) byte-identical to the recorded fingerprint. A change to any of these
lands only with the manifest changed in the same PR; the project's CODEOWNERS
names who approves the manifest. The check reads source with `ast` and runs no
test, so it holds whatever the rest of the suite shows. Run from the repo root;
test ids are `path/test_x.py::test_name` or `path/test_x.py::TestClass::test_name`.

    python3 check_locked_tests.py [--manifest tests/LOCKED_MANIFEST.json]   # check
    python3 check_locked_tests.py --update                                  # re-record
    python3 check_locked_tests.py --add tests/test_x.py                     # lock a file

The manifest's `min_tests` is the floor on how many tests it lists (`--min`
overrides), so a manifest emptied in a PR is red too.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path.cwd()
MANIFEST = ROOT / "tests" / "LOCKED_MANIFEST.json"
MIN_LOCKED = 1
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


def inspect(test_id: str, cache: dict[str, ast.Module | None]) -> tuple[dict | None, list[str]]:
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
    return fingerprint(fn), skips(fn, classes, tree)


def load() -> dict:
    return json.loads(MANIFEST.read_text())


def save(doc: dict) -> None:
    doc["tests"] = dict(sorted(doc["tests"].items()))
    MANIFEST.write_text(json.dumps(doc, indent=1) + "\n")


def check() -> int:
    doc = load()
    tests = doc["tests"]
    floor = MIN_LOCKED if MIN_LOCKED > 1 else int(doc.get("min_tests", 1))
    cache: dict[str, ast.Module | None] = {}
    breaks = []
    if len(tests) < floor:
        breaks.append(f"the manifest lists {len(tests)} tests, under the floor of {floor}")
    for test_id, rec in tests.items():
        got, skipped = inspect(test_id, cache)
        if got is None:
            breaks.append(f"{test_id}: deleted ({skipped[0]})")
            continue
        for s in skipped:
            breaks.append(f"{test_id}: skipped by {s}")
        if got["asserts"] < rec["asserts"]:
            breaks.append(f"{test_id}: asserts dropped {rec['asserts']} -> {got['asserts']}")
        if got["literals"] != rec["literals"]:
            breaks.append(f"{test_id}: literals changed ({rec['literals']} -> {got['literals']})")
    for line in breaks:
        print(f"LOCKED {line}")
    print(f"locked tests: {len(tests)} read, {len(breaks)} breaks")
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


def main() -> int:
    global MANIFEST, MIN_LOCKED
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="tests/LOCKED_MANIFEST.json")
    ap.add_argument("--min", type=int, default=1)
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--add", nargs="+", metavar="FILE")
    args = ap.parse_args()
    MANIFEST = ROOT / args.manifest
    MIN_LOCKED = args.min
    if args.add:
        return add(args.add)
    if args.update:
        return update()
    return check()


if __name__ == "__main__":
    sys.exit(main())
