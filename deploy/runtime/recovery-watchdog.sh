#!/usr/bin/env bash
set -euo pipefail

install_root="${1:?installed runtime root is required}"
generated="${install_root}/app/deploy/generated"
# shellcheck disable=SC1091
source "${generated}/deployment.env"

domain="gui/$(id -u)"
public_failures=0
next_tunnel_recovery=0

timestamp() {
  date -u '+%Y-%m-%dT%H:%M:%SZ'
}

watchdog_log() {
  printf '%s %s\n' "$(timestamp)" "$*" >&2
}

agent_running() {
  launchctl print "${domain}/$1" 2>/dev/null | grep -q 'state = running'
}

kickstart_if_stopped() {
  local label="$1" name="$2"
  if ! agent_running "${label}"; then
    watchdog_log "${name} agent is stopped; requesting restart."
    launchctl kickstart "${domain}/${label}" 2>/dev/null || true
  fi
}

while true; do
  kickstart_if_stopped "${SERVICE_LABEL}" "Application"
  kickstart_if_stopped "${TUNNEL_LABEL}" "Tunnel"

  if curl --silent --fail --max-time 3 "${LOCAL_HEALTH_URL}" >/dev/null; then
    if curl --silent --fail --max-time 6 \
      --retry 2 --retry-all-errors --retry-delay 1 \
      "${PUBLIC_HEALTH_URL}" >/dev/null; then
      public_failures=0
    else
      public_failures=$((public_failures + 1))
      now="$(date +%s)"
      if (( public_failures >= 3 && now >= next_tunnel_recovery )); then
        watchdog_log "Public health failed three checks while local Web stayed healthy; restarting Tunnel."
        launchctl kickstart -k "${domain}/${TUNNEL_LABEL}" 2>/dev/null || true
        public_failures=0
        next_tunnel_recovery=$((now + 60))
      fi
    fi
  else
    # The application supervisor owns local recovery. Avoid restarting a
    # healthy Tunnel merely because its origin is temporarily unavailable.
    public_failures=0
  fi
  sleep 10
done
