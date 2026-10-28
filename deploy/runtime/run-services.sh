#!/usr/bin/env bash
set -euo pipefail

install_root="${1:?installed runtime root is required}"
app_root="${install_root}/app"
generated="${app_root}/deploy/generated"
secrets_root="${install_root}/secrets"
data_root="${install_root}/data"

# launchd and manual invocations must never run two supervisors against the
# same embedded vector store, even while an older process is shutting down.
if [[ "${2:-}" != "--locked" ]]; then
  exec python3 "${app_root}/deploy/runtime/service-lifecycle.py" lock \
    "${data_root}/service-supervisor.lock" \
    /bin/bash "${BASH_SOURCE[0]}" "${install_root}" --locked
fi

# shellcheck disable=SC1091
source "${generated}/deployment.env"

web_environment="${secrets_root}/web.env"
compiler_environment="${secrets_root}/compiler.env"
compiler_executable="${install_root}/venv/bin/deepshovel-compiler"
wrangler="${app_root}/node_modules/.bin/wrangler"
wrangler_config="${generated}/wrangler.public.json"

for required in "${web_environment}" "${compiler_environment}" "${compiler_executable}" "${NODE_EXECUTABLE}" "${wrangler}" "${wrangler_config}"; do
  if [[ ! -e "${required}" ]]; then
    echo "Required runtime component is missing: ${required}" >&2
    exit 78
  fi
done

if grep -Eq '^[[:space:]]*(export[[:space:]]+)?COMPILER_SHARED_SECRET[[:space:]]*=' "${compiler_environment}"; then
  echo "Compiler-private environment must not define COMPILER_SHARED_SECRET." >&2
  exit 78
fi

set -a
# shellcheck disable=SC1090
source "${web_environment}"
# shellcheck disable=SC1090
source "${compiler_environment}"
set +a

export COMPILER_ENV=production
export COMPILER_DATA_DIR="${data_root}/compiler"
export HOME="${install_root}/runtime-home"
export XDG_CONFIG_HOME="${install_root}/runtime-home/config"
export TMPDIR="${install_root}/runtime-home/tmp"
mkdir -p "${COMPILER_DATA_DIR}" "${HOME}" "${XDG_CONFIG_HOME}" "${TMPDIR}"

compiler_pid=""
web_pid=""
compiler_started_at=0
web_started_at=0
compiler_healthy_at=0
web_healthy_at=0
compiler_unhealthy_since=0
web_unhealthy_since=0
compiler_retry_delay=1
web_retry_delay=1
compiler_next_start=0
web_next_start=0

timestamp() {
  date -u '+%Y-%m-%dT%H:%M:%SZ'
}

service_log() {
  printf '%s %s\n' "$(timestamp)" "$*" >&2
}

collect_process_tree() {
  local root_pid="$1"
  local child_pid
  while IFS= read -r child_pid; do
    [[ -z "${child_pid}" ]] || collect_process_tree "${child_pid}"
  done < <(pgrep -P "${root_pid}" 2>/dev/null || true)
  printf '%s\n' "${root_pid}"
}

terminate_with_deadline() {
  local root_pid="$1"
  local attempt pid alive
  local pids=()
  # Save descendants before signalling: a parent can exit first and orphan a
  # worker that ignores TERM. Re-running pgrep on the dead parent loses it.
  while IFS= read -r pid; do
    pids+=("${pid}")
  done < <(collect_process_tree "${root_pid}")
  kill -TERM "${pids[@]}" 2>/dev/null || true
  for attempt in {1..4}; do
    alive=0
    for pid in "${pids[@]}"; do
      if kill -0 "${pid}" 2>/dev/null; then alive=1; fi
    done
    (( alive )) || return 0
    sleep 1
  done
  kill -KILL "${pids[@]}" 2>/dev/null || true
}

cleanup() {
  local web_cleanup_pid=""
  local compiler_cleanup_pid=""
  trap - EXIT
  trap '' INT TERM HUP
  if [[ -n "${web_pid}" ]]; then
    terminate_with_deadline "${web_pid}" &
    web_cleanup_pid=$!
  fi
  if [[ -n "${compiler_pid}" ]]; then
    terminate_with_deadline "${compiler_pid}" &
    compiler_cleanup_pid=$!
  fi
  [[ -z "${web_cleanup_pid}" ]] || wait "${web_cleanup_pid}" 2>/dev/null || true
  [[ -z "${compiler_cleanup_pid}" ]] || wait "${compiler_cleanup_pid}" 2>/dev/null || true
  [[ -z "${web_pid}" ]] || wait "${web_pid}" 2>/dev/null || true
  [[ -z "${compiler_pid}" ]] || wait "${compiler_pid}" 2>/dev/null || true
}
# A stop signal must leave the supervisor loop; cleanup alone would return
# to that loop and spawn replacement children while launchd is unloading us.
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

start_compiler() {
  "${compiler_executable}" &
  compiler_pid=$!
  compiler_started_at="$(date +%s)"
  compiler_healthy_at=0
  compiler_unhealthy_since=0
  compiler_next_start=0
  service_log "Compiler started (pid ${compiler_pid})."
}

start_web() {
  (
    cd "${app_root}"
    exec "${NODE_EXECUTABLE}" "${wrangler}" dev \
      --config "${wrangler_config}" \
      --env-file "${web_environment}" \
      --local \
      --ip 127.0.0.1 \
      --port "${LOCAL_PORT}" \
      --persist-to "${data_root}/wrangler" \
      --show-interactive-dev-session false \
      --log-level warn
  ) &
  web_pid=$!
  web_started_at="$(date +%s)"
  web_healthy_at=0
  web_unhealthy_since=0
  web_next_start=0
  service_log "Web started (pid ${web_pid})."
}

