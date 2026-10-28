#!/usr/bin/env bash
set -euo pipefail

install_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
generated="${install_root}/app/deploy/generated"
lifecycle="${install_root}/app/deploy/runtime/service-lifecycle.py"
# UI clicks, terminal commands and deployment scripts share the same lock.
case "${1:-}" in
  start|start-service|stop|stop-service|restart)
    if [[ "${2:-}" != "--locked" ]]; then
      exec python3 "${lifecycle}" lock "${install_root}/data/service-control.lock" \
        /bin/bash "${BASH_SOURCE[0]}" "$1" --locked
    fi
    ;;
esac
# shellcheck disable=SC1091
source "${generated}/deployment.env"

SYNC_LABEL="${SYNC_LABEL:-${SERVICE_LABEL%.service}.compile-sync}"
AWAKE_LABEL="${AWAKE_LABEL:-${SERVICE_LABEL%.service}.awake}"
domain="gui/$(id -u)"
agents="${HOME}/Library/LaunchAgents"

loaded() {
  launchctl print "${domain}/$1" >/dev/null 2>&1
}

load_agent() {
  local label="$1"
  local plist="${agents}/${label}.plist"
  if ! loaded "${label}"; then
    for _ in {1..30}; do
      launchctl bootstrap "${domain}" "${plist}" 2>/dev/null || true
      loaded "${label}" && break
      sleep 0.2
    done
  fi
  loaded "${label}" || { echo "LaunchAgent did not load: ${label}" >&2; return 70; }
  if launchctl print "${domain}/${label}" 2>/dev/null | grep -q 'state = running'; then
    return 0
  fi
  for _ in {1..20}; do
    launchctl kickstart "${domain}/${label}" 2>/dev/null && return 0
    sleep 0.2
  done
  echo "LaunchAgent did not start: ${label}" >&2
  return 70
}

unload_agent() {
  local label="$1"
  if loaded "${label}"; then
    launchctl bootout "${domain}/${label}" 2>/dev/null || true
    for _ in {1..120}; do
      loaded "${label}" || return 0
      sleep 0.2
    done
    echo "LaunchAgent did not unload: ${label}" >&2
    return 70
  fi
}

# Older rollback bundles may not have installed this optional component yet.
load_sync_agent() {
  if [[ -f "${agents}/${SYNC_LABEL}.plist" ]]; then load_agent "${SYNC_LABEL}"; fi
}

wait_stopped() {
  python3 "${lifecycle}" stopped "${install_root}" "${LOCAL_PORT}" "${COMPILER_PORT}"
}

start_application() {
  if ! loaded "${SERVICE_LABEL}"; then wait_stopped; fi
  load_agent "${SERVICE_LABEL}"
  load_agent "${AWAKE_LABEL}"
  load_sync_agent
}

wait_ready() {
  echo "Waiting for Web and Compiler startup (including retrieval warmup)..."
  python3 "${lifecycle}" ready "${LOCAL_HEALTH_URL}" \
    "http://127.0.0.1:${COMPILER_PORT}/ready" --compiler
  if [[ "${1:-}" == "public" ]]; then
    python3 "${lifecycle}" ready "${PUBLIC_HEALTH_URL}" --timeout 45
  fi
  echo "DeepShovel is ready."
}

case "${1:-}" in
  start)
    start_application
    load_agent "${TUNNEL_LABEL}"
    load_agent "${WATCHDOG_LABEL}"
    wait_ready public
    ;;
  start-service)
    start_application
    load_agent "${WATCHDOG_LABEL}"
    wait_ready
    ;;
  stop)
    unload_agent "${WATCHDOG_LABEL}"
    unload_agent "${TUNNEL_LABEL}"
    unload_agent "${SYNC_LABEL}"
    unload_agent "${SERVICE_LABEL}"
    wait_stopped
    unload_agent "${AWAKE_LABEL}"
    ;;
  stop-service)
    unload_agent "${WATCHDOG_LABEL}"
    unload_agent "${SYNC_LABEL}"
    unload_agent "${SERVICE_LABEL}"
    wait_stopped
    unload_agent "${AWAKE_LABEL}"
    ;;
  restart)
    unload_agent "${WATCHDOG_LABEL}"
    unload_agent "${TUNNEL_LABEL}"
    unload_agent "${SYNC_LABEL}"
    unload_agent "${SERVICE_LABEL}"
    wait_stopped
    start_application
    load_agent "${TUNNEL_LABEL}"
    load_agent "${WATCHDOG_LABEL}"
    wait_ready public
    ;;
  status)
    for label in "${SERVICE_LABEL}" "${TUNNEL_LABEL}" "${WATCHDOG_LABEL}" "${SYNC_LABEL}" "${AWAKE_LABEL}"; do
      if loaded "${label}"; then
        launchctl print "${domain}/${label}" | sed -n '1,24p'
      else
        echo "${label}: not loaded"
      fi
    done
    ;;
  open)
    open "${PUBLIC_URL}"
    ;;
  logs)
    open "${INSTALL_ROOT}/logs"
    ;;
  folder)
    open "${INSTALL_ROOT}"
    ;;
  *)
    echo "usage: $0 {start|start-service|stop|stop-service|restart|status|open|logs|folder}" >&2
    exit 64
    ;;
esac
