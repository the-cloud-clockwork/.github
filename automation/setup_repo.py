#!/usr/bin/env python3
import argparse
import base64
import json
import subprocess
import sys
from pathlib import Path

ORG = "the-cloud-clockwork"
BRANCH = "dev"
DEV_REF = f"refs/heads/{BRANCH}"
SETUP_BRANCH = "ci-standard-setup"
CALLER_PATH = ".github/workflows/shared-tests.yml"
CALLER_SOURCE = Path(__file__).with_name("shared-tests.yml")
RULESET_FIELDS = ("name", "target", "enforcement", "conditions", "rules", "bypass_actors")

MERGE_QUEUE = {
    "grouping_strategy": "ALLGREEN",
    "max_entries_to_build": 1,
    "max_entries_to_merge": 5,
    "min_entries_to_merge": 1,
    "min_entries_to_merge_wait_minutes": 1,
}
MERGE_QUEUE_DEFAULTS = {"check_response_timeout_minutes": 60, "merge_method": "SQUASH"}


def gh(args: list[str], body: dict | None = None) -> dict | list | None:
    result = subprocess.run(
        ["gh", "api", *args, *(["--input", "-"] if body is not None else [])],
        input=json.dumps(body) if body is not None else None,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        if "HTTP 404" in result.stderr:
            return None
        sys.exit(f"gh api {' '.join(args)} failed: {result.stderr.strip()}")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def protects_dev(ruleset: dict) -> bool:
    include = ruleset.get("conditions", {}).get("ref_name", {}).get("include", [])
    return DEV_REF in include


def dev_ruleset(repo: str) -> dict | None:
    summaries = gh([f"repos/{ORG}/{repo}/rulesets"]) or []
    rulesets = [gh([f"repos/{ORG}/{repo}/rulesets/{item['id']}"]) for item in summaries]
    dev = [item for item in rulesets if item and item["target"] == "branch" and protects_dev(item)]
    queued = [item for item in dev if any(rule["type"] == "merge_queue" for rule in item["rules"])]
    if len(queued) > 1:
        sys.exit(f"{repo}: more than one dev ruleset carries a merge queue")
    return (queued or dev or [None])[0]


def with_merge_queue(rules: list[dict]) -> list[dict]:
    current = next((rule for rule in rules if rule["type"] == "merge_queue"), None)
    params = {**(current or {}).get("parameters", {}), **MERGE_QUEUE}
    params = {**params, **{key: params.get(key, value) for key, value in MERGE_QUEUE_DEFAULTS.items()}}
    queue = {"type": "merge_queue", "parameters": params}
    if current is None:
        return [*rules, queue]
    return [queue if rule is current else rule for rule in rules]


def ruleset_body(ruleset: dict | None, stored: tuple[str, dict] | None) -> dict:
    if stored:
        body = {key: stored[1][key] for key in RULESET_FIELDS if key in stored[1]}
        return {**body, "rules": with_merge_queue(body["rules"])}
    if ruleset is None:
        return {
            "name": "dev-merge-queue",
            "target": "branch",
            "enforcement": "active",
            "conditions": {"ref_name": {"include": [DEV_REF], "exclude": []}},
            "rules": with_merge_queue([]),
            "bypass_actors": [],
        }
    body = {key: ruleset[key] for key in RULESET_FIELDS if key in ruleset}
    body["rules"] = with_merge_queue(
        [{key: rule[key] for key in ("type", "parameters") if rule.get(key) is not None} for rule in ruleset["rules"]]
    )
    return body


def read_file(repo: str, path: str, ref: str) -> tuple[str, str] | None:
    found = gh([f"repos/{ORG}/{repo}/contents/{path}?ref={ref}"])
    if not found:
        return None
    return base64.b64decode(found["content"]).decode(), found["sha"]


def stored_payload(repo: str, ruleset_name: str) -> tuple[str, dict] | None:
    path = f".github/rulesets/{ruleset_name}.json"
    found = read_file(repo, path, BRANCH)
    return (path, json.loads(found[0])) if found else None


def planned_files(repo: str, stored: tuple[str, dict] | None, body: dict) -> dict[str, str]:
    files = {CALLER_PATH: CALLER_SOURCE.read_text()}
    if stored:
        files[stored[0]] = json.dumps({**stored[1], "rules": body["rules"]}, indent=2) + "\n"
    return {path: text for path, text in files.items() if (read_file(repo, path, BRANCH) or ("",))[0] != text}


def apply_ruleset(repo: str, ruleset: dict | None, body: dict) -> None:
    endpoint = f"repos/{ORG}/{repo}/rulesets"
    if ruleset is None:
        written = gh([endpoint, "--method", "POST"], body)
    else:
        written = gh([f"{endpoint}/{ruleset['id']}", "--method", "PUT"], body)
    queue = next(rule for rule in written["rules"] if rule["type"] == "merge_queue")
    if any(queue["parameters"][key] != value for key, value in MERGE_QUEUE.items()):
        sys.exit(f"{repo}: merge queue readback differs: {queue['parameters']}")
    print(f"ruleset {written['name']} ({written['id']}) written")


def open_pull_request(repo: str, files: dict[str, str]) -> None:
    base = gh([f"repos/{ORG}/{repo}/git/ref/heads/{BRANCH}"])["object"]["sha"]
    if gh([f"repos/{ORG}/{repo}/git/ref/heads/{SETUP_BRANCH}"]):
        gh([f"repos/{ORG}/{repo}/git/refs/heads/{SETUP_BRANCH}", "--method", "PATCH"], {"sha": base, "force": True})
    else:
        gh([f"repos/{ORG}/{repo}/git/refs", "--method", "POST"], {"ref": f"refs/heads/{SETUP_BRANCH}", "sha": base})
    for path, text in files.items():
        existing = read_file(repo, path, SETUP_BRANCH)
        content = {
            "message": f"Add the org CI standard: {path}",
            "content": base64.b64encode(text.encode()).decode(),
            "branch": SETUP_BRANCH,
            **({"sha": existing[1]} if existing else {}),
        }
        gh([f"repos/{ORG}/{repo}/contents/{path}", "--method", "PUT"], content)
    opened = gh([f"repos/{ORG}/{repo}/pulls?head={ORG}:{SETUP_BRANCH}&base={BRANCH}&state=open"])
    if opened:
        print(f"pull request updated: {opened[0]['html_url']}")
        return
    created = gh(
        [f"repos/{ORG}/{repo}/pulls", "--method", "POST"],
        {
            "title": "Adopt the org CI standard",
            "head": SETUP_BRANCH,
            "base": BRANCH,
            "body": "Opened by the org setup workflow. See "
            f"https://github.com/{ORG}/.github/tree/main/automation for why.\n\n"
            + "\n".join(f"- `{path}`" for path in files),
        },
    )
    print(f"pull request opened: {created['html_url']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Give one repository the org CI standard.")
    parser.add_argument("--repo", required=True, help="repository name in the organization")
    parser.add_argument("--dry-run", action="store_true", help="print the settings and files without writing")
    args = parser.parse_args()

    ruleset = dev_ruleset(args.repo)
    stored = stored_payload(args.repo, ruleset["name"]) if ruleset else None
    body = ruleset_body(ruleset, stored)
    files = planned_files(args.repo, stored, body)
    before = next((rule for rule in (ruleset or {"rules": []})["rules"] if rule["type"] == "merge_queue"), None)
    after = next(rule for rule in body["rules"] if rule["type"] == "merge_queue")

    print(f"repository: {ORG}/{args.repo}")
    print(f"dev ruleset: {body['name']} ({ruleset['id'] if ruleset else 'new'})")
    print(f"ruleset source: {stored[0] if stored else 'live ruleset'}")
    print(f"merge queue before: {json.dumps((before or {}).get('parameters'), sort_keys=True)}")
    print(f"merge queue after:  {json.dumps(after['parameters'], sort_keys=True)}")
    print(f"pull request into {BRANCH} from {SETUP_BRANCH}: {', '.join(files) or 'nothing to change'}")
    if args.dry_run:
        print("\n--- ruleset body ---")
        print(json.dumps(body, indent=2))
        for path, text in files.items():
            print(f"\n--- {path} ---")
            print(text, end="")
        return

    apply_ruleset(args.repo, ruleset, body)
    if files:
        open_pull_request(args.repo, files)


if __name__ == "__main__":
    main()
