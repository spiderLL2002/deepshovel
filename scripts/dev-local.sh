#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${DEEPSHOVEL_ENV_READY:-}" != "1" ]]; then
  exec "${script_dir}/project-env.sh" -- "$0" "$@"
fi

wrangler="${DEEPSHOVEL_PROJECT_ROOT}/node_modules/.bin/wrangler"
vite="${DEEPSHOVEL_PROJECT_ROOT}/node_modules/.bin/vite"

if [[ ! -x "${wrangler}" || ! -x "${vite}" ]]; then
  echo "Dependencies are unavailable. Run npm run install:ci first." >&2
  exit 69
fi

node "${script_dir}/sync-pdfjs-assets.mjs"

echo "Applying local D1 migrations..."
CI=1 "${wrangler}" d1 migrations apply DB \
  --local \
  --config "${DEEPSHOVEL_PROJECT_ROOT}/wrangler.local.jsonc" \
  --persist-to "${DEEPSHOVEL_PROJECT_ROOT}/.wrangler/state"

# Background work (Compile progression and import, retrieval indexing and
# Claim-relation analysis) is driven by the same work pump as the installed
# service. It only calls the loopback dev server with the .dev.vars secret.
dev_port="${DEEPSHOVEL_DEV_PORT:-5173}"
pump_root="${DEEPSHOVEL_PROJECT_ROOT}/.wrangler/work-pump"
mkdir -p "${pump_root}"
python3 - "${pump_root}/config.json" "${DEEPSHOVEL_PROJECT_ROOT}/.dev.vars" "${dev_port}" "${pump_root}/status.json" <<'PY'
import json
import sys

config, secret_file, port, status = sys.argv[1:5]
with open(config, "w", encoding="utf-8") as stream:
    json.dump({
        "web_base_url": f"http://127.0.0.1:{port}",
        "secret_file": secret_file,
        "status_file": status,
    }, stream)
PY
python3 "${DEEPSHOVEL_PROJECT_ROOT}/deploy/runtime/compile-sync.py" "${pump_root}/config.json" &
pump_pid=$!
trap 'kill "${pump_pid}" 2>/dev/null || true' EXIT INT TERM

"${vite}" --port "${dev_port}" --strictPort "$@"
