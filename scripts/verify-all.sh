#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "${script_dir}/.." && pwd)"
compiler_python="${COMPILER_TEST_PYTHON:-${project_root}/compiler/.venv/bin/python}"
tsc="${project_root}/node_modules/.bin/tsc"
verification_state="$(mktemp -d "${TMPDIR:-/tmp}/deepshovel-verify.XXXXXX")"

cleanup() {
  case "${verification_state}" in
    "${TMPDIR:-/tmp}"/deepshovel-verify.*) rm -rf "${verification_state}" ;;
  esac
}
trap cleanup EXIT

[[ -x "${compiler_python}" ]] || {
  echo "Compiler test environment is unavailable: ${compiler_python}" >&2
  echo "Create compiler/.venv and install compiler[test] before verification." >&2
  exit 69
}
[[ -x "${tsc}" ]] || {
  echo "Web dependencies are unavailable. Run npm run install:ci first." >&2
  exit 69
}
command -v sqlite3 >/dev/null || {
  echo "SQLite is required for fresh migration verification." >&2
  exit 69
}

echo "[1/8] Compiler tests"
(cd "${project_root}/compiler" && "${compiler_python}" -m pytest)

echo "[2/8] Compiler static checks"
(cd "${project_root}/compiler" && "${compiler_python}" -m ruff check compiler_service tests)
"${compiler_python}" -m unittest discover -s "${project_root}/tests" -p 'test_*.py'

echo "[3/8] Node contract and route tests"
(cd "${project_root}" && npm run test:node)

echo "[4/8] TypeScript"
(cd "${project_root}" && "${tsc}" --noEmit --incremental false)

echo "[5/8] ESLint"
(cd "${project_root}" && npm run lint)

echo "[6/8] Production build and artifact gate"
(cd "${project_root}" && npm run build)

echo "[7/8] Public deployment bundle"
(cd "${project_root}" && bash scripts/verify_public_bundle.sh)

echo "[8/8] Fresh D1-compatible SQLite migration chain"
database_file="${verification_state}/fresh.sqlite3"
migration_count=0
for migration in "${project_root}"/drizzle/*.sql; do
  [[ -f "${migration}" ]] || continue
  migration_count=$((migration_count + 1))
  echo "Applying $(basename "${migration}")"
  sqlite3 -bail -cmd 'PRAGMA foreign_keys=ON;' "${database_file}" < "${migration}"
done
[[ "${migration_count}" -gt 0 && -f "${database_file}" ]] || {
  echo "No D1 migrations were applied to the fresh verification database." >&2
  exit 70
}
integrity="$(sqlite3 "file:${database_file}?mode=ro&immutable=1" \
  'PRAGMA foreign_key_check; PRAGMA integrity_check;')"
[[ "${integrity}" == "ok" ]] || {
  echo "Fresh D1 integrity verification failed:" >&2
  echo "${integrity}" >&2
  exit 70
}

"${compiler_python}" - "${database_file}" "${project_root}/db/schema.ts" <<'PY'
import re
import sqlite3
import sys
from pathlib import Path

with sqlite3.connect(Path(sys.argv[1]).as_uri() + '?mode=ro', uri=True) as db:
    actual = {row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )}
    # Explicitly verify the one registered contentless FTS5 table and its actual shadows.
    fts_sql = db.execute("SELECT sql FROM sqlite_master WHERE name='library_name_search'").fetchone()
    if not fts_sql or 'fts5' not in fts_sql[0].lower() or 'contentless_delete=1' not in fts_sql[0]:
        raise SystemExit('Missing registered library contentless FTS5 index')
    shadow_tables = {row[1] for row in db.execute('PRAGMA table_list') if row[2] == 'shadow'}
    expected_shadows = {'library_name_search_data', 'library_name_search_idx', 'library_name_search_config', 'library_name_search_docsize'}
    if shadow_tables != expected_shadows:
        raise SystemExit(f'Unexpected FTS shadow tables: {shadow_tables ^ expected_shadows}')
    actual -= expected_shadows
expected = set(re.findall(r'sqliteTable\("([^\"]+)"', Path(sys.argv[2]).read_text()))
if actual != expected:
    raise SystemExit(f'Schema drift: extra={actual - expected}, missing={expected - actual}')
print(f'Fresh database matches all {len(actual)} current application tables.')
PY

echo "DeepShovel offline verification passed."
