#!/usr/bin/env bash
set -Eeuo pipefail

mode="${1:-install}"
if [[ "${mode}" != "install" && "${mode}" != "update" ]]; then
  echo "usage: $0 {install|update}" >&2
  exit 64
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "${script_dir}/.." && pwd)"
manifest="${script_dir}/public_manifest.py"
# shellcheck disable=SC1091
source "${project_root}/deploy/runtime/runtime-swap.sh"
python3 "${manifest}" validate --require-public >/dev/null

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Local public installation is supported only on macOS." >&2
  exit 69
fi
for command in node npm python3 python3.12 rsync curl launchctl plutil swiftc codesign lsof shlock; do
  command -v "${command}" >/dev/null 2>&1 || { echo "Required command is missing: ${command}" >&2; exit 69; }
done

install_directory="$(python3 "${manifest}" get install_directory)"
display_name="$(python3 "${manifest}" get display_name)"
service_namespace="$(python3 "${manifest}" get service_namespace)"
install_root="${HOME}/Library/Application Support/${install_directory}"
app_root="${install_root}/app"
next_app="${install_root}/app.next"
rollback="${install_root}/app.rollback"
venv_root="${install_root}/venv"
next_venv="${install_root}/venv.next"
rollback_venv="${install_root}/venv.rollback"
install_lock="${install_root}/.install.lock"
agents="${HOME}/Library/LaunchAgents"
domain="gui/$(id -u)"
requires_access="$(python3 "${manifest}" get requires_cloudflare_access)"
compiler_port="$(python3 "${manifest}" get compiler_port)"
local_port="$(python3 "${manifest}" get local_port)"
service_label="${service_namespace}.service"
tunnel_label="${service_namespace}.tunnel"
watchdog_label="${service_namespace}.watchdog"
control_label="${service_namespace}.control"
awake_label="${service_namespace}.awake"
sync_label="${service_namespace}.compile-sync"
lock_acquired=0
service_was_loaded=0
tunnel_config_needs_reload=0

release_install_lock() {
  if [[ "${lock_acquired}" == "1" ]]; then
    rm -f "${install_lock}"
  fi
}

rollback_app() {
  local status="${1:-70}"
  local rollback_ready=0
  local control_script=""
  # Rollback is the final consistency boundary. Disable recursive ERR handling
  # and ignore a second signal until app and venv have both been restored.
  trap - ERR
  trap '' INT TERM HUP
  if runtime_swap_pending; then
    for candidate in \
      "${app_root}/deploy/runtime/service-control.sh" \
      "${rollback}/deploy/runtime/service-control.sh"; do
      if [[ -x "${candidate}" ]]; then control_script="${candidate}"; break; fi
    done
    if launchctl print "${domain}/${service_label}" >/dev/null 2>&1; then
      if [[ -z "${control_script}" ]] || ! "${control_script}" stop-service; then
        echo "Failed to stop the replacement service; the installed app was left intact." >&2
        exit 70
      fi
    fi
    if ! runtime_swap_restore; then
      echo "Failed to restore the previous application runtime." >&2
      exit 70
    fi
  fi
  if [[ "${service_was_loaded}" == "1" ]] \
    && ! launchctl print "${domain}/${service_label}" >/dev/null 2>&1; then
    chmod +x "${app_root}/deploy/runtime/"*.sh
    if ! "${app_root}/deploy/runtime/service-control.sh" start-service; then
      echo "The previous app was restored, but its service did not start." >&2
      exit 70
    fi
    # shellcheck disable=SC1090
    source "${app_root}/deploy/generated/deployment.env"
    for _ in {1..240}; do
      if curl --silent --show-error --fail --max-time 2 "${LOCAL_HEALTH_URL}" >/dev/null \
        && curl --silent --show-error --fail --max-time 3 "http://127.0.0.1:${COMPILER_PORT}/health" >/dev/null; then
        rollback_ready=1
        break
      fi
      sleep 1
    done
    if [[ "${rollback_ready}" != "1" ]]; then
      echo "The previous app was restored, but it did not become healthy." >&2
      exit 70
    fi
  fi
  exit "${status}"
}