stop_compiler() {
  local pid="${compiler_pid}"
  [[ -z "${pid}" ]] || terminate_with_deadline "${pid}"
  [[ -z "${pid}" ]] || wait "${pid}" 2>/dev/null || true
  compiler_pid=""
}

stop_web() {
  local pid="${web_pid}"
  [[ -z "${pid}" ]] || terminate_with_deadline "${pid}"
  [[ -z "${pid}" ]] || wait "${pid}" 2>/dev/null || true
  web_pid=""
}

schedule_compiler_restart() {
  local reason="$1" now
  now="$(date +%s)"
  compiler_next_start=$((now + compiler_retry_delay))
  service_log "Compiler ${reason}; retrying in ${compiler_retry_delay}s."
  compiler_retry_delay=$((compiler_retry_delay < 15 ? compiler_retry_delay * 2 : 30))
  compiler_healthy_at=0
  compiler_unhealthy_since=0
}

schedule_web_restart() {
  local reason="$1" now
  now="$(date +%s)"
  web_next_start=$((now + web_retry_delay))
  service_log "Web ${reason}; retrying in ${web_retry_delay}s."
  web_retry_delay=$((web_retry_delay < 15 ? web_retry_delay * 2 : 30))
  web_healthy_at=0
  web_unhealthy_since=0
}

reap_exited_components() {
  local status=0
  if [[ -n "${web_pid}" ]] && ! kill -0 "${web_pid}" 2>/dev/null; then
    wait "${web_pid}" 2>/dev/null || status=$?
    web_pid=""
    schedule_web_restart "exited with status ${status}"
  fi
  status=0
  if [[ -n "${compiler_pid}" ]] && ! kill -0 "${compiler_pid}" 2>/dev/null; then
    wait "${compiler_pid}" 2>/dev/null || status=$?
    compiler_pid=""
    schedule_compiler_restart "exited with status ${status}"
  fi
}

start_due_components() {
  local now
  now="$(date +%s)"
  if [[ -z "${web_pid}" ]] && (( now >= web_next_start )); then
    start_web
  fi
  if [[ -z "${compiler_pid}" ]] && (( now >= compiler_next_start )); then
    start_compiler
  fi
}

# Log only bounded transport metadata; never response bodies, URLs, or credentials.
probe_health() {
  local result status elapsed exit_code=0
  result="$(curl --silent --fail --max-time 5 --output /dev/null \
    --write-out '%{http_code} %{time_total}' "$1")" || exit_code=$?
  read -r status elapsed <<< "${result}"
  [[ "${status}" =~ ^[0-9]{3}$ ]] || status=000
  [[ "${elapsed}" =~ ^[0-9]{1,6}\.[0-9]{1,6}$ ]] || elapsed=unknown
  health_probe_result="curl_exit=${exit_code} http_status=${status} elapsed_s=${elapsed}"
  return "${exit_code}"
}

check_web_health() {
  local now failed_for
  [[ -n "${web_pid}" ]] || return 0
  if probe_health "${LOCAL_HEALTH_URL}"; then
    now="$(date +%s)"
    if (( web_unhealthy_since != 0 )); then
      service_log "Web health recovered; ${health_probe_result}."
    fi
    web_unhealthy_since=0
    if (( web_healthy_at == 0 )); then
      web_healthy_at="${now}"
      service_log "Web is healthy."
    elif (( now - web_healthy_at >= 60 )); then
      web_retry_delay=1
    fi
    return 0
  fi
  now="$(date +%s)"
  if (( web_healthy_at == 0 && now - web_started_at < 90 )); then
    return 0
  fi
  if (( web_unhealthy_since == 0 )); then
    web_unhealthy_since="${now}"
  fi
  failed_for=$((now - web_unhealthy_since))
  service_log "Web health probe failed; ${health_probe_result} consecutive_failure_s=${failed_for}."
  if (( failed_for < 120 )); then
    return 0
  fi
  service_log "Web remained unhealthy for ${failed_for}s; restarting only Web."
  stop_web
  schedule_web_restart "became unhealthy"
}

check_compiler_health() {
  local now failed_for
  [[ -n "${compiler_pid}" ]] || return 0
  # /ready is a deployment/warmup signal, not a reason to kill a live Compiler.
  if probe_health "http://127.0.0.1:${COMPILER_PORT}/health"; then
    now="$(date +%s)"
    if (( compiler_unhealthy_since != 0 )); then
      service_log "Compiler health recovered; ${health_probe_result}."
    fi
    compiler_unhealthy_since=0
    if (( compiler_healthy_at == 0 )); then
      compiler_healthy_at="${now}"
      service_log "Compiler is healthy."
    elif (( now - compiler_healthy_at >= 60 )); then
      compiler_retry_delay=1
    fi
    return 0
  fi
  now="$(date +%s)"
  if (( compiler_healthy_at == 0 && now - compiler_started_at < 240 )); then
    return 0
  fi
  if (( compiler_unhealthy_since == 0 )); then
    compiler_unhealthy_since="${now}"
  fi
  failed_for=$((now - compiler_unhealthy_since))
  service_log "Compiler health probe failed; ${health_probe_result} consecutive_failure_s=${failed_for}."
  if (( failed_for < 120 )); then
    return 0
  fi
  service_log "Compiler remained unhealthy for ${failed_for}s; restarting only Compiler."
  stop_compiler
  schedule_compiler_restart "became unhealthy"
}

# Web starts immediately so the public UI remains available while the heavier
# Compiler initializes. Each component is then supervised independently.
start_web
start_compiler
next_health_check=0
while true; do
  reap_exited_components
  start_due_components
  now="$(date +%s)"
  if (( now >= next_health_check )); then
    check_web_health
    check_compiler_health
    next_health_check=$((now + 5))
  fi
  sleep 2
done
