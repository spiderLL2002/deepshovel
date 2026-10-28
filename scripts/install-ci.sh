#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${DEEPSHOVEL_ENV_READY:-}" != "1" ]]; then
  exec "${script_dir}/project-env.sh" -- "$0" "$@"
fi

runtime_root="${DEEPSHOVEL_PROJECT_ROOT}/.project-runtime"
expected_cache="${runtime_root}/npm-cache"
lock_dir="${runtime_root}/install.lock.d"
if ! mkdir "${lock_dir}" 2>/dev/null; then
  echo "Another dependency install is already running for ${DEEPSHOVEL_PROJECT_ROOT}." >&2
  exit 75
fi
trap 'rmdir "${lock_dir}" 2>/dev/null || true' EXIT INT TERM

if command -v sha256sum >/dev/null 2>&1; then
  lockfile_sha256="$(sha256sum "${DEEPSHOVEL_PROJECT_ROOT}/package-lock.json" | awk '{print $1}')"
else
  lockfile_sha256="$(shasum -a 256 "${DEEPSHOVEL_PROJECT_ROOT}/package-lock.json" | awk '{print $1}')"
fi
use_seeded_cache=0
seed_cache="${DEEPSHOVEL_NPM_CACHE_SEED:-}"
if [[ -n "${seed_cache}" && -r "${seed_cache}/.deepshovel-lockfile-sha256" ]]; then
  seed_lockfile_sha256="$(<"${seed_cache}/.deepshovel-lockfile-sha256")"
  if [[ "${seed_lockfile_sha256}" == "${lockfile_sha256}" ]]; then
    echo "[setup] using the matching image-seeded npm cache"
    cp -a "${seed_cache}/." "${expected_cache}/"
    use_seeded_cache=1
  fi
fi

echo "[setup] installing dependencies from package-lock.json"
export NPM_CONFIG_MAXSOCKETS=1
export NPM_CONFIG_FETCH_RETRIES=0
export NPM_CONFIG_FETCH_TIMEOUT=30000
npm_ci_args=(ci --cache "${expected_cache}")
if [[ "${use_seeded_cache}" == "1" ]]; then
  npm_ci_args+=(--prefer-offline)
fi
if command -v timeout >/dev/null 2>&1; then
  timeout \
    --signal=TERM \
    --kill-after="${DEEPSHOVEL_INSTALL_KILL_AFTER:-15s}" \
    "${DEEPSHOVEL_INSTALL_TIMEOUT:-8m}" \
    npm "${npm_ci_args[@]}"
else
  # macOS does not ship GNU timeout. The caller still owns process-level
  # cancellation, while Linux/CI retains the stricter bounded path above.
  npm "${npm_ci_args[@]}"
fi

vinext="${DEEPSHOVEL_PROJECT_ROOT}/node_modules/.bin/vinext"
if [[ ! -x "${vinext}" ]]; then
  echo "npm ci exited successfully but node_modules/.bin/vinext is unavailable." >&2
  exit 69
fi

node --input-type=module - "${DEEPSHOVEL_PROJECT_ROOT}/node_modules/.deepshovel-install.json" "${lockfile_sha256}" <<'NODE'
import { writeFile } from "node:fs/promises";

await writeFile(
  process.argv[2],
  `${JSON.stringify({
    lockfile_sha256: process.argv[3],
    node: process.version,
    platform: `${process.platform}-${process.arch}`,
  }, null, 2)}\n`,
);
NODE
echo "[setup] npm ci passed and vinext is available"