on_install_error() {
  local status="$?"
  # `set -E` makes every subshell and command substitution inherit this trap.
  # Only the main shell may roll back; a subshell just propagates its failure,
  # otherwise the rollback would run twice and stop the restored runtime.
  # (Not BASHPID: it needs bash 4, and macOS ships bash 3.2.)
  if (( BASH_SUBSHELL > 0 )); then exit "${status}"; fi
  rollback_app "${status}"
}

on_install_signal() {
  rollback_app "$1"
}

trap release_install_lock EXIT
trap on_install_error ERR
trap 'on_install_signal 130' INT
trap 'on_install_signal 143' TERM
trap 'on_install_signal 129' HUP

python3 "${manifest}" registry-check --install-root "${install_root}" >/dev/null

if [[ "${mode}" == "install" ]] && ! launchctl print "${domain}/${service_namespace}.service" >/dev/null 2>&1; then
  if lsof -nP -iTCP:"${compiler_port}" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port ${compiler_port} is in use by a development Compiler." >&2
    echo "Stop npm run dev:compiler before first installation so persistent data can be copied consistently." >&2
    exit 75
  fi
  if lsof -nP -iTCP:5173 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port 5173 is in use by a development Web process." >&2
    echo "Stop npm run dev before first installation so the local D1/R2 state can be copied consistently." >&2
    exit 75
  fi
  if lsof -nP -iTCP:"${local_port}" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Production Web port ${local_port} is already in use." >&2
    exit 75
  fi
fi

if [[ ! -f "${project_root}/.dev.vars" || ! -f "${project_root}/compiler/.env.local" ]]; then
  echo "Root .dev.vars and compiler/.env.local are required before public installation." >&2
  exit 78
fi

mkdir -p "${install_root}" "${install_root}/data/wrangler" "${install_root}/data/compiler" \
  "${install_root}/logs" "${install_root}/secrets" "${install_root}/cloudflared" \
  "${install_root}/runtime-home/config" "${install_root}/runtime-home/tmp" "${agents}"
shlock -p "$$" -f "${install_lock}" || {
  echo "Another DeepShovel install or update is already running." >&2
  exit 75
}
lock_acquired=1

if [[ ! -f "${install_root}/secrets/web.env" ]]; then
  cp "${project_root}/.dev.vars" "${install_root}/secrets/web.env"
fi
if [[ ! -f "${install_root}/secrets/compiler.env" ]]; then
  cp "${project_root}/compiler/.env.local" "${install_root}/secrets/compiler.env"
fi
chmod 600 "${install_root}/secrets/web.env" "${install_root}/secrets/compiler.env"

if [[ ! -e "${install_root}/data/.initial-import-complete" ]]; then
  if [[ -d "${project_root}/.wrangler/state" ]]; then
    rsync -a "${project_root}/.wrangler/state/" "${install_root}/data/wrangler/"
  fi
  if [[ -d "${project_root}/compiler/.compiler-data" ]]; then
    rsync -a "${project_root}/compiler/.compiler-data/" "${install_root}/data/compiler/"
  fi
  touch "${install_root}/data/.initial-import-complete"
fi

if [[ "${PUBLIC_SKIP_TESTS:-0}" != "1" ]]; then
  (cd "${project_root}" && npm run test:node)
fi
(cd "${project_root}" && PUBLIC_DEPLOY_BUILD=1 npm run build)

rm -rf "${next_app}"
mkdir -p "${next_app}"
while IFS= read -r relative; do
  source_path="${project_root}/${relative}"
  [[ -e "${source_path}" ]] || { echo "Missing runtime path: ${relative}" >&2; exit 66; }
  mkdir -p "${next_app}/$(dirname "${relative}")"
  if [[ -d "${source_path}" ]]; then
    rsync -a --delete "${source_path}/" "${next_app}/${relative}/"
  else
    rsync -a "${source_path}" "${next_app}/${relative}"
  fi
done < <(python3 "${manifest}" get runtime_paths)

