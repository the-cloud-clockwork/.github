# Automation

One setup run gives a repository the organization's CI standard. This page says what the setup changes and why our CI works this way.

## Run it

On GitHub: open **Actions → Setup repository → Run workflow**, type the repository name and leave **dry run** ticked the first time. The run prints every setting and file it would change and writes nothing. Run it again with dry run unticked to apply.

On your own machine, with a `gh` login that can read the repository:

```bash
python3 automation/setup_repo.py --repo agentihooks --dry-run
```

Drop `--dry-run` to apply. The workflow signs in as the organization's agenticore GitHub App (org secrets `AGENTICORE_APP_ID` and `AGENTICORE_APP_PRIVATE_KEY`), because writing a ruleset needs the App's repository **Administration: write** permission and that App holds it. The App also needs write on contents, pull requests and workflows to open the pull request. A dry run asks only for read.

## What it changes

1. **The merge queue settings in the repository's `dev` ruleset.**

   | Setting | Value | GitHub's name |
   |---|---|---|
   | Test one group at a time | 1 | Build concurrency (`max_entries_to_build`) |
   | Smallest group | 1 | Minimum group size (`min_entries_to_merge`) |
   | Largest group | 5 | Maximum group size (`max_entries_to_merge`) |
   | Wait for a group to fill | 1 minute | `min_entries_to_merge_wait_minutes` |
   | Merge only pull requests whose checks pass | on | Only merge non-failing pull requests (`ALLGREEN`) |

   The check timeout and merge method already on the ruleset stay as they are. A repository with no `dev` ruleset is refused: protect `dev` first. When the repository keeps its ruleset as a file under `.github/rulesets/`, the setup changes the same numbers in that file too, so the ruleset sync that antoncore runs on every push to its `dev` does not put the old numbers back.

2. **A pull request into `dev`** that adds `.github/workflows/shared-tests.yml`, a short workflow that calls the shared test workflow in this repository (`python-tests-reusable.yml`). The copy it adds is [`shared-tests.yml`](shared-tests.yml) in this folder.

## Why our CI works this way

### Test several pull requests together

The merge queue tests each pull request on top of everything ahead of it before it reaches `dev`. When it tests them one at a time, every pull request costs a full test run. When it tests a small group together and the group passes, the whole group merges on one run.

Aviator's documentation works the numbers: a queue of 10 pull requests needs 10 extra test runs when groups hold 1, 4 when they hold 3, and 2 when they hold 5 ([Aviator, batching](https://docs.aviator.co/mergequeue/concepts/batching)). GitHub itself ships groups of 30 or more changes at a time ([How GitHub uses merge queue](https://github.blog/engineering/engineering-principles/how-github-uses-merge-queue-to-ship-hundreds-of-changes-every-day/)). We pick 5: big enough to save most runs, small enough that a failing group is quick to sort out.

The one minute wait lets a second and third pull request join a group instead of each starting its own run a few seconds apart.

### Test one group at a time

GitHub can test several groups at once, each one guessing that the groups ahead of it will pass. Every wrong guess throws away a full test run, and those runs fight the pull request checks for the same runners.

Uber measured this on its own queue: limiting how many of these guessed builds run cut the builds per change from 3.39 to 1.85, cut weekly CPU hours by 35 to 52 percent, and cut the slowest waits (95th percentile) by 32 to 45 percent ([Uber, arXiv 2501.03440](https://arxiv.org/html/2501.03440)). Mergify's page on queue speed covers the same trade off ([Mergify, performance](https://docs.mergify.com/merge-queue/performance)).

What we saw ourselves: at 14:47 UTC on 20 October, three agentihooks merge groups held 87 of 118 unfinished CI jobs against about 40 runners on a seven node cost cap. Pull request checks and the hive image build waited up to ten minutes for a runner.

### A quick check before merge, the full check after

Google runs a fast part of its tests before a change goes in and the full set after, and undoes a change that breaks the full set ([Software Engineering at Google, chapter 23](https://abseil.io/resources/swe-book/html/ch23.html)). The shared test workflow is built the same way: a pull request runs only the tests its change touches, the merge queue runs the unit tests on one Python version, and the other Python versions and the slow checks run after the merge on `dev`. A failure there holds the release and gets fixed by the next pull request.

### Keep pull request checks from waiting

On the cluster, runners for pull request checks and image builds should carry a higher pod priority than long batch work, so Kubernetes starts them first when the cluster is full ([Kubernetes, pod priority and preemption](https://kubernetes.io/docs/concepts/scheduling-eviction/pod-priority-preemption/)).

### Where the settings come from

GitHub documents every merge queue setting above ([GitHub, managing a merge queue](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue)).

## What we chose not to do, for now

- **Paid merge queue products** (Mergify, Trunk, Aviator). GitHub's own queue does what we need once its settings are right.
- **Picking tests with a trained model.** Meta does this ([Meta, predictive test selection](https://engineering.fb.com/2018/11/21/developer-tools/predictive-test-selection/)), but the tools for it on pytest are not mature yet.
