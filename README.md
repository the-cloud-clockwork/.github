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
| `locked-tests-reusable.yml` | Protected test list: a locked test deleted, skipped or weakened is red | `manifest`, `min_tests`, `runs_on` | _(none)_ |
| `semgrep-reusable.yml` | A project's Semgrep rules: fixtures, scan, Sonar external-issues artifact | `config` (required), `tests`, `scan_path`, `runs_on` | _(none)_ |

## Code examination: joining a project

Three pieces, each a short caller workflow in the project. `scripts/` in this repo holds the
checkers the reusables run (`check_locked_tests.py`, `semgrep_to_sonar.py`).

**1. Locked tests.** Create the manifest and lock tests (static, runs no test):

```bash
gh api repos/The-Cloud-Clockwork/.github/contents/scripts/check_locked_tests.py \
  -H 'Accept: application/vnd.github.raw' > "$HOME/check_locked_tests.py"
echo '{"min_tests": 0, "tests": {}}' > tests/LOCKED_MANIFEST.json
python3 "$HOME/check_locked_tests.py" --add tests/test_money.py tests/test_fills.py
python3 "$HOME/check_locked_tests.py" --update     # after a deliberate change to a locked test
```

Set `min_tests` to the count locked, and own the manifest in `.github/CODEOWNERS`
(`/tests/LOCKED_MANIFEST.json @owner`); code-owner review is enforced only when the branch's
ruleset requires it. Caller:

```yaml
# .github/workflows/ci-locked-tests.yml
on:
  pull_request:
    branches: [dev]
jobs:
  locked:
    uses: The-Cloud-Clockwork/.github/.github/workflows/locked-tests-reusable.yml@main
```

A PR goes red when a locked test is deleted, gains a skip or xfail (decorator, module
`pytestmark`, `pytest.skip()` in the body), loses an assert, or changes any literal
(expected values, parametrize rows, inputs) without the manifest changing with it.

**2. Semgrep rules → SonarQube.** Rules in `.semgrep/rules.yml`, one fixture file per rule
file with `# ruleid:` / `# ok:` lines (`semgrep --test` fails when a rule stops matching its
broken case). Caller, with the import into the project's Sonar gate:

```yaml
# .github/workflows/ci-semgrep.yml
on:
  pull_request:
    branches: [dev]
jobs:
  semgrep:
    uses: The-Cloud-Clockwork/.github/.github/workflows/semgrep-reusable.yml@main
    with:
      config: .semgrep/rules.yml
      tests: .semgrep/tests/rules.py
  sonar:
    needs: semgrep
    if: always()
    uses: The-Cloud-Clockwork/.github/.github/workflows/sonar-reusable.yml@main
    with:
      external_issues_artifact: semgrep-sonar
      scanner_args: -Dproject.settings=sonar-project.properties
    secrets: inherit
```

Semgrep skips rule files under a dot-directory when given the directory, so `tests` names the
fixture file (or a non-dot directory). The findings land in Sonar as external issues
(engine `semgrep`, RELIABILITY / HIGH), so they count against the quality gate.

**3. Property tests** need no shared workflow: add `hypothesis` to the project's dev
dependencies and write `@given` tests next to the unit tests; they run wherever the suite runs.

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