# The Atlas reference dataset is protected local data and is not in the
# repository, so a source build lacks it. Carry every installed Atlas file the
# new bundle does not provide into the replacement; files shipped by the new
# build win. Without this an ordinary update would empty the Atlas.
installed_atlas="${app_root}/dist/client/geochemical-atlas"
if [[ -d "${installed_atlas}" && ! -L "${installed_atlas}" ]]; then
  mkdir -p "${next_app}/dist/client/geochemical-atlas"
  rsync -a --ignore-existing "${installed_atlas}/" "${next_app}/dist/client/geochemical-atlas/"
fi

python3 "${manifest}" generate-runtime --output "${next_app}/deploy/generated" --install-root "${install_root}"
(cd "${next_app}" && npm ci --ignore-scripts --no-audit --no-fund)

# Stamp the staged Compiler with the source identity the Web build embedded
# (same rules as build/release-stamp-vite-plugin.ts); /health reports it as
# "release". Only the staged copy carries the file, never the source tree.
source_commit="unknown"
if commit="$(git -C "${project_root}" rev-parse --short=12 HEAD 2>/dev/null)" \
  && [[ "${commit}" =~ ^[0-9a-f]{12}$ ]]; then
  source_commit="${commit}"
  if [[ -n "$(git -C "${project_root}" status --porcelain --untracked-files=no 2>/dev/null)" ]]; then
    source_commit="${commit}-dirty"
  fi
fi
printf '%s\n' "${source_commit}" > "${next_app}/compiler/compiler_service/SOURCE_COMMIT"

rm -rf "${next_venv}"
python3.12 -m venv "${next_venv}"
"${next_venv}/bin/python" -m pip install --disable-pip-version-check --quiet "${next_app}/compiler"

if [[ "${mode}" == "install" ]]; then
  if [[ "${requires_access}" == "true" && ! -e "${install_root}/cloudflared/access-confirmed" ]]; then
    if [[ "${DEEPSHOVEL_ACCESS_CONFIRMED:-}" == "1" ]]; then
      touch "${install_root}/cloudflared/access-confirmed"
    elif [[ -t 0 ]]; then
      echo "This private research system requires Cloudflare Access on the configured hostname."
      echo "Configure an Access policy for the site and a bypass only for /api/health."
      read -r -p "Type ACCESS-CONFIGURED to continue: " answer
      [[ "${answer}" == "ACCESS-CONFIGURED" ]] || { echo "Installation stopped before DNS publication." >&2; exit 77; }
      touch "${install_root}/cloudflared/access-confirmed"
    else
      echo "Set DEEPSHOVEL_ACCESS_CONFIRMED=1 only after Cloudflare Access is configured." >&2
      exit 77
    fi
  fi
  "${script_dir}/setup_public_tunnel.sh" "${install_root}"
elif [[ ! -f "${install_root}/cloudflared/config.yml" ]]; then
  echo "Tunnel configuration is missing; run make public-install first." >&2
  exit 78
else
  tunnel_id="$(tr -d '\r\n' <"${install_root}/cloudflared/tunnel-id")"
  python3 "${manifest}" generate-tunnel \
    --output "${install_root}/cloudflared/config.yml" \
    --tunnel-id "${tunnel_id}" \
    --credentials-file "${install_root}/cloudflared/credentials.json"
  chmod 600 "${install_root}/cloudflared/config.yml"
  tunnel_pid="$(launchctl print "${domain}/${tunnel_label}" 2>/dev/null \
    | awk '/^[[:space:]]*pid = / {print $3; exit}' || true)"
  if [[ -n "${tunnel_pid}" ]]; then
    tunnel_process_started="$(ps -o lstart= -p "${tunnel_pid}" | xargs)"
    tunnel_process_started_epoch="$(LC_ALL=C date -j -f '%a %b %d %T %Y' \
      "${tunnel_process_started}" '+%s')"
    tunnel_config_modified_epoch="$(stat -f '%m' \
      "${install_root}/cloudflared/config.yml")"
    if (( tunnel_config_modified_epoch > tunnel_process_started_epoch )); then
      tunnel_config_needs_reload=1
    fi
  fi
fi

