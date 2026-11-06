# One-command runs and resume

The Phase 2 wrapper prepares a frozen task set, records a manifest, runs Harbor,
and summarizes saved results. It does not replace Harbor's trial engine.

## Run without calling AI

From the workspace root:

```bash
# Inspect the plan: no containers or model calls, no result directory created.
bash bench/run --config configs/dev-oracle.json --dry-run

# Give the provided answer to the small smoke task and grade it.
bash bench/run --config configs/dev-oracle.json \
  --task smoke-fix-calc --job-name example-oracle

# Do nothing, then grade. Reward zero is expected; it is not a pipeline error.
bash bench/run --config configs/dev-nop.json \
  --task smoke-fix-calc --job-name example-nop
```

Omit `--task` to run all registered suite tasks; repeat it to select several.
`--job-name` must be a new name. If omitted, a timestamp/random name is generated.
The [versioned configs](../configs/) contain suite, agent, model if needed,
attempt count and concurrency. The schema rejects unknown fields, credentials
and arbitrary Harbor command options. Retry count is fixed at zero.
This initial wrapper supports oracle, nop and the tested host Copilot adapter.
The older direct-API entry remains [run_step4.sh](phase0/run_step4.sh);
Claude/Codex are not implicitly claimed as supported by this wrapper.

## Paid Copilot execution

```bash
bash bench/run --config configs/dev-copilot.json \
  --task smoke-fix-calc --dry-run

# Only after reviewing the plan and approving account usage:
bash bench/run --config configs/dev-copilot.json \
  --task smoke-fix-calc --job-name example-copilot --confirm-spend
```

The example model is `claude-haiku-4.5`; use an available model in a new config
when necessary. CLI login stays on the host. No API key belongs in job configs.
There is **no hard spending cap** in this wrapper. Paid execution requires
`--confirm-spend` on both initial run and resume and is limited to 12 task trials.
One trial may contain many model/tool calls. There are no paid model probes or
smoke preflights automatically added by this wrapper.
Recorded usage is whatever the adapter reports, not a verified billing statement.
Mini-swe-agent/API budget handling is intentionally left to its existing runner.

## Interrupt and resume

```bash
# Ctrl+C during a run asks Harbor to cancel and clean up.
# Later, use the same result directory name, not a new config:
bash bench/run --resume example-oracle

# Paid run:
bash bench/run --resume example-copilot --confirm-spend
```

The runner records interruption, gives Harbor time to clean up, and saves logs.
After a 45-second grace period it kills the child process group if still running.
Check for leftover task containers after a forced kill or host failure.
SIGTERM is also handled; uncatchable host/process death may leave state `running`.
The persisted inputs still support an explicit resume after the old process has
stopped. A file lock rejects concurrent run/resume processes for the same job.

On resume:

- Completed trials, including legitimate reward zero, are retained.
- Incomplete or `CancelledError` trials move to `interrupted/<id>/` before Harbor
  resumes them. Logs are retained; failed attempts are not silently erased.
- Other exception results remain recorded, are not automatically retried, and
  make the final wrapper result nonzero. Diagnose them before starting a new run.
- A finished healthy run is a no-op: no model calls or trial re-execution.
- Frozen tasks/configs, runner/adapter source hashes and Harbor/CLI versions are
  checked. Changed inputs/versions are rejected rather than mixed into one run.
  Changes to the original task worktree do not change the frozen inputs.
- If an interrupted paid trial is restarted, its earlier use is not refunded.
  Archived attempts retain their logs; no exact total-billing claim is made.

Do not manually edit saved configs, frozen tasks or result files.
Do not move result directories between machines and expect resume to work:
the effective config currently contains absolute paths.

## Output

```text
jobs/<job-name>/
  manifest.json       # versions, hashes, host, attempts, state and execution history
  request.json        # allowlisted wrapper config
  suite.json          # suite version and membership
  effective.json      # resolved Harbor config (absolute frozen paths)
  inputs/             # cache-free task copies
  runner.log          # Harbor stdout/stderr
  summary.json        # trial rewards, errors, agent versions and reported usage
  harbor/             # original Harbor job and trial output, not rewritten
  interrupted/        # preserved incomplete/cancelled attempts, when present
```

Use `tail -f jobs/<job-name>/runner.log` for live Harbor output.
The wrapper returns 0 only for a complete set of structurally valid results,
not only when agents solve every task. Reward zero is a valid task result.
Errors, missing results, mismatched checksums or artifact failures return nonzero.
An interrupted run returns 130.

The manifest records task versions/digests, base-image references, requested
model, observed agent information, Harbor/CLI versions, Python/OS/kernel/Docker,
git commit if available, run timestamps and source hashes.
Unavailable observations are explicitly recorded with errors. No immutable
model snapshot, USD price table, gateway or hard budget is invented when absent.
Unknown usage remains unknown; `summary.json` retains adapter-reported values.

These manifests contain local paths and host information; Harbor logs and
artifacts can contain task code. Keep `jobs/` private and outside version control.
No upload, hosted service or viewer is started automatically.

## Checks

```bash
source bench/env.sh
HARBOR_PY="$(dirname "$(readlink -f "$(command -v harbor)")")/python"
"$HARBOR_PY" -m unittest discover -s bench/tests -v

# Explicit local Docker integration, no AI:
"$HARBOR_PY" bench/tests/e2e_pipeline.py
```

The integration deliberately interrupts a sequential three-task oracle run
after a completed trial, resumes it, verifies that completed result bytes did
not change, then checks idempotent resume and nop's expected zero reward.
GitHub CI is configured to run these checks, but local success is not a claim
that a GitHub-hosted run has passed.
