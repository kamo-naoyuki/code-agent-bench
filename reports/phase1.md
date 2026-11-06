# Phase 1: initial development tasks and quality gates

Date: 2026-11-05. Status: **Q1-Q5 verified locally; Q6 pending.**
Phase 1 is not formally complete. Phase 0's outstanding operational approvals
and direct-API verification have not been silently waived.

Latest update: suite `dev-v1` is now **1.1.0**, following the independent AI
review below. The original 120-trial report describes the pre-review versions;
changed tasks were revalidated separately, not silently treated as unchanged.

## Delivered

- [Ten-task suite registry](../suites/dev-v1.json), nine new tasks plus the existing
  smoke task, with bugfix, feature and refactor categories.
- [Task authoring and review rules](../tasks/README.md).
- Digest-pinned shared agent/verifier build templates, materialized into each
  task's separate build context.
- [Shared grading helper](../bench/taskkit/grade.py): allowed-file quarantine,
  low-privilege execution, protected verifier logs, explicit plugin loading.
- [History-free source exporter](../bench/taskkit/export_source.sh), tested to
  retain the selected commit's files without altering the source repository.
- [Quality command](../bench/validate-tasks) and
  [GitHub Actions configuration](../.github/workflows/task-quality.yml).
  This workspace is not a Git repository; the workflow is configured but has
  not run on GitHub. Its core validation command was executed locally.

## Final validation

```bash
bash bench/validate-tasks --oracle-runs 10 --concurrency 6
```

Final evidence: [machine-readable report](phase1-validation-20261105-211202.json).
Harbor 0.25.0, Python 3.12, Docker on the existing host.
Automatic retries disabled. Every trial runs in fresh task/verifier containers.

| Gate | Result | Evidence |
|---|---|---|
| Q1 static lint | Pass, all 10 tasks | Harbor lint including root-agent and unpinned-image; generated contexts match templates |
| Q2 provided solution | Pass, all 10 tasks | First of the ten baseline oracle runs per task |
| Q3 no changes | Pass, all 10 tasks | Reward 0 and actual failed tests; no infrastructure exceptions |
| Q4 alternative solution | Pass, 10/10 | `SOLUTION=2`, transferred artifacts checked to contain the alternative implementation |
| Q5 repeatability | Pass, 100/100 | Each task's baseline solution passed 10 separate trials |
| Q6 specification/test review | **Pending** | Independent human review and approved `harbor check` still needed |

| Task | Category | nop reward | Alternative reward | Baseline oracle |
|---|---|---|---|---|
| smoke-fix-calc | bugfix, rename/delete | 0 | 1 | 10/10 |
| fix-interval-merge | bugfix | 0 | 1 | 10/10 |
| fix-csv-fields | bugfix | 0 | 1 | 10/10 |
| feature-toposort | feature | 0 | 1 | 10/10 |
| feature-ttl-cache | feature | 0 | 1 | 10/10 |
| fix-pagination | bugfix | 0 | 1 | 10/10 |
| refactor-stable-groups | refactor | 0 | 1 | 10/10 |
| feature-json-merge | feature | 0 | 1 | 10/10 |
| fix-retry-policy | bugfix | 0 | 1 | 10/10 |
| feature-range-parser | feature | 0 | 1 | 10/10 |

Results and frozen task inputs:

- [Cache-free task snapshots](../jobs/p1-20261105-211202-inputs/)
- [No-change trials](../jobs/p1-20261105-211202-nop/): about 108 seconds
- [Alternative-solution trials](../jobs/p1-20261105-211202-alternative/): about 114 seconds
- [Baseline oracle trials](../jobs/p1-20261105-211202-oracle/): about 1,036 seconds

The final run took approximately 21 minutes, excluding development/preflight.
All 120 results have expected rewards, no exceptions, successful `/app`
artifact collection, and nonempty test reports without skipped tests.
Each task has one identical checksum across all three validation stages.
The smoke task verifies deletion of the obsolete module, renaming of the helper,
and preserved public behavior after transfer to the separate verifier.

## Tooling checks and fixes found during development

- Eight unit tests passed: quarantine allowlist, deletions, symlink/path escape,
  special/oversized files, git snapshot export, incomplete/failed validation
  results, checksum drift, and bytecode-independent frozen input snapshots.
- CPython syntax validation passed for 55 Python files without writing bytecode;
  shell scripts passed `bash -n`.