if [[ -f "${app_root}/deploy/generated/deployment.env" ]]; then
  # shellcheck disable=SC1090
  source "${app_root}/deploy/generated/deployment.env"
  if launchctl print "${domain}/${service_label}" >/dev/null 2>&1; then
    service_was_loaded=1
  fi
  if [[ "${mode}" == "update" ]]; then
    "${app_root}/deploy/runtime/service-control.sh" stop-service
  else
    "${app_root}/deploy/runtime/service-control.sh" stop
  fi
fi
for port in "${local_port}" "${compiler_port}"; do
  for ((port_wait = 0; port_wait < 180; port_wait += 1)); do
    if ! lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
  if lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Service port ${port} is still in use after shutdown; update aborted." >&2
    exit 75
  fi
done

# Uvicorn can stop listening before an in-flight retrieval task releases the
# embedded Qdrant file lock. Do not activate or warm the replacement runtime
# until that exact persistent lock is free.
retrieval_lock="${install_root}/data/compiler/retrieval/qdrant/.lock"
for ((lock_wait = 0; lock_wait < 180; lock_wait += 1)); do
  if [[ ! -e "${retrieval_lock}" ]] || ! lsof "${retrieval_lock}" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
if [[ -e "${retrieval_lock}" ]] && lsof "${retrieval_lock}" >/dev/null 2>&1; then
  echo "Retrieval vector-store lock is still held after service shutdown; update aborted." >&2
  exit 75
fi

runtime_swap_activate \
  "${app_root}" "${next_app}" "${rollback}" \
  "${venv_root}" "${next_venv}" "${rollback_venv}"
python3 - "${venv_root}/bin" "${next_venv}" "${venv_root}" <<'PY'
from pathlib import Path
import sys

bin_dir = Path(sys.argv[1])
old_prefix, new_prefix = sys.argv[2:]
entrypoints = sorted(bin_dir.glob("deepshovel-*"))
if not entrypoints:
    raise SystemExit("No DeepShovel entrypoints were installed")
for entrypoint in entrypoints:
    content = entrypoint.read_text(encoding="utf-8")
    if content.count(old_prefix) != 1:
        raise SystemExit(
            f"DeepShovel entrypoint {entrypoint.name} did not contain exactly one staged venv path"
        )
    entrypoint.write_text(content.replace(old_prefix, new_prefix), encoding="utf-8")
PY
grep -F "${venv_root}/bin/python" "${venv_root}/bin/deepshovel-compiler" >/dev/null
"${venv_root}/bin/python" -c 'import compiler_service'
chmod +x "${app_root}/deploy/runtime/"*.sh

wrangler="${app_root}/node_modules/.bin/wrangler"
python3 "${app_root}/deploy/runtime/apply-local-migrations.py" \
  --wrangler "${wrangler}" \
  --config "${app_root}/deploy/generated/wrangler.public.json" \
  --persist-to "${install_root}/data/wrangler"

# Materialize retrieval models and repair any complete lexical-only projection
# before launchd exposes the replacement runtime. Persistent model files live
# outside the application swap and make subsequent updates fast.
(
  set -a
  # shellcheck disable=SC1090
  source "${install_root}/secrets/web.env"
  # shellcheck disable=SC1090
  source "${install_root}/secrets/compiler.env"
  set +a
  export COMPILER_ENV=production
  export COMPILER_DATA_DIR="${install_root}/data/compiler"
  export HOME="${install_root}/runtime-home"
  export XDG_CONFIG_HOME="${install_root}/runtime-home/config"
  export TMPDIR="${install_root}/runtime-home/tmp"
  mkdir -p "${COMPILER_DATA_DIR}" "${HOME}" "${XDG_CONFIG_HOME}" "${TMPDIR}"
  "${venv_root}/bin/python" -m compiler_service.retrieval_warmup
  "${venv_root}/bin/python" -m compiler_service.openalex_warmup
)

