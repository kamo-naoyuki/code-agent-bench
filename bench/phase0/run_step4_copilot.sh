#!/bin/bash
# Phase 0 / Step 4 (PLAN.md 0.3, Appendix B) WITHOUT API keys: the reference "agent" is the host's GitHub Copilot CLI,
# using the Copilot login that already exists on this host. No key is typed, copied or placed in a sandbox
# (bench/agents/copilot_host.py explains the isolation). The cost unit is Copilot PREMIUM REQUESTS, not USD:
# one trial = one prompt = (the model's multiplier) premium requests, whatever the number of model/tool calls.
#
# Differences from run_step4.sh (the API-key / mini-swe-agent route), see reports/phase0.md:
#   * the scaffold is Copilot CLI, not mini-swe-agent (PLAN.md 4.2 mode B, "product comparison"): do not mix the
#     two routes in one ranking
#   * model ids are Copilot catalog aliases (e.g. claude-haiku-4.5), not dated snapshots (PLAN.md 6.1)
#   * the budget is measured exactly: each model is probed once (costs one multiplier) to learn its multiplier
#
# Required environment:
#   MODEL_A, MODEL_B            Copilot model ids (`copilot` --model values). May be equal for a harness-only test.
#   BUDGET_PREMIUM_REQUESTS     hard cap for probes + preflight + Step 4 together; refuses to start above it
#   CONFIRM_SPEND=yes           acknowledge that Copilot premium requests will be consumed
# Optional:
#   N_TASKS (default 3)  K (default 2)  N_CONCURRENT (default 2: Copilot rate limits)
#   REASONING_EFFORT        low|medium|high|xhigh|max, passed to the CLI for both models
#   EXTRA_ARGS="..."        extra `harbor run` arguments for every job (e.g. --agent-timeout-multiplier 0.25)
#   SKIP_PREFLIGHT=yes      skip the smoke-task canary per model (not recommended)
#   DRY_RUN=1               print the plan only: no model call, nothing consumed
set -euo pipefail
cd "$(dirname "$0")/../.."
source bench/env.sh

: "${MODEL_A:?set MODEL_A}" "${MODEL_B:?set MODEL_B}" "${BUDGET_PREMIUM_REQUESTS:?set BUDGET_PREMIUM_REQUESTS (hard cap)}"
N_TASKS="${N_TASKS:-3}"
K="${K:-2}"
N_CONCURRENT="${N_CONCURRENT:-2}"
N_TRIALS=$((2 * N_TASKS * K))
if [ "$N_TRIALS" -gt 12 ]; then
  echo "refusing: $N_TRIALS agent trials > 12 (PLAN.md 0.3 caps Step 4 + G3 at 12)" >&2
  exit 2
fi
AGENT="bench.agents.copilot_host:CopilotHostAgent"
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
HARBOR_PY="$(dirname "$(readlink -f "$(command -v harbor)")")/python"
STAMP="$(date +%Y%m%d-%H%M)"
read -r -a EXTRA <<< "${EXTRA_ARGS:-}"
AK=()
[ -n "${REASONING_EFFORT:-}" ] && AK+=(--ak "reasoning_effort=$REASONING_EFFORT")

cat <<EOF
Step 4 plan (Copilot route, no API keys)
  agent         : $AGENT   (Copilot CLI $(copilot --version 2>/dev/null | head -1 | sed 's/\.$//'), host login, sandbox reached only through one bridged bash tool)
  models        : $MODEL_A  |  $MODEL_B
  tasks         : terminal-bench@2.0, first $N_TASKS (identical for both models), k=$K  -> $N_TRIALS agent trials, concurrency $N_CONCURRENT
  budget        : $BUDGET_PREMIUM_REQUESTS premium requests in total (probes + preflight + trials)
  telemetry     : HARBOR_TELEMETRY=$HARBOR_TELEMETRY
EOF
if [ -n "${DRY_RUN:-}" ]; then
  echo "DRY_RUN: no model was called, nothing was consumed. Needed to run for real: CONFIRM_SPEND=yes"
  exit 0
