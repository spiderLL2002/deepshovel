#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
manifest="${script_dir}/public_manifest.py"
display_name="$(python3 "${manifest}" get display_name)"
install_directory="$(python3 "${manifest}" get install_directory)"
install_root="${HOME}/Library/Application Support/${install_directory}"
generated="${install_root}/app/deploy/generated/deployment.env"

if [[ ! -f "${generated}" ]]; then
  echo "DeepShovel public runtime is not installed at ${install_root}."
  exit 3
fi
# shellcheck disable=SC1090
source "${generated}"
domain="gui/$(id -u)"
failed=0

agent_status() {
  local name="$1" label="$2"
  if launchctl print "${domain}/${label}" 2>/dev/null | grep -q 'state = running'; then
    printf '%-18s %s\n' "${name}" "running"
  else
    printf '%-18s %s\n' "${name}" "stopped"
    failed=1
  fi
}

http_status() {
  local name="$1" url="$2" timeout="$3"
  if curl --silent --show-error --fail --max-time "${timeout}" \
    --retry 2 --retry-all-errors --retry-delay 1 "${url}" >/dev/null; then
    printf '%-18s %s\n' "${name}" "healthy"
  else
    printf '%-18s %s\n' "${name}" "unreachable"
    failed=1
  fi
}

echo "${display_name} public deployment"
agent_status "Application agent" "${SERVICE_LABEL}"
http_status "Local Web" "${LOCAL_HEALTH_URL}" 3
http_status "Local Compiler" "http://127.0.0.1:${COMPILER_PORT}/health" 3
agent_status "Tunnel agent" "${TUNNEL_LABEL}"
http_status "Public Web" "${PUBLIC_HEALTH_URL}" 6
agent_status "Recovery agent" "${WATCHDOG_LABEL}"
agent_status "Awake agent" "${AWAKE_LABEL}"
agent_status "Compile sync" "${SYNC_LABEL:-${SERVICE_LABEL%.service}.compile-sync}"
printf '%-18s %s\n' "Logs" "${install_root}/logs"
exit "${failed}"
