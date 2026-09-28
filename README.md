# .github

Organization-wide GitHub configuration, reusable workflows, and shared templates.

## Reusable Workflows

All reusable workflows live in `.github/workflows/` and are referenced as:

```
The-Cloud-Clock-Work/.github/.github/workflows/<name>@main
```

| Workflow | Purpose | Key Inputs | Secrets |
|----------|---------|------------|---------|
| `claude-reusable.yml` | Claude Code Action (issue/PR assistant) | `model`, `trigger_phrase`, `allowed_tools`, `timeout_minutes` | `ANTHROPIC_AUTH_TOKEN` (required), `ANTHROPIC_BASE_URL` (required) |
| `release-reusable.yml` | Semver bump → commit → tag → push | `bump` (required), `branch`, `version_file` | `RELEASE_TOKEN` (optional) |
| `ghcr-build-reusable.yml` | Build + push Docker image to GHCR | `dockerfile`, `context` | _(uses `GITHUB_TOKEN`)_ |
| `docker-publish-reusable.yml` | Build + push Docker image to DockerHub | `image_name` (required), `context` | `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN` |
| `pypi-publish-reusable.yml` | Build + publish to PyPI via OIDC | `python_version` | _(OIDC trusted publishers)_ |
| `docs-audit-reusable.yml` | Claude-powered docs audit with MCP (self-contained prompt) | `scope`, `extra_instructions`, `anthropic_url_base` (required), + more | `ANTHROPIC_TOKEN_BASE`, `RELEASE_TOKEN`, `MCP_API_KEY` |
| `sonar-reusable.yml` | SonarQube scan through the Cloudflare Access proxy, quality gate | `python_version`, `test_command`, `scanner_args`, `external_issues_artifact` | `SONAR_TOKEN`, `SONAR_HOST_URL`, `CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET` |
| `code-exam-reusable.yml` | Locked tests, Semgrep rules, rule catalog, coverage ratchet, mutation ledger | `config`, `package_dir`, `mutation_modules`, per-check booleans | _(none)_ |
| `semgrep-reusable.yml` | A project's Semgrep rules: fixtures, scan, Sonar external-issues artifact | `config` (required), `tests`, `scan_path`, `runs_on` | _(none)_ |

## Code examination: joining a project

