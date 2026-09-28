#!/usr/bin/env python3
"""The rule catalog: tests/RULE_CATALOG.yaml, the finite map of the engine.

Every part (1-22) and every numbered row of .claude/rules/backtest-trust.md has
an entry: its spec in one line with the source, the engine functions that
implement it, the locked tests that pin it and the mutants those tests kill.
Every function in MODULES is claimed by an entry or listed as infra with a
reason. What is not yet true sits in `pending`, and `pending` only shrinks.

    python3 scripts/gate_rule_catalog.py --base-root B --head-root H   # CI: gate-catalog
    python3 scripts/gate_rule_catalog.py                               # this tree against itself
    python3 scripts/gate_rule_catalog.py --seed                        # rewrite the seed (first run only)

Red on the PR's merge result H against its parent B, the base's copy judging:
  - a part or row in H's backtest-trust.md with no entry;
  - an entry with no locked test or no mutant, unless B's `pending` lists it;
  - a locked test an entry names that H's manifest does not lock;
  - a function in MODULES that no entry claims and infra does not list, unless B's `pending` lists it;
  - an entry or infra line naming a function that does not exist;
  - an infra line with no reason;
  - `pending` growing, or an entry B holds losing a locked test or a mutant.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CATALOG = Path("tests") / "RULE_CATALOG.yaml"
MANIFEST = Path("tests") / "LOCKED_MANIFEST.json"
TRUST = Path(".claude") / "rules" / "backtest-trust.md"
SRC = Path("simulation") / "src" / "qitp_simulation"
MODULES = (
    "engine",
    "exit_geometry",
    "fill_audit",
    "print_tape",
    "second_bars",
    "liquidity",
    "slippage",
    "print_validity",
    "ticks",
    "broker_profiles",
)
PART_RE = re.compile(r"^\| (\d{1,2}) \| ([^|]+?) \| (G\d) \| ([^|]+?) \| ([^|]+?) \|")
ROW_RE = re.compile(r"^\|\s*([^|]*?)\s*\|\s*([^|]+?)\s*\|\s*(\d{3})\s*\|")
MIN_RULES = 40
MIN_FUNCTIONS = 200


def rules_in(root: Path) -> dict[str, dict]:
    found: dict[str, dict] = {}
    part = ""
    for line in (root / TRUST).read_text().splitlines():
        m = PART_RE.match(line)
        if m:
            found[f"part-{int(m.group(1))}"] = {"title": m.group(2).strip(), "spec": m.group(4).strip()}
            continue
        m = ROW_RE.match(line)
        if m:
            part = m.group(1).strip() or part
            text = m.group(2).strip()
            found.setdefault(f"row-{m.group(3)}", {"title": text, "spec": text, "part": part})
    return found


def functions_in(root: Path, text: dict[str, str] | None = None) -> dict[str, ast.AST]:
    out: dict[str, ast.AST] = {}
    for mod in MODULES:
        source = (root / SRC / f"{mod}.py").read_text()
        if text is not None:
            text[mod] = source
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out[f"{mod}:{node.name}"] = node
            elif isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        out[f"{mod}:{node.name}.{sub.name}"] = sub
    return out


def load(root: Path) -> dict:
    path = root / CATALOG
    doc = yaml.safe_load(path.read_text()) if path.is_file() else None
    doc = doc or {}
    doc.setdefault("rules", {})
    doc.setdefault("infra", {})
    pending = doc.setdefault("pending", {})
    for key in ("unclaimed", "no_test", "no_mutant"):
        pending[key] = list(pending.get(key) or [])
    return doc


def judge(base: Path, head: Path) -> list[str]:
    old, new = load(base), load(head)
    locked = set(json.loads((head / MANIFEST).read_text())["tests"])
    functions = functions_in(head)
    wanted = rules_in(head)
    rules, infra, pending = new["rules"], new["infra"], new["pending"]
    breaks = []
    if len(wanted) < MIN_RULES:
        breaks.append(f"{TRUST} yields {len(wanted)} parts and rows, under the floor of {MIN_RULES}")
    if len(functions) < MIN_FUNCTIONS:
        breaks.append(f"MODULES yield {len(functions)} functions, under the floor of {MIN_FUNCTIONS}")
    for rid in sorted(set(wanted) - set(rules)):
        breaks.append(f"{rid}: no catalog entry ({wanted[rid]['title'][:60]})")
    for key in ("unclaimed", "no_test", "no_mutant"):
        for item in sorted(set(pending[key]) - set(old["pending"][key])):
            breaks.append(f"pending.{key} grew: {item}")
    claimed: set[str] = set()
    for rid, entry in sorted(rules.items()):
        entry = entry or {}
        tests = list(entry.get("locked_tests") or [])
        mutants = list(entry.get("mutants") or [])
        if not tests and rid not in old["pending"]["no_test"]:
            breaks.append(f"{rid}: names no locked test")
        if not mutants and rid not in old["pending"]["no_mutant"]:
            breaks.append(f"{rid}: names no mutant")
        for t in tests:
            if t not in locked:
                breaks.append(f"{rid}: {t} is not a locked test")
        for fid in entry.get("functions") or []:
            if fid not in functions:
                breaks.append(f"{rid}: claims {fid}, which does not exist")
            claimed.add(fid)
        was = (old["rules"].get(rid) or {}) if rid in old["rules"] else {}
        for field in ("locked_tests", "mutants"):
            for item in sorted(set(was.get(field) or []) - set(entry.get(field) or [])):
                breaks.append(f"{rid}: dropped {field[:-1].replace('_', ' ')} {item}")
    for rid in sorted(set(old["rules"]) - set(rules)):
        breaks.append(f"{rid}: entry removed")
    for fid, reason in sorted(infra.items()):
        if fid not in functions:
            breaks.append(f"infra {fid} does not exist")
        if not str(reason or "").strip():
            breaks.append(f"infra {fid} has no reason")
    for fid in sorted(set(functions) - claimed - set(infra) - set(old["pending"]["unclaimed"])):
        breaks.append(f"{fid}: no rule claims it and infra does not list it")
    return breaks


def seed() -> int:
    doc = load(ROOT)
    manifest = json.loads((ROOT / MANIFEST).read_text())["tests"]
    text: dict[str, str] = {}
    functions = functions_in(ROOT, text)
    sources = {fid: ast.get_source_segment(text[fid.split(":")[0]], node) or "" for fid, node in functions.items()}
    test_text: dict[str, str] = {}
    for tid in manifest:
        path = tid.split("::")[0]
        if path not in test_text:
            test_text[path] = (ROOT / path).read_text()
    rules = doc["rules"]
    for rid, meta in rules_in(ROOT).items():
        entry = rules.setdefault(rid, {})
        entry.setdefault("title", meta["title"])
        entry.setdefault("spec", meta["spec"])
        entry.setdefault("source", f"backtest-trust.md {rid.replace('-', ' ')}")
        entry.setdefault("functions", [])
        entry.setdefault("locked_tests", [])
        entry.setdefault("mutants", [])
        if rid.startswith("row-"):
            n = rid[4:]
            cite = re.compile(rf"(\brows?\s+(\d{{3}}\s*[,/]\s*)*{n}\b|FOLLOW-UPS\s+{n}\b|\({n}\))")
            entry["functions"] = sorted(set(entry["functions"]) | {f for f, s in sources.items() if cite.search(s)})
            entry["locked_tests"] = sorted(
                set(entry["locked_tests"]) | {t for t in manifest if cite.search(test_text[t.split("::")[0]])}
            )
    claimed = {f for e in rules.values() for f in e.get("functions") or []}
    doc["pending"] = {
        "unclaimed": sorted(set(functions) - claimed - set(doc["infra"])),
        "no_test": sorted(r for r, e in rules.items() if not e.get("locked_tests")),
        "no_mutant": sorted(r for r, e in rules.items() if not e.get("mutants")),
    }
    (ROOT / CATALOG).write_text(yaml.safe_dump(doc, sort_keys=True, width=120, allow_unicode=True))
    p = doc["pending"]
    print(
        f"seeded {len(rules)} entries; pending: {len(p['unclaimed'])} unclaimed functions, "
        f"{len(p['no_test'])} without a locked test, {len(p['no_mutant'])} without a mutant"
    )
    return 0


def _configure(path: str | None, section: str) -> None:
    """Override this module's upper-case settings from a project's code-exam JSON (section `rule_catalog`)."""
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
    ap.add_argument("--base-root", type=Path)
    ap.add_argument("--head-root", type=Path)
    ap.add_argument("--seed", action="store_true")
    args = ap.parse_args()
    _configure(args.config, "rule_catalog")
    if args.seed:
        return seed()
    base = (args.base_root or ROOT).resolve()
    head = (args.head_root or ROOT).resolve()
    breaks = judge(base, head)
    for line in breaks:
        print(f"CATALOG {line}")
    p = load(head)["pending"]
    print(
        f"rule catalog: {len(load(head)['rules'])} entries, {len(breaks)} breaks; pending {len(p['unclaimed'])} "
        f"unclaimed functions, {len(p['no_test'])} without a locked test, {len(p['no_mutant'])} without a mutant"
    )
    return 1 if breaks else 0


if __name__ == "__main__":
    sys.exit(main())