"${script_dir}/install_control_app.sh" "${install_root}"
control_executable="${HOME}/Applications/${display_name} Control.app/Contents/MacOS/DeepShovelControl"
plist_stage="${install_root}/generated-plists"
rm -rf "${plist_stage}"
python3 "${manifest}" generate-plists \
  --output "${plist_stage}" \
  --install-root "${install_root}" \
  --cloudflared "$(command -v cloudflared)" \
  --control-executable "${control_executable}"

for plist in "${plist_stage}/"*.plist; do
  plutil -lint "${plist}" >/dev/null
  cp "${plist}" "${agents}/$(basename "${plist}")"
done

for label in "${service_label}" "${tunnel_label}" "${watchdog_label}" "${control_label}" "${awake_label}" "${sync_label}"; do
  if ! launchctl print "${domain}/${label}" >/dev/null 2>&1; then
    launchctl bootstrap "${domain}" "${agents}/${label}.plist"
  fi
done
if ! launchctl print "${domain}/${service_label}" 2>/dev/null | grep -q 'state = running'; then
  launchctl kickstart "${domain}/${service_label}"
fi
if [[ "${mode}" == "install" || "${tunnel_config_needs_reload}" == "1" ]] \
  || ! launchctl print "${domain}/${tunnel_label}" 2>/dev/null | grep -q 'state = running'; then
  launchctl kickstart -k "${domain}/${tunnel_label}"
fi
if ! launchctl print "${domain}/${watchdog_label}" 2>/dev/null | grep -q 'state = running'; then
  launchctl kickstart "${domain}/${watchdog_label}"
fi
launchctl kickstart -k "${domain}/${control_label}"
launchctl kickstart -k "${domain}/${awake_label}"
# The agents no longer start at load. The work pump drives imports, indexing
# and Claim-relation analysis, so an update restarts it on the new code.
launchctl kickstart -k "${domain}/${sync_label}"

# shellcheck disable=SC1090
source "${app_root}/deploy/generated/deployment.env"
runtime_asset="$(find "${app_root}/dist/client/assets" -maxdepth 1 -type f -name '*.js' -print -quit)"
if [[ -z "${runtime_asset}" ]]; then
  echo "Built Web bundle has no JavaScript runtime asset." >&2
  rollback_app 70
fi
runtime_asset_relative="${runtime_asset#"${app_root}/dist/client/"}"
local_asset_url="http://127.0.0.1:${LOCAL_PORT}/${runtime_asset_relative}"
public_asset_url="${PUBLIC_URL%/}/${runtime_asset_relative}"
local_ready=0
# Wrangler/workerd can spend well over a minute materializing a freshly swapped
# production bundle on this Mac even though both supervised processes are healthy.
for _ in {1..240}; do
  if curl --silent --show-error --fail --max-time 3 "${LOCAL_HEALTH_URL}" >/dev/null \
    && curl --silent --show-error --fail --max-time 3 "http://127.0.0.1:${COMPILER_PORT}/health" >/dev/null \
    && curl --silent --show-error --fail --max-time 6 "${local_asset_url}" >/dev/null; then
    local_ready=1
    break
  fi
  sleep 1
done
if [[ "${local_ready}" != "1" ]]; then
  echo "Local Web did not become healthy. See ${install_root}/logs/service.stderr.log" >&2
  rollback_app 70
fi

runtime_swap_commit
trap - ERR

public_ready=0
for _ in {1..30}; do
  if curl --silent --show-error --fail --max-time 6 "${PUBLIC_HEALTH_URL}" >/dev/null \
    && curl --compressed --silent --show-error --fail --max-time 45 "${public_asset_url}" >/dev/null; then
    public_ready=1
    break
  fi
  sleep 2
done
if [[ "${public_ready}" != "1" ]]; then
  echo "Local service is healthy, but public health is unavailable. See ${install_root}/logs/tunnel.stderr.log" >&2
  exit 69
fi

tunnel_id="$(tr -d '\r\n' <"${install_root}/cloudflared/tunnel-id")"
python3 "${manifest}" registry-write --install-root "${install_root}" --tunnel-id "${tunnel_id}" >/dev/null
echo "${display_name} is online at ${PUBLIC_URL}"
if [[ "${mode}" == "install" ]]; then open "${PUBLIC_URL}"; fi
