# Phase 2 execution pipeline

Date: 2026-11-05. The local oracle/nop execution and interruption/resume path
is implemented and verified. GitHub-hosted CI, paid Copilot E2E and direct-API
execution through this new wrapper are not claimed as verified.

## Delivered

- [One-command runner](../bench/run) and [implementation](../bench/pipeline.py).
- Versioned [oracle](../configs/dev-oracle.json),
  [nop](../configs/dev-nop.json) and [Copilot](../configs/dev-copilot.json) configs.
- Cache-free frozen task inputs, resolved Harbor config, atomic manifest and
  result summary, saved together under `jobs/<name>/`.
- Explicit resume with input/config/code/version checks, per-run locking,
  preservation of completed results, and archiving of interrupted attempts.
- [Usage instructions](../bench/PIPELINE.md) and README examples.
- CI configuration extended to unit-test and exercise the pipeline with oracle/nop.

## Local evidence

Eight pipeline unit tests passed, covering strict configs/paid confirmation,
input hashes/symlinks, locking, atomic writes/tamper detection, preserved
interrupted attempts, valid zero rewards vs infrastructure errors, idempotent
completed resume, and durable interrupted state.
The existing eight taskkit regression tests also passed.
Python compiler and shell syntax checks passed; workflow YAML structure was
validated locally. No new editor-level type-check result is claimed.

The [Docker integration check](../bench/tests/e2e_pipeline.py) deliberately:

1. Starts three oracle tasks sequentially.
2. Waits for a successful result, then sends SIGTERM to the wrapper.
3. Checks that state `interrupted` was persisted.
4. Resumes the same frozen inputs and checks all three results are successful.
5. Verifies the first completed result's bytes were not changed.
6. Resumes the completed job again and checks that no trials are re-executed.
7. Executes nop and verifies reward zero is a valid completed run.

Final integration outputs:

- [Oracle manifest](../jobs/p2-resume-20f1acf4/manifest.json):
  state `completed`, 3/3 successful, no issues.
- [Oracle summary](../jobs/p2-resume-20f1acf4/summary.json).
- [Nop manifest](../jobs/p2-resume-20f1acf4-nop/manifest.json):
  state `completed`, 1 trial, zero successful solves, no pipeline issues.
- The interrupted `fix-pagination` attempt is preserved beneath the oracle
  run's `interrupted/` directory, not silently deleted.

In this test Harbor did not exit within the wrapper's 45-second interruption
grace period, so its process group was killed (return code -9); the wrapper
persisted interruption and recovery still passed. This is evidence for restart
from saved inputs/results, not a claim that every cancellation is graceful.
Cleanup of Docker resources after an uncatchable host/process death is not
guaranteed and must be checked before manual recovery.

## Reproducibility and boundaries

- Manifest records suite/task versions, deterministic content hashes, pinned
  base image references, requested model, Harbor/CLI/OS/kernel/Python/Docker
  observations, runner/adapter hashes, execution history and timestamps.
- The workspace has no git commit; git unavailability is recorded explicitly,
  rather than inventing a revision. Runtime file hashes provide local identity.
- Native Harbor trial records are retained; adapter-reported agent version and
  usage are included in summary. Unknown snapshot/price/budget fields remain
  unknown. No immutable provider snapshot or exact billing ceiling is promised.
- Source/config/runtime version changes are rejected on resume. The implementation
  currently records absolute paths and is not a portable run migration format.
- Non-cancellation errors are not automatically retried. They remain visible and
  produce a nonzero wrapper exit. Reward zero alone does not.
- Paid runs require explicit confirmation on both start and resume. The Copilot
  config was inspected without calling a model; this new wrapper's paid E2E is
  unverified. The previously tested adapter itself is unchanged.
- Direct API use still enters through the prior Phase 0 runner, not this wrapper.
- No evaluation-model API/CLI call was made for these pipeline tests.
  An independent AI subagent was used for specification/test review; that review
  is separate from target-agent benchmarking.

## Remaining gates

The independent AI task review and its fixes are recorded in
[Phase 1 results](phase1.md). Human sign-off and approved `harbor check` remain
outstanding; Phase 1 is not silently declared complete. Likewise the configured
GitHub workflow has not yet been executed on GitHub.
Phase 0 operational approvals and direct-API provider verification remain as
previously documented.
