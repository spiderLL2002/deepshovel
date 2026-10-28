#!/usr/bin/env bash
set -euo pipefail

compiler_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
worker_environment_file="${COMPILER_SHARED_ENV_FILE:-${compiler_root}/../.dev.vars}"
environment_file="${COMPILER_ENV_FILE:-${compiler_root}/.env.local}"
compiler="${COMPILER_EXECUTABLE:-${compiler_root}/.venv/bin/deepshovel-compiler}"

if [[ ! -f "${worker_environment_file}" ]]; then
  echo "Shared local environment is missing: ${worker_environment_file}" >&2
  echo "Create root .dev.vars with COMPILER_SHARED_SECRET." >&2
  exit 78
fi

if [[ ! -f "${environment_file}" ]]; then
  echo "Compiler environment is missing: ${environment_file}" >&2
  echo "Create compiler/.env.local from .env.example before starting." >&2
  exit 78
fi

if grep -Eq '^[[:space:]]*(export[[:space:]]+)?COMPILER_SHARED_SECRET[[:space:]]*=' "${environment_file}"; then
  echo "Compiler-private environment must not define COMPILER_SHARED_SECRET: ${environment_file}" >&2
  echo "Keep the shared secret only in root .dev.vars." >&2
  exit 78
fi

if [[ ! -x "${compiler}" ]]; then
  echo "Compiler executable is missing: ${compiler}" >&2
  echo "Run: cd compiler && python3.12 -m venv .venv && .venv/bin/python -m pip install -e '.[test]'" >&2
  exit 69
fi

set -a
# shellcheck disable=SC1090
source "${worker_environment_file}"
# shellcheck disable=SC1090
source "${environment_file}"
set +a

cd "${compiler_root}"
exec "${compiler}"
