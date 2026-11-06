#!/bin/bash
# Phase 0 / Step 4 (PLAN.md 0.3, Appendix B): reference agent mini-swe-agent, 2 models, the same
# public terminal-bench@2.0 tasks, k=2. THIS SPENDS REAL MONEY, so it refuses to start without an
# explicit budget and confirmation. It encodes every pitfall found while rehearsing with the mock
# LLM (see reports/phase0.md):
#   * Harbor's mini-swe-agent defaults are cost_limit=0 / step_limit=0 (= unlimited) -> always set both
#   * litellm>=1.106 breaks mini-swe-agent (needs `mcp`) -> pin the agent install by date (UV_EXCLUDE_NEWER)
#   * the proxy injected into containers by ~/.docker/config.json may intercept TLS to LLM APIs ->
#     override the HTTPS proxy only, with the proxy the host shell uses (BENCH_CONTAINER_PROXY);
#     overriding HTTP too breaks apt (plain-HTTP mirrors time out on that proxy)
#   * a broken agent install is recorded as a plain AgentTimeoutError -> run a cheap pre-flight first
#
# Required environment:
#   MODEL_A, MODEL_B     litellm model ids with provider prefix and snapshot id, e.g. anthropic/claude-...-20250929
#   BUDGET_USD           hard budget for the Step 4 trials; the per-trial cost_limit is BUDGET_USD / n_trials
#   CONFIRM_SPEND=yes    acknowledge that real API calls will be made
#   provider API keys    already exported in the environment (never written to disk by this script)
# Optional:
#   N_TASKS (default 3)  K (default 2)  N_CONCURRENT (default 3)
#   BENCH_CONTAINER_PROXY   HTTPS proxy for agent containers (default: the host's HTTPS_PROXY)
#   BENCH_UV_EXCLUDE_NEWER  date pin for the agent install; "none" disables (default 2026-10-15T00:00:00Z)
#   ALLOW_UNPRICED=yes      continue even if litellm has no price for a model (cost_limit then does nothing)
#   EXTRA_ARGS="..."        extra `harbor run` arguments for every job (e.g. --base-url / --api-format for a Gateway)
#   SKIP_PREFLIGHT=yes      skip the hello-world canary (not recommended)
#   DRY_RUN=1               print the plan and the commands, run nothing
set -euo pipefail
cd "$(dirname "$0")/../.."
source bench/env.sh

: "${MODEL_A:?set MODEL_A}" "${MODEL_B:?set MODEL_B}" "${BUDGET_USD:?set BUDGET_USD (hard cap in USD)}"
N_TASKS="${N_TASKS:-3}"
K="${K:-2}"
N_CONCURRENT="${N_CONCURRENT:-3}"
N_TRIALS=$((2 * N_TASKS * K))
if [ "$N_TRIALS" -gt 12 ]; then
  echo "refusing: $N_TRIALS agent trials > 12 (PLAN.md 0.3 caps Step 4 + G3 at 12)" >&2
  exit 2
fi
PER_TRIAL_USD=$(python3 -c "print(f'{float(\"$BUDGET_USD\") / $N_TRIALS:.4f}')")
PIN="${BENCH_UV_EXCLUDE_NEWER:-2026-10-15T00:00:00Z}"
CONTAINER_PROXY="${BENCH_CONTAINER_PROXY:-${HTTPS_PROXY:-}}"
STAMP="$(date +%Y%m%d-%H%M)"

AE=(--ae LITELLM_LOCAL_MODEL_COST_MAP=True)
[ "$PIN" != "none" ] && AE+=(--ae "UV_EXCLUDE_NEWER=$PIN")
# Override HTTPS only. The two proxies on this site are complementary: the docker-config proxy
# (HTTP_PROXY, used by apt over plain HTTP) works for apt mirrors but intercepts TLS to LLM APIs,
# while the shell's proxy passes LLM TLS through but times out on plain-HTTP apt mirrors.
if [ -n "$CONTAINER_PROXY" ]; then
  AE+=(--ae "HTTPS_PROXY=$CONTAINER_PROXY" --ae "https_proxy=$CONTAINER_PROXY")