`code-exam-reusable.yml` runs five checks on every PR. The graders are `scripts/` in this repo
at `tools_ref`; the project's settings, rules and baselines are read from the PR merge's parent
(the base branch, `HEAD^1`), never from the PR. Each check is proven red by a mutant PR in the
first project that adopted it (tcc-qitp #1910–#1915).

| Check | Red when | Project files |
|---|---|---|
| `locked-tests` | a locked test is deleted, skipped/xfailed, loses an assert, changes a literal, or a helper, fixture or constant it reaches changes; a manifest entry is dropped or altered; a gate file is edited; a conftest adds a collection hook or skip | `tests/LOCKED_MANIFEST.json` |
| `semgrep-rules` | a rule file stops matching its `# ruleid:` fixture, or the scan finds a blocking issue (`# nosemgrep` and `.semgrepignore` are ignored) | `.semgrep/<name>.yml` + `.semgrep/tests/<name>.py` |
| `rule-catalog` | a rule, row or function of the listed modules has no entry; an entry loses a locked test or mutant; the `pending` backlog grows | `tests/RULE_CATALOG.yaml` |
| `coverage-ratchet` | a module's line or branch coverage falls under the baseline, a baseline value differs from the run's measurement, or a coverage hook appears | `tests/COVERAGE_BASELINE.json` |
| `mutation-ledger` | a mutant of a changed function survives without an `equivalent` ledger entry backed by an adversarial-second-reader verdict (line 1 `reader: adversarial-second-reader (launched by X)`, line 2 `claims-by: Y`, a CONFIRMED line for that exact mutant, the file's sha256 in the entry) | `tests/MUTATION_LEDGER.json`, `tests/mutation_verdicts/` |

**Settings** go in `.github/code-exam.json`, one section per check, keys are the upper-case
settings at the top of each script (an unknown key fails the run):

```json
{
  "locked_tests": {"FROZEN_GLOBS": [".github/workflows/code-exam.yml", ".semgrep/**/*"], "MIN_LOCKED": 50},
  "rule_catalog": {"TRUST": "docs/rules.md", "SRC": "src/mypkg", "MODULES": ["pricing", "fees"], "MIN_RULES": 10, "MIN_FUNCTIONS": 20},
  "coverage_ratchet": {"PACKAGE": "src/mypkg", "MODULES": ["pricing", "fees"]},
  "mutation_ledger": {"PACKAGE": "mypkg", "SRC": "src/mypkg"}
}
```

**Caller** (`.github/workflows/code-exam.yml`, plain `pull_request`):

```yaml
on:
  pull_request:
    branches: [dev]
jobs:
  exam:
    uses: The-Cloud-Clockwork/.github/.github/workflows/code-exam-reusable.yml@main
    with:
      package_dir: src
      dist_name: mypkg
      import_name: mypkg
      mutation_modules: "pricing fees"
      rule_catalog: true
      coverage_ratchet: true
      mutation_ledger: true
```

**Seeding.** Lock tests with `python3 check_locked_tests.py --add tests/test_x.py` (fetch the
script with `gh api repos/The-Cloud-Clockwork/.github/contents/scripts/check_locked_tests.py -H
'Accept: application/vnd.github.raw'`); seed the catalog with `gate_rule_catalog.py --seed`; the
coverage baseline's first values come alone in a PR that changes only the baseline, equal to that
run's measurement; the ledger starts empty.

**Making it binding.** A PR can edit its own caller workflow, so a required check by name is only
as strong as the caller. Make it binding with an organization ruleset that *requires the
workflow* (pinned to this repo's ref), or keep the caller on the default branch and use
`pull_request_target` there: GitHub runs `pull_request_target` workflows only from the
repository's default branch (since 2025-12-08), so a gate on a non-default integration branch
never starts.

**Semgrep → SonarQube.** `semgrep-reusable.yml` produces the `semgrep-sonar` artifact;
`sonar-reusable.yml` imports it with `external_issues_artifact: semgrep-sonar`.

**Property tests** need no shared workflow: add `hypothesis` to the project's dev dependencies.

## Workflow Templates

| Template | Description |
|----------|-------------|
| `workflow-templates/claude-code.yml` | Drop-in Claude Code Action caller |

## Usage Examples

### Release

```yaml
# .github/workflows/release.yml
name: Release
on:
  workflow_dispatch:
    inputs:
      bump:
        description: "Version bump type"
        required: true
        type: choice
        options: [patch, minor, major]

permissions:
  contents: write

jobs:
  release:
    uses: The-Cloud-Clock-Work/.github/.github/workflows/release-reusable.yml@main
    with:
      bump: ${{ inputs.bump }}
    secrets:
      # Org standardized on ORG_GITHUB_TOKEN. There is NO org secret named
      # RELEASE_TOKEN — referencing it resolves to empty and silently falls
      # back to GITHUB_TOKEN.
      RELEASE_TOKEN: ${{ secrets.ORG_GITHUB_TOKEN }}
```

### GHCR Build

```yaml
# .github/workflows/build.yml
name: Docker Build
on:
  push:
    tags: ["v*"]

permissions:
  contents: read
  packages: write

jobs:
  build:
    uses: The-Cloud-Clock-Work/.github/.github/workflows/ghcr-build-reusable.yml@main
```

### DockerHub Publish

```yaml
# .github/workflows/docker-publish.yml
name: Publish Docker image
on:
  push:
    tags: ["v*"]

jobs:
  push:
    uses: The-Cloud-Clock-Work/.github/.github/workflows/docker-publish-reusable.yml@main
    with:
      image_name: my-project
    secrets:
      DOCKERHUB_USERNAME: ${{ secrets.DOCKERHUB_USERNAME }}
      DOCKERHUB_TOKEN: ${{ secrets.DOCKERHUB_TOKEN }}
```

### PyPI Publish

> **Note:** PyPI OIDC trusted publishing does NOT support reusable workflows.
> The publish steps must be inlined in the caller. `pypi-publish-reusable.yml` exists
> for non-OIDC use cases only.

```yaml
# .github/workflows/publish-pypi.yml
name: Publish to PyPI
on:
  push:
    tags: ["v*"]
  workflow_dispatch:

permissions:
  contents: read
  id-token: write

jobs:
  gate:
    if: github.event_name == 'workflow_dispatch'
    runs-on: ubuntu-latest
    environment: release
    steps:
      - run: echo "Manual dispatch approved"

  publish:
    needs: gate
    if: always() && (needs.gate.result == 'success' || needs.gate.result == 'skipped')
    runs-on: ubuntu-latest
    environment: pypi
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install build
        run: pip install build
      - name: Build wheel + sdist
        run: python -m build
      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@release/v1
```

### Docs Audit

```yaml
# .github/workflows/docs-audit.yml
name: Docs Audit
on:
  workflow_dispatch:
    inputs:
      scope:
        description: "Audit scope"
        required: false
        type: string

permissions:
  contents: write
  pull-requests: write

jobs:
  docs-audit:
    uses: The-Cloud-Clock-Work/.github/.github/workflows/docs-audit-reusable.yml@main
    with:
      scope: ${{ inputs.scope }}
      anthropic_url_base: ${{ vars.ANTHROPIC_URL_BASE }}
      anthropic_url_fallback: ${{ vars.ANTHROPIC_URL_FALLBACK }}
      anthropic_model_base: ${{ vars.ANTHROPIC_MODEL_BASE }}
      anthropic_model_fallback: ${{ vars.ANTHROPIC_MODEL_FALLBACK }}
      mcp_server_url: ${{ vars.MCP_SERVER_URL }}
    secrets:
      RELEASE_TOKEN: ${{ secrets.ORG_GITHUB_TOKEN }}
      ANTHROPIC_TOKEN_BASE: ${{ secrets.ANTHROPIC_TOKEN_BASE }}
      ANTHROPIC_TOKEN_FALLBACK: ${{ secrets.ANTHROPIC_TOKEN_FALLBACK }}
      MCP_API_KEY: ${{ secrets.MCP_API_KEY }}
```
