# Source this before running Harbor:  source bench/env.sh
# (no `set -e` here: this file is sourced into interactive shells)

# G4 (PLAN.md 8.3): Harbor sends usage stats to PostHog unless this is off. Always opt out.
export HARBOR_TELEMETRY=off

# Harbor itself must be run from the pinned uv tool install (see reports/phase0.md for the version).
export PATH="$HOME/.local/bin:$PATH"

# The Docker client config (~/.docker/config.json) injects the corporate proxy into builds and
# containers. Direct internet access is blocked on this host except for a few sites.
# Reaching a host-side service (e.g. the mock LLM or a gateway on the docker0 gateway IP) from a
# container requires NO_PROXY for that IP; bench/phase0/*.sh pass it with `--ae` per run.
export BENCH_HOST_GATEWAY_IP="${BENCH_HOST_GATEWAY_IP:-172.17.0.1}"

# Plain, non-interactive output (no colours) so logs can be diffed and archived.
export NO_COLOR=1
