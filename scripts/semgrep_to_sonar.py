#!/usr/bin/env python3
"""Semgrep JSON -> SonarQube generic external issues (10.3+ format).

Usage: semgrep_to_sonar.py semgrep.json semgrep_sonar.json
"""

from __future__ import annotations

import json
import sys


def convert(report: dict) -> dict:
    rules: dict[str, dict] = {}
    issues = []
    for result in report.get("results", []):
        rule_id = result["check_id"].rsplit(".", 1)[-1]
        message = result["extra"]["message"].strip()
        rules.setdefault(
            rule_id,
            {
                "id": rule_id,
                "name": rule_id,
                "description": message,
                "engineId": "semgrep",
                "cleanCodeAttribute": "LOGICAL",
                "impacts": [{"softwareQuality": "RELIABILITY", "severity": "HIGH"}],
            },
        )
        start, end = result["start"], result["end"]
        text_range = {"startLine": start["line"], "endLine": end["line"]}
        if (start["line"], start["col"]) < (end["line"], end["col"]):
            text_range["startColumn"] = start["col"] - 1
            text_range["endColumn"] = end["col"] - 1
        issues.append(
            {
                "ruleId": rule_id,
                "effortMinutes": 30,
                "primaryLocation": {"message": message, "filePath": result["path"], "textRange": text_range},
            }
        )
    return {"rules": list(rules.values()), "issues": issues}


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        sys.exit(__doc__)
    with open(argv[1]) as fh:
        report = json.load(fh)
    with open(argv[2], "w") as fh:
        json.dump(convert(report), fh, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