- Pylance syntax checks timed out. No editor-level type-check result is claimed;
  runtime unit tests and actual container trials are the validation evidence.
- The first grader preflight found the installed reporting plugin's entry point
  is `ctrf`, not `pytest_json_ctrf.plugin`. Explicit plugin loading was corrected.
- The first complete run had correct rewards but task hash drift caused by
  bytecode cleanup. Its report is marked superseded and is **not** Q5 evidence.
  Harbor's `.harborignore` does not provide whole-task cache exclusion.
  Validation now freezes cache-free copies and rejects within/between-stage drift.
- A privilege guard correctly rejected Harbor's initially writable verifier log
  directories. The helper now restricts the log parent/directory before executing
  submitted code, then checks the unprivileged user cannot write verifier logs.
  Ordinary I/O failures are not swallowed as rejected submissions.
- The original smoke task's grading implementation changed, so its task version
  is now `1.1.0`. Historical Phase 0 job records are unchanged.

Only oracle and nop were used: **no additional model API or agent CLI calls**,
and no Copilot premium requests from these validation runs. Image builds and
package downloads used network and local compute resources.
All matching task containers had exited after final validation; results and
snapshots are retained. The source worktree is not used as a mutable test input.

## Remaining work before sign-off

1. Follow the [Q6 checklist](../tasks/README.md#q6-review-before-phase-1-sign-off)
   and record an independent review for each of the ten tasks.
2. Obtain approval/budget before `harbor check`, which sends task material to a
   model provider. It was not run as part of this no-model-call implementation.
3. Publish to a repository and verify the configured GitHub Actions workflow
   on the intended runner before claiming CI execution.

These are small, Python-only development tasks. Difficulty is an initial author
estimate, not calibrated; they do not establish representative product rankings.
Adversarial grading resistance (Q7), production task expansion and model-based
difficulty calibration remain later work.

## Independent AI review and corrections (2026-11-05)

A separate general-purpose AI reviewer inspected all ten task contracts and
tests, with no target-agent/model benchmark invocation. This is independent AI
review, **not human approval and not `harbor check`**.

| Task | Finding | Correction / disposition |
|---|---|---|
| smoke-fix-calc | Valid window domain was implicit; empty input/window 1 lacked coverage | Explicit positive-integer precondition, empty/window-1 tests; version 1.2.0 |
| fix-interval-merge | No specification/test mismatch identified | No changes |
| fix-csv-fields | Quoted whitespace preservation lacked direct coverage | Added quoted-whitespace case; version 1.1.0 |
| feature-toposort | No specification/test mismatch identified | No changes |
| feature-ttl-cache | Test required eager expired-get eviction although specification allowed lazy eviction | Removed the unstated eviction assertion; tested stored expired length before purge; version 1.1.0 |
| fix-pagination | Noninteger input behavior was ambiguous and oracle implementations differed | Specified ValueError for nonintegers/booleans, aligned both solutions, added invalid-type cases; version 1.1.0 |
| refactor-stable-groups | No specification/test mismatch identified | No changes |
| feature-json-merge | Mutable non-dictionary replacement lacked aliasing coverage | Added replacement deep-copy test; version 1.1.0 |
| fix-retry-policy | No specification/test mismatch identified | No changes |
| feature-range-parser | No specification/test mismatch identified | No changes |

The original smoke alternatives are two different patch formulas, not two
independently designed full implementations; their limited diversity is not
presented as proof of general grader robustness.

After regeneration, the five changed tasks passed:

- Static lint and generated-context consistency.
- Nop: 5/5 reward 0, with actual failed tests.
- Alternative oracle: 5/5 reward 1.
- Baseline oracle: 50/50 reward 1 (10 fresh trials per changed task).
- No trial errors, artifact failures or within/between-stage checksum drift.

Evidence: [review-fix validation](phase1-validation-20261105-220045.json)
and [frozen revised inputs](../jobs/p1-20261105-220045-inputs/).
The five unchanged tasks were byte-for-byte compared with the earlier validated
snapshots; their earlier 10-repeat evidence still applies. Thus all current
ten tasks have passing Q1-Q5 evidence, split across the original and revised runs.
The suite version changed, but this is not a new 120-trial whole-suite run.
All matching task containers had exited after the revised validation.

Q6 still needs the required human decision and approved rubric check.
