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
| `release-reusable.yml` | Semver bump → commit → tag → push | `bump` (required), `branch`, `version_file` | `TCC_CI_CLIENT_ID`, `TCC_CI_APP_PRIVATE_KEY` (optional, org CI App; falls back to `GITHUB_TOKEN`) |
| `ghcr-build-reusable.yml` | Build + push Docker image to GHCR | `dockerfile`, `context` | _(uses `GITHUB_TOKEN`)_ |
| `docker-publish-reusable.yml` | Build + push Docker image to DockerHub | `image_name` (required), `context` | `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN` |
| `pypi-publish-reusable.yml` | Build + publish to PyPI via OIDC | `python_version` | _(OIDC trusted publishers)_ |
| `docs-audit-reusable.yml` | Claude-powered docs audit with MCP (self-contained prompt) | `scope`, `extra_instructions`, `anthropic_url_base` (required), + more | `ANTHROPIC_TOKEN_BASE`, `TCC_CI_CLIENT_ID`, `TCC_CI_APP_PRIVATE_KEY`, `MCP_API_KEY` |
| `python-tests-reusable.yml` | Staged Python tests: changed tests on pull request, one sharded version in the merge queue, the other versions and slow checks after merge | `pr_python`, `queue_python`, `push_python`, `shards`, `pr_runner`, `queue_runner`, `push_runner`, `slow_command` | _(none)_ |

## Automation

`automation/` holds the setup that gives a repository the org CI standard (merge queue settings and the shared test caller) and a README on why our CI works this way. Run it from **Actions → Setup repository**.

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
      # The org CI GitHub App mints the push token; without it the
      # workflow falls back to GITHUB_TOKEN.
      TCC_CI_CLIENT_ID: ${{ secrets.TCC_CI_CLIENT_ID }}
      TCC_CI_APP_PRIVATE_KEY: ${{ secrets.TCC_CI_APP_PRIVATE_KEY }}
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
      TCC_CI_CLIENT_ID: ${{ secrets.TCC_CI_CLIENT_ID }}
      TCC_CI_APP_PRIVATE_KEY: ${{ secrets.TCC_CI_APP_PRIVATE_KEY }}
      ANTHROPIC_TOKEN_BASE: ${{ secrets.ANTHROPIC_TOKEN_BASE }}
      ANTHROPIC_TOKEN_FALLBACK: ${{ secrets.ANTHROPIC_TOKEN_FALLBACK }}
      MCP_API_KEY: ${{ secrets.MCP_API_KEY }}
```

### Python Tests

Three stages, chosen by the event that called the workflow:

| Event | Runs | Purpose |
|-------|------|---------|
| `pull_request` | Changed test files and the test files that import a changed module, on `pr_python` | Fast feedback before the queue |
| `merge_group` | The full suite on `queue_python` (default 3.12), split into `shards` jobs | The one check that gates the merge |
| `push` to `push_branch` | The full suite on `push_python` (default 3.12) plus `slow_command` | Slow checks after merge, gating promotion only |

A changed file under `full_suite_paths` (dependency and pytest configuration, conftest, workflows), a changed non Python file under `test_paths`, a deleted file, or a changed module no test imports directly runs the whole suite on a pull request. Non Python files outside `test_paths` select no tests. A test that reaches a changed module only through another module is not selected; the merge queue's full run catches it. Make the ruleset require the aggregator check, `<caller job> / Python Tests — Required`, never the per shard checks.

```yaml
# .github/workflows/tests.yml
name: Tests
on:
  pull_request:
    branches: [dev]
  merge_group:
  push:
    branches: [dev]

permissions:
  contents: read

jobs:
  tests:
    uses: The-Cloud-Clock-Work/.github/.github/workflows/python-tests-reusable.yml@main
    with:
      queue_python: '["3.12"]'
      push_python: '["3.12"]'
      shards: 6
      queue_runner: anton
      slow_command: "python -m pytest -m slow"
```

Why the merge queue tests one version (research of 20 October 2026):

1. Batch several pull requests per merge test. A ten pull request queue needs 10, 4 or 2 extra pipelines at batch size 1, 3 or 5 ([Aviator batching](https://docs.aviator.co/mergequeue/concepts/batching)); GitHub ships groups of 30 or more ([how GitHub uses merge queue](https://github.blog/engineering/engineering-principles/how-github-uses-merge-queue-to-ship-hundreds-of-changes-every-day/), [merge queue settings](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue)).
2. Cap concurrent builds. Uber cut speculative builds per change from 3.39 to 1.85, weekly CPU hours by 35 to 52 percent and P95 wait by 32 to 45 percent ([Uber, 2025](https://arxiv.org/html/2501.03440); [Mergify performance](https://docs.mergify.com/merge-queue/performance)).
3. Run a fast subset before merge and the full suite after merge, reverting a breaking change ([Google TAP](https://abseil.io/resources/swe-book/html/ch23.html)).
4. Give pull request checks and image builds pod priority so queue shards cannot starve them ([Kubernetes pod priority](https://kubernetes.io/docs/concepts/scheduling-eviction/pod-priority-preemption/)).

Rejected for now: paid queue products (Mergify, Trunk, Aviator) and predictive test selection ([Meta](https://engineering.fb.com/2018/11/21/developer-tools/predictive-test-selection/)), whose pytest tooling is immature.

Local evidence: at 14:47 UTC on 20 October 2026 three agentihooks merge groups held 87 of 118 unfinished CI jobs against about 40 runners on a seven node cost cap, and pull request checks and an image build waited up to ten minutes for a runner.