fi
AK=(--ak "config_file=configs/phase0-mini-swe-agent.yaml")
# Extra `harbor run` arguments, e.g. a Gateway (G3): EXTRA_ARGS="--base-url http://172.17.0.1:4000/v1 --api-format openai_chat_completions"
read -r -a EXTRA <<< "${EXTRA_ARGS:-}"

cat <<EOF
Step 4 plan
  models        : $MODEL_A  |  $MODEL_B
  tasks         : terminal-bench@2.0, first $N_TASKS (identical for both models)
  attempts (k)  : $K   -> $N_TRIALS agent trials
  budget        : \$$BUDGET_USD total  ->  cost_limit \$$PER_TRIAL_USD per trial (+ step/time limits from configs/phase0-mini-swe-agent.yaml)
  agent pin     : UV_EXCLUDE_NEWER=$PIN
  container HTTPS proxy: ${CONTAINER_PROXY:-<docker default>}
  telemetry     : HARBOR_TELEMETRY=$HARBOR_TELEMETRY
EOF

HARBOR_PY="$(dirname "$(readlink -f "$(command -v harbor)")")/python"
for M in "$MODEL_A" "$MODEL_B"; do
  priced=$("$HARBOR_PY" - "$M" <<'PY'
import sys
import litellm
m = sys.argv[1]
print("yes" if (litellm.model_cost.get(m) or litellm.model_cost.get(m.split("/", 1)[-1])) else "no")
PY
)
  echo "  price known   : $M -> $priced (Harbor's bundled litellm price map)"
  if [ "$priced" != "yes" ] && [ "${ALLOW_UNPRICED:-}" != "yes" ]; then
    echo "refusing: no price for $M, so cost_limit would silently do nothing. Set ALLOW_UNPRICED=yes to rely on step/time limits only." >&2
    exit 3
  fi
done

run_job() {  # name model dataset-args... (-k and limits are appended)
  local name="$1" model="$2"; shift 2
  harbor run "$@" -a mini-swe-agent -m "$model" -n "$N_CONCURRENT" --job-name "$name" \
    "${AK[@]}" --ak "cost_limit=$COST_LIMIT" "${AE[@]}" ${EXTRA[@]+"${EXTRA[@]}"}
}

if [ -n "${DRY_RUN:-}" ]; then
  echo "DRY_RUN: nothing executed."
  exit 0
fi
[ "${CONFIRM_SPEND:-}" = "yes" ] || { echo "refusing: set CONFIRM_SPEND=yes to spend up to \$$BUDGET_USD" >&2; exit 4; }

if [ "${SKIP_PREFLIGHT:-}" != "yes" ]; then
  echo "== pre-flight: hello-world x 1 per model (harness check, not an evaluation trial)"
  COST_LIMIT="${PREFLIGHT_COST_LIMIT:-0.25}"
  i=0
  for M in "$MODEL_A" "$MODEL_B"; do
    i=$((i + 1))
    run_job "p0-preflight-$STAMP-$i" "$M" -t hello-world/hello-world -k 1
    python3 bench/phase0/summarize_job.py "jobs/p0-preflight-$STAMP-$i" \
      || { echo "pre-flight failed for $M: fix the harness before spending the Step 4 budget" >&2; exit 5; }
  done
fi

COST_LIMIT="$PER_TRIAL_USD"
echo "== Step 4"
run_job "p0-step4-$STAMP-a" "$MODEL_A" --dataset terminal-bench@2.0 -l "$N_TASKS" -k "$K"
run_job "p0-step4-$STAMP-b" "$MODEL_B" --dataset terminal-bench@2.0 -l "$N_TASKS" -k "$K"

mkdir -p reports
python3 bench/phase0/summarize_job.py "jobs/p0-step4-$STAMP-a" "jobs/p0-step4-$STAMP-b" | tee "reports/phase0-step4-$STAMP.txt" || true
echo "Next: harbor view jobs  (Phase 0 Step 5 / G7; see the viewer warning in reports/phase0.md)"
