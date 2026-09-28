#!/usr/bin/env python3
"""The coverage ratchet: tests/COVERAGE_BASELINE.json only goes up.

The base's copy of this file judges the PR's merge result H against its parent B,
reading H's measured coverage (coverage.py JSON, measured by gate-coverage.yml
from the base's workflow) as data:
  - a module in MODULES under B's baseline, lines or branches, is red;
  - H's baseline below B's is red, and any value H changes must equal what H
    measured: a raise is the run's own number, nothing else;
  - while B's baseline is empty, only a seed PR may write it: one that changes
    nothing but the baseline, so its merge result is dev and its measurement is
    dev's;
  - H adding a coverage hook (settrace, sitecustomize, a .pth file, coverage's
    own API or settings) under simulation/ that B does not have is red.

    python3 scripts/gate_coverage_ratchet.py --coverage cov.json --base-root B --head-root H
    python3 scripts/gate_coverage_ratchet.py --coverage cov.json --record   # write the measured values
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = Path("tests") / "COVERAGE_BASELINE.json"
PACKAGE = "simulation/src/qitp_simulation"
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
TAMPER_RE = re.compile(
    r"\bsys\.settrace\b|\bsys\.setprofile\b|\bthreading\.settrace\b|\bsitecustomize\b|\busercustomize\b"
    r"|^\s*(import|from)\s+coverage\b|COVERAGE_PROCESS_START|COVERAGE_FILE|\[tool\.coverage|\[coverage:",
    re.M,
)
TAMPER_SUFFIXES = {".py", ".toml", ".cfg", ".ini", ".pth", ".sh"}
TOLERANCE = 0.01


def measured(cov_path: Path) -> dict[str, dict[str, float]]:
    files = json.loads(cov_path.read_text())["files"]
    out = {}
    for mod in MODULES:
        entry = next((v for k, v in files.items() if k.replace("\\", "/").endswith(f"{PACKAGE}/{mod}.py")), None)
        if entry is None:
            out[mod] = {"lines": 0.0, "branches": 0.0}
            continue
        s = entry["summary"]
        lines = 100.0 * s["covered_lines"] / s["num_statements"] if s["num_statements"] else 100.0
        branches = 100.0 * s.get("covered_branches", 0) / s["num_branches"] if s.get("num_branches") else 100.0
        out[mod] = {"lines": round(lines, 2), "branches": round(branches, 2)}
    return out


def baseline(root: Path) -> dict[str, dict[str, float]]:
    path = root / BASELINE
    return json.loads(path.read_text()) if path.is_file() else {}


def tamper_count(root: Path) -> dict[str, int]:
    counts = {}
    for f in (root / "simulation").rglob("*"):
        if f.is_file() and f.suffix in TAMPER_SUFFIXES and ".pr" not in f.parts:
            n = len(TAMPER_RE.findall(f.read_text(errors="replace"))) + (1 if f.suffix == ".pth" else 0)
            if n:
                counts[str(f.relative_to(root))] = n
    return counts


def judge(cov: dict, base: Path, head: Path, changed: list[str]) -> list[str]:
    old, new = baseline(base), baseline(head)
    breaks = []
    if not old and new and [c for c in changed if c != str(BASELINE)]:
        breaks.append(
            f"the first baseline must come alone (a seed PR touching only {BASELINE}), this PR also changes "
            f"{len([c for c in changed if c != str(BASELINE)])} other files"
        )
    for mod in MODULES:
        for kind in ("lines", "branches"):
            got = cov[mod][kind]
            floor = old.get(mod, {}).get(kind)
            claim = new.get(mod, {}).get(kind)
            if floor is not None and got + TOLERANCE < floor:
                breaks.append(f"{mod} {kind}: {got:.2f} % measured, under the baseline {floor:.2f} %")
            if floor is not None and (claim is None or claim + TOLERANCE < floor):
                breaks.append(f"{mod} {kind}: the baseline was lowered ({floor} -> {claim})")
            if claim is not None and claim != floor and abs(claim - got) > TOLERANCE:
                breaks.append(f"{mod} {kind}: the baseline says {claim:.2f} %, the run measured {got:.2f} %")
    was, now = tamper_count(base), tamper_count(head)
    for rel, n in sorted(now.items()):
        if n > was.get(rel, 0):
            breaks.append(f"{rel}: adds a coverage hook or setting ({was.get(rel, 0)} -> {n})")
    return breaks


def _configure(path: str | None, section: str) -> None:
    """Override this module's upper-case settings from a project's code-exam JSON (section `coverage_ratchet`)."""
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
    ap.add_argument("--coverage", type=Path, required=True)
    ap.add_argument("--base-root", type=Path)
    ap.add_argument("--head-root", type=Path)
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--changed", type=Path, help="the PR's changed paths, one per line")
    args = ap.parse_args()
    _configure(args.config, "coverage_ratchet")
    cov = measured(args.coverage)
    for mod in MODULES:
        print(f"{mod}: lines {cov[mod]['lines']:.2f} %, branches {cov[mod]['branches']:.2f} %")
    if args.record:
        (ROOT / BASELINE).write_text(json.dumps(cov, indent=1, sort_keys=True) + "\n")
        return 0
    changed = args.changed.read_text().split() if args.changed else []
    breaks = judge(cov, (args.base_root or ROOT).resolve(), (args.head_root or ROOT).resolve(), changed)
    for line in breaks:
        print(f"COVERAGE {line}")
    print(f"coverage ratchet: {len(MODULES)} modules, {len(breaks)} breaks")
    return 1 if breaks else 0


if __name__ == "__main__":
    sys.exit(main())
