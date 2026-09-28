#!/usr/bin/env python3
"""The mutation ledger: tests/MUTATION_LEDGER.json accounts for every surviving mutant.

A survivor is either killed (a test now turns it red, so it stops surviving) or
recorded as equivalent: `claimed_by` names the session that claims it changes no
output, `verdict` points to an adversarial-second-reader verdict file under
tests/mutation_verdicts/ whose line 1 is `reader: adversarial-second-reader
(launched by X)` (the agent's fresh context is the independence; X may be the
claimer), whose line 2 is `claims-by: Y` with Y the entry's claimer, whose line
for that exact mutant says CONFIRMED, and whose sha256 is the entry's
`verdict_sha256`.

The base's copy judges the PR's merge result H against its parent B, reading the
mutmut results the gate's measure job produced as data. Red:
  - a survivor the ledger does not hold as equivalent;
  - an equivalent entry with no verdict file, a verdict not written by the
    adversarial-second-reader agent, one answering another claimer, a hash
    mismatch, or no CONFIRMED line for that exact mutant;
  - a verdict file B holds that H edits or deletes (verdicts only accumulate);
  - an entry B holds that H removes or changes while the mutant still survives.

    python3 scripts/gate_mutation_ledger.py --results DIR --base-root B --head-root H
    python3 scripts/gate_mutation_ledger.py --changed-functions B H MODULE   # mutmut name globs to run
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from pathlib import Path

LEDGER = Path("tests") / "MUTATION_LEDGER.json"
VERDICTS = Path("tests") / "mutation_verdicts"
PACKAGE = "qitp_simulation"
SRC = Path("simulation") / "src" / PACKAGE
SURVIVED_RE = re.compile(r"^\s*([\w.ǁ]+__mutmut_\d+): survived\s*$")
READER_RE = re.compile(r"^reader:\s*adversarial-second-reader \(launched by ([^)\s]+)\)\s*$")
CLAIMS_RE = re.compile(r"^claims-by:\s*(\S+)\s*$")


def survivors(results: Path) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for f in sorted(results.glob("g1_mutants_*.txt")):
        module = f.stem.removeprefix("g1_mutants_")
        if module.endswith("_diffs"):
            continue
        out[module] = {m.group(1) for line in f.read_text().splitlines() if (m := SURVIVED_RE.match(line))}
    return out


def ledger(root: Path) -> dict[str, dict[str, dict]]:
    path = root / LEDGER
    return (json.loads(path.read_text()) if path.is_file() else {}).get("modules", {})


def verdict_breaks(root: Path, module: str, mutant: str, entry: dict) -> list[str]:
    ref = entry.get("verdict")
    where = f"{module} {mutant}"
    if not ref:
        return [f"{where}: equivalent with no verdict file"]
    path = root / ref
    if not path.is_file() or VERDICTS not in path.relative_to(root).parents:
        return [f"{where}: verdict {ref} is not a file under {VERDICTS}"]
    text = path.read_text()
    lines = text.splitlines() + ["", ""]
    reader, claims = READER_RE.match(lines[0]), CLAIMS_RE.match(lines[1])
    if not reader or not claims:
        return [
            f"{where}: verdict {ref} lacks line 1 `reader: adversarial-second-reader (launched by X)`"
            " or line 2 `claims-by: Y`"
        ]
    if claims.group(1) != entry.get("claimed_by"):
        return [
            f"{where}: verdict {ref} answers claims by {claims.group(1)}, the ledger says {entry.get('claimed_by')}"
        ]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if entry.get("verdict_sha256") != digest:
        return [f"{where}: verdict {ref} does not match the entry's verdict_sha256 ({digest})"]
    short = re.escape(mutant.split(".")[-1])
    confirmed = re.compile(rf"(?<![\w]){short}(?![\w]).*:\s*CONFIRMED\b")
    if not any(confirmed.search(line) for line in text.splitlines()[2:]):
        return [f"{where}: {ref} has no CONFIRMED line for it"]
    return []


def judge(found: dict[str, set[str]], base: Path, head: Path) -> list[str]:
    old, new = ledger(base), ledger(head)
    breaks = []
    for f in sorted((base / VERDICTS).glob("*")) if (base / VERDICTS).is_dir() else []:
        rel = f.relative_to(base)
        if not (head / rel).is_file() or (head / rel).read_bytes() != f.read_bytes():
            breaks.append(f"{rel}: a verdict file was edited or deleted")
    for module, alive in sorted(found.items()):
        entries = new.get(module, {})
        for mutant in sorted(alive):
            entry = entries.get(mutant)
            if not entry or entry.get("status") != "equivalent":
                breaks.append(f"{module} {mutant}: survived, not in the ledger")
                continue
            breaks += verdict_breaks(head, module, mutant, entry)
        for mutant, was in sorted(old.get(module, {}).items()):
            if mutant in alive and entries.get(mutant) != was:
                breaks.append(f"{module} {mutant}: ledger entry removed or changed while it still survives")
    return breaks


def _functions(root: Path, module: str) -> dict[str, str]:
    path = root / SRC / f"{module}.py"
    if not path.is_file():
        return {}
    out = {}
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[f"x_{node.name}"] = hashlib.sha256(ast.dump(node).encode()).hexdigest()
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out[f"xǁ{node.name}ǁ{sub.name}"] = hashlib.sha256(ast.dump(sub).encode()).hexdigest()
    return out


def changed_functions(base: Path, head: Path, module: str) -> list[str]:
    was, now = _functions(base, module), _functions(head, module)
    return [f"{PACKAGE}.{module}.{name}__mutmut_*" for name, digest in sorted(now.items()) if was.get(name) != digest]


def _configure(path: str | None, section: str) -> None:
    """Override this module's upper-case settings from a project's code-exam JSON (section `mutation_ledger`)."""
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
    ap.add_argument("--results", type=Path)
    ap.add_argument("--base-root", type=Path)
    ap.add_argument("--head-root", type=Path)
    ap.add_argument("--changed-functions", nargs=3, metavar=("BASE", "HEAD", "MODULE"))
    args = ap.parse_args()
    _configure(args.config, "mutation_ledger")
    if args.changed_functions:
        base, head, module = args.changed_functions
        print("\n".join(changed_functions(Path(base), Path(head), module)))
        return 0
    found = survivors(args.results)
    breaks = judge(found, args.base_root.resolve(), args.head_root.resolve())
    for line in breaks:
        print(f"MUTANT {line}")
    total = sum(len(v) for v in found.values())
    print(f"mutation ledger: {len(found)} modules run, {total} survivors, {len(breaks)} breaks")
    return 1 if breaks else 0


if __name__ == "__main__":
    sys.exit(main())
