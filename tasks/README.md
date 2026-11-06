# Development task suite

The initial suite is [dev-v1](../suites/dev-v1.json): ten small Python tasks,
including the original rename/delete smoke task, bug fixes, feature additions,
and a non-mutating refactor. These handwritten tasks exercise the harness;
they are **not a calibrated, representative production benchmark**.
All are public development tasks, not heldout material.

## Authoring contract

- Directory: `tasks/dev/<category-or-action>-<short-name>/`.
- Schema: Harbor 1.4; task version starts at `1.0.0`. Change the version when
  instructions, source, tests, grading or environment semantics change.
- Required metadata: category, language, initial difficulty, source, split,
  unique canary. Initial `easy` difficulty is an author estimate, not measured.
- Instructions state the full observable contract, allowed source files,
  invalid-input behavior, mutation/identity requirements and preserved behavior.
- `environment/app/` contains only starter source; no answers or hidden tests.
- Dependencies and base image are pinned. The shared Python base is fixed by
  digest in [sync_templates.py](../bench/taskkit/sync_templates.py).
- Agent is non-root; model-facing tools must operate only in the sandbox.
- Artifacts are transferred as the `/app` directory, preserving deletions.
- A separate offline verifier copies only contract-listed regular source files
  to `/submission`. Symlinks, special files and files over 1 MB are rejected.
  Agent-supplied test/config files are never imported by the verifier.
- Hidden tests run as `grader`, from a trusted directory, with isolated Python
  and explicit pytest plugins. The root verifier writes reward based on test
  exit status. Harbor's initially writable log directories are restricted before
  execution, and a guard verifies that `grader` cannot write verifier logs.
  Runner errors must not be accepted as ordinary task failures.
- This is defense in depth, not a proof against hostile submitted Python.
  Phase 6's adversarial verifier tests (Q7) remain separate work.

## Shared files

Harbor builds agent and verifier images from separate directories. Shared files
are therefore materialized into each build context, not symlinked outside it.
Edit the sources in [taskkit](../bench/taskkit/), then run:

```bash
source bench/env.sh
HARBOR_PY="$(dirname "$(readlink -f "$(command -v harbor)")")/python"
"$HARBOR_PY" bench/taskkit/sync_templates.py
"$HARBOR_PY" bench/taskkit/sync_templates.py --check
```

Generated files: both Dockerfiles and Docker ignore files, verifier runner/helper/contract, and the nine
new tasks' metadata and oracle dispatch scripts. The original smoke task keeps
its own metadata and oracle implementation. Each task supports `SOLUTION=1`
and `SOLUTION=2`, using distinct implementations.

## Validate without AI or API credits

```bash
# Fast checks only
bash bench/validate-tasks --static-only

# Q1-Q5: 10 tasks x (nop + alternative + 10 oracle repeats) = 120 trials
bash bench/validate-tasks --oracle-runs 10 --concurrency 3

# Narrow development check (does NOT complete Q5)
bash bench/validate-tasks --task fix-pagination --oracle-runs 1 --concurrency 1
```

This uses only Harbor's `oracle` (provided answer) and `nop` (no changes).
No model API or CLI is called. Docker, CPU, disk, image/package downloads and
network bandwidth are still used. CI runs the same command after publishing
this workspace to a GitHub repository; local execution does not run GitHub CI.

Q1 checks Harbor lint including root-agent and unpinned-image. Q2/Q5 require all
ten baseline oracle runs to pass in fresh containers. Q3 requires nop reward 0
and actual failed tests. Q4 runs the second solution for each task. Missing
results/reports, failed artifact collection, exceptions, and wrong trial counts
all fail validation; automatic retries are disabled.
Before starting containers, the validator freezes a cache-free task snapshot
under `jobs/p1-<timestamp>-inputs/`. Every stage uses that same snapshot; task
checksums must match both within each stage and between stages. Python bytecode
in the working tree is not included in the snapshot. Do not edit saved snapshots.
The original smoke task is now version `1.2.0` after grading and contract-review
updates; Phase 0 results retain their original task metadata. The suite is
version `1.1.0`; individual task versions are recorded in the registry when they
differ from `1.0.0`.

Machine-readable reports go to `reports/phase1-validation-<timestamp>.json`;
trial logs and submitted artifacts remain in `jobs/p1-<timestamp>-*/`.

## Q6 review before Phase 1 sign-off

Automatic gates do **not** complete Q6. A reviewer must independently compare
each instruction with every hidden test, confirming that:

1. Every assertion follows an explicit requirement, not an unstated preference.
2. Examples, boundary cases and error behavior agree with the specification.
3. Baseline failures exercise the intended bug/feature, not test setup errors.
4. Both solutions satisfy the public contract; tests do not prescribe one algorithm.
5. Preserved behavior is covered, including mutation and identity when specified.
6. File deletion/rename requirements and submitted-file allowlists agree.

Record reviewer, date, findings and disposition for every task. The planned
`harbor check` rubric also requires a model and external transmission of task
content: obtain approval and a budget before running it. Neither writing tests
nor passing Q1-Q5 is a substitute for this independent review.

## Import source without leaking future git history

For a **disposable new task snapshot**, not the active source worktree:

```bash
bash bench/taskkit/export_source.sh /path/to/source/repo BASE_COMMIT /new/snapshot/path
```

The destination must not exist. This exports tracked files from the chosen
commit without `.git`, refs, remote configuration, reflogs or future objects,
and never mutates the source repository. Submodules and LFS expansion are not
implemented. Audit tracked files for secrets/answers before building an image.
