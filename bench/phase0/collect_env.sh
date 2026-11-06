#!/bin/bash
# Print the versions and host facts that Phase 0 results depend on (PLAN.md 8.1 "ホスト情報", S5).
# Output is meant to be saved:  bench/phase0/collect_env.sh > reports/phase0-env.txt
# Secrets are never printed: proxy URLs are reduced to host names, API keys are not read.
cd "$(dirname "$0")/../.."
source bench/env.sh

echo "collected_at: $(date -Is)"
echo "git_sha: $(git rev-parse HEAD 2>/dev/null || echo 'n/a (not a git repository)')"
echo "host: $(hostname) | $(uname -sr) | $(. /etc/os-release && echo "$PRETTY_NAME")"
echo "cpus: $(nproc) | mem: $(free -g | awk '/Mem:/ {print $2"GiB"}')"
echo "user: $(id -un) | groups: $(id -Gn)"
echo
echo "== Docker"
docker version --format 'client={{.Client.Version}} server={{.Server.Version}} api={{.Server.APIVersion}}' 2>&1
docker compose version 2>&1 | head -1
docker buildx version 2>&1 | head -1
docker info --format 'storage_driver={{.Driver}} cgroup={{.CgroupVersion}} root={{.DockerRootDir}} daemon_proxy={{.HTTPSProxy}}' 2>&1
echo "cli-plugins (user): $(ls ~/.docker/cli-plugins 2>/dev/null | tr '\n' ' ')"
echo
echo "== Harbor / Python"
harbor --version 2>&1 | head -1
uv --version
uv tool list 2>/dev/null | grep -E '^harbor' || true
"$(dirname "$(readlink -f "$(command -v harbor)")")/python" --version
echo
echo "== Copilot CLI (host agent route; the login itself is never read or printed)"
copilot --version 2>&1 | head -1 || echo "copilot: not found"
echo "COPILOT_HOME=${COPILOT_HOME:-<default ~/.copilot>}"
echo
echo "== Network facts"
echo "shell proxy host : $(echo "${HTTPS_PROXY:-none}" | sed -E 's#^[a-z]+://##; s#:.*##')"
echo "docker-config proxy host: $(python3 - <<'PY'
import json, os, re
p = os.path.expanduser("~/.docker/config.json")
try:
    v = json.load(open(p)).get("proxies", {}).get("default", {}).get("httpsProxy", "none")
except Exception:
    v = "none"
print(re.sub(r"^[a-z]+://", "", v).split(":")[0])
PY
)"
echo "HARBOR_TELEMETRY=${HARBOR_TELEMETRY}"
echo
echo "== Images used (digest-pinned in tasks)"
docker image ls --digests --format '{{.Repository}}:{{.Tag}} {{.Digest}}' 2>/dev/null | grep -E '^(python:|ubuntu:24.04|alexgshaw/)' | head -8
