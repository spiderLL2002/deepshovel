#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${DEEPSHOVEL_ENV_READY:-}" != "1" ]]; then
  exec "${script_dir}/project-env.sh" -- "$0" "$@"
fi

require_pdfjs_asset() {
  local label="$1"
  local directory="$2"
  shift 2
  [[ -d "${directory}" ]] || { echo "Missing PDF.js ${label} directory: ${directory}" >&2; return 1; }
  local pattern
  for pattern in "$@"; do
    [[ -n "$(find "${directory}" -maxdepth 1 -type f -name "${pattern}" -print -quit)" ]] && return 0
  done
  echo "Missing PDF.js ${label} in ${directory}" >&2
  return 1
}

verify_pdfjs_assets() {
  local root="$1"
  require_pdfjs_asset "CMap" "${root}/cmaps" "*.bcmap"
  require_pdfjs_asset "standard font" "${root}/standard_fonts" "*.pfb" "*.ttf"
  require_pdfjs_asset "WASM decoder" "${root}/wasm" "*.wasm"
  require_pdfjs_asset "decoder fallback" "${root}/wasm" "*_fallback.js"
}

if [[ "${1:-}" == "--pdfjs-assets-only" ]]; then
  [[ "$#" == 2 ]] || { echo "usage: $0 --pdfjs-assets-only <pdfjs-root>" >&2; exit 64; }
  verify_pdfjs_assets "$2"
  echo "Validated PDF.js runtime assets."
  exit 0
fi
[[ "$#" == 0 ]] || { echo "usage: $0 [--pdfjs-assets-only <pdfjs-root>]" >&2; exit 64; }

worker="${DEEPSHOVEL_PROJECT_ROOT}/dist/server/index.js"
migrations="${DEEPSHOVEL_PROJECT_ROOT}/dist/runtime/drizzle"
pdfjs="${DEEPSHOVEL_PROJECT_ROOT}/dist/client/pdfjs"

[[ -f "${worker}" ]] || {
  echo "Missing Worker entry: dist/server/index.js" >&2
  exit 66
}
[[ -n "$(find "${migrations}" -maxdepth 1 -type f -name '*.sql' -print -quit 2>/dev/null)" ]] || {
  echo "Missing packaged D1 migrations: dist/runtime/drizzle" >&2
  exit 66
}
verify_pdfjs_assets "${pdfjs}"

node --input-type=module - "${worker}" <<'NODE'
import { pathToFileURL } from "node:url";

const [workerPath] = process.argv.slice(2);

const workerUrl = pathToFileURL(workerPath);
workerUrl.searchParams.set("sites-validation", `${process.pid}-${Date.now()}`);
const worker = await import(workerUrl.href);
if (!worker.default || typeof worker.default.fetch !== "function") {
  throw new Error("dist/server/index.js must have an ESM default export with fetch(request, env, ctx)");
}
NODE

echo "Validated runtime artifact: Worker, D1 migrations, and PDF.js assets are present."
