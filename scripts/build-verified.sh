#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${DEEPSHOVEL_ENV_READY:-}" != "1" ]]; then
  exec "${script_dir}/project-env.sh" -- "$0" "$@"
fi

vinext="${DEEPSHOVEL_PROJECT_ROOT}/node_modules/.bin/vinext"
if [[ ! -x "${vinext}" ]]; then
  echo "vinext is unavailable. Run npm run install:ci and wait for it to finish before building." >&2
  exit 69
fi

node "${script_dir}/sync-pdfjs-assets.mjs"
node "${script_dir}/sync-atlas-workbench.mjs"

echo "Running bounded vinext build..."
if command -v timeout >/dev/null 2>&1; then
  timeout \
    --signal=TERM \
    --kill-after="${DEEPSHOVEL_BUILD_KILL_AFTER:-10s}" \
    "${DEEPSHOVEL_BUILD_TIMEOUT:-3m}" \
    "${vinext}" build
else
  # macOS does not ship GNU timeout. The caller still owns process-level
  # cancellation, while CI/Linux keeps the stricter bounded path above.
  "${vinext}" build
fi

"${script_dir}/validate-artifact.sh"