fi
[ "${CONFIRM_SPEND:-}" = "yes" ] || { echo "refusing: set CONFIRM_SPEND=yes to consume up to $BUDGET_PREMIUM_REQUESTS premium requests" >&2; exit 4; }

echo "== probing the models (one minimal prompt each; this also measures the multiplier)"
# PROBE_CMD is a test hook (a fake probe lets the budget guard be exercised without consuming quota).
PROBE_CMD="${PROBE_CMD:-$HARBOR_PY -m bench.agents.copilot_host probe}"
multiplier() {  # prints the model's premium-request multiplier; non-zero exit if the model is unusable
  local json
  json=$($PROBE_CMD "$1") || { echo "probe failed for $1: $json" >&2; return 1; }
  "$HARBOR_PY" -c 'import json,sys; print(json.loads(sys.argv[1])["premium_requests"])' "$json"
}
PROBES=2
MULT_A=$(multiplier "$MODEL_A") || exit 3
if [ "$MODEL_B" = "$MODEL_A" ]; then MULT_B="$MULT_A"; PROBES=1; else MULT_B=$(multiplier "$MODEL_B") || exit 3; fi
PREFLIGHT_TRIALS=0; [ "${SKIP_PREFLIGHT:-}" != "yes" ] && PREFLIGHT_TRIALS=1
read -r SPENT PLANNED TOTAL <<< "$("$HARBOR_PY" - "$MULT_A" "$MULT_B" "$N_TASKS" "$K" "$PREFLIGHT_TRIALS" "$PROBES" <<'PY'
import sys
a, b, tasks, k, pre, probes = float(sys.argv[1]), float(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6])
spent = a if probes == 1 else a + b    # the probes just made
planned = (tasks * k + pre) * (a + b)  # per model: preflight trial(s) + Step 4 trials
print(f"{spent:.2f} {planned:.2f} {spent + planned:.2f}")
PY
)"
echo "  multiplier A=$MULT_A  B=$MULT_B | spent so far (probes)=$SPENT | planned=$PLANNED | total=$TOTAL | budget=$BUDGET_PREMIUM_REQUESTS"
if awk -v t="$TOTAL" -v b="$BUDGET_PREMIUM_REQUESTS" 'BEGIN { exit !(t > b) }'; then
  echo "refusing: planned total $TOTAL premium requests exceeds BUDGET_PREMIUM_REQUESTS=$BUDGET_PREMIUM_REQUESTS (probes already consumed $SPENT)" >&2
  exit 6
fi

run_job() {  # name model dataset-args...
  local name="$1" model="$2"; shift 2
  harbor run "$@" -a "$AGENT" -m "$model" -n "$N_CONCURRENT" --job-name "$name" ${AK[@]+"${AK[@]}"} ${EXTRA[@]+"${EXTRA[@]}"}
}

if [ "${SKIP_PREFLIGHT:-}" != "yes" ]; then
  echo "== pre-flight: smoke task x 1 per model (harness check, not an evaluation trial)"
  i=0
  for M in "$MODEL_A" "$MODEL_B"; do
    i=$((i + 1))
    run_job "p0c-preflight-$STAMP-$i" "$M" -p tasks/dev/smoke-fix-calc -k 1
    python3 bench/phase0/summarize_job.py "jobs/p0c-preflight-$STAMP-$i" \
      || { echo "pre-flight failed for $M: fix the harness before spending the Step 4 budget" >&2; exit 5; }
  done
fi

echo "== Step 4"
run_job "p0c-step4-$STAMP-a" "$MODEL_A" --dataset terminal-bench@2.0 -l "$N_TASKS" -k "$K"
run_job "p0c-step4-$STAMP-b" "$MODEL_B" --dataset terminal-bench@2.0 -l "$N_TASKS" -k "$K"

mkdir -p reports
python3 bench/phase0/summarize_job.py "jobs/p0c-step4-$STAMP-a" "jobs/p0c-step4-$STAMP-b" | tee "reports/phase0-step4-copilot-$STAMP.txt" || true
echo "Next: harbor view jobs  (Phase 0 Step 5 / G7; the viewer API is unauthenticated, see reports/phase0.md F4)"
