import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/semgrep-reusable.yml"
STEP = "- name: Fail on findings new against the base"

STUB = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    set -euo pipefail
    n=$(( $(cat "$STUB/count") + 1 ))
    echo "$n" > "$STUB/count"
    printf '%s\\n' "$*" >> "$STUB/args"
    for arg in "$@"; do
      if [[ "$arg" == --json-output=* && -f "$STUB/report_$n.json" ]]; then cp "$STUB/report_$n.json" "${arg#--json-output=}"; fi
    done
    exit "$(cat "$STUB/code_$n")"
    """
)

TIMEOUT = {"results": [], "errors": [{"type": "Timeout", "rule_id": "r", "path": "a.py"}]}
FINDING = {"results": [{"check_id": "r"}], "errors": []}
MIXED = {"results": [], "errors": [{"type": "Timeout"}, {"type": "SemgrepError"}]}


def step_script() -> str:
    lines = WORKFLOW.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == STEP)
    run = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |")
    indent = len(lines[run]) - len(lines[run].lstrip()) + 2
    body = []
    for line in lines[run + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        body.append(line[indent:])
    return "\n".join(body) + "\n"


class RescanTests(unittest.TestCase):
    def scan(self, *calls):
        with tempfile.TemporaryDirectory() as folder:
            stub = Path(folder)
            (stub / "count").write_text("0")
            for n, (code, report) in enumerate(calls, start=1):
                (stub / f"code_{n}").write_text(str(code))
                if report is not None:
                    (stub / f"report_{n}.json").write_text(json.dumps(report))
            (stub / "bin").mkdir()
            (stub / "bin/semgrep").write_text(STUB)
            (stub / "bin/semgrep").chmod(0o755)
            env = {
                **os.environ,
                "PATH": f"{stub / 'bin'}:{os.environ['PATH']}",
                "STUB": folder,
                "RUNNER_TEMP": folder,
                "BASE": "abc123",
                "CONFIGS": "p/ci p/secrets",
                "EXCLUDE_RULES": "rule.one",
                "TIMEOUT": "30",
            }
            done = subprocess.run(["bash", "-e", "-c", step_script()], env=env, capture_output=True, text=True)
            return done.returncode, (stub / "args").read_text().splitlines()

    def test_clean_scan_passes_once_with_every_input(self):
        code, args = self.scan((0, {"results": [], "errors": []}))
        self.assertEqual(code, 0)
        self.assertEqual(len(args), 1)
        words = args[0].split()
        self.assertEqual(words[:5], ["scan", "--error", "--strict", "--verbose", "--timeout"])
        for pair in (["--config", "p/ci"], ["--config", "p/secrets"], ["--exclude-rule", "rule.one"]):
            self.assertIn(" ".join(pair), args[0])
        self.assertIn("--baseline-commit abc123", args[0])

    def test_timeouts_alone_scan_once_more_and_grade_the_second_scan(self):
        code, args = self.scan((2, TIMEOUT), (0, {"results": [], "errors": []}))
        self.assertEqual((code, len(args)), (0, 2))
        code, args = self.scan((2, TIMEOUT), (2, TIMEOUT))
        self.assertEqual((code, len(args)), (2, 2))

    def test_a_finding_fails_without_a_second_scan(self):
        code, args = self.scan((1, FINDING))
        self.assertEqual((code, len(args)), (1, 1))

    def test_other_errors_or_no_report_fail_without_a_second_scan(self):
        for calls in ([(2, MIXED)], [(2, None)]):
            with self.subTest(calls=calls):
                code, args = self.scan(*calls)
                self.assertEqual((code, len(args)), (2, 1))


if __name__ == "__main__":
    unittest.main()
