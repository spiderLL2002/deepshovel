#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "${script_dir}/.." && pwd)"
manifest="${script_dir}/public_manifest.py"
verification_root="$(mktemp -d "${TMPDIR:-/tmp}/deepshovel-public-verify.XXXXXX")"
cleanup() {
  case "${verification_root}" in
    "${TMPDIR:-/tmp}"/deepshovel-public-verify.*) rm -rf "${verification_root}" ;;
  esac
}
trap cleanup EXIT

cd "${project_root}"
python3 -m py_compile "${manifest}"
python3 -m py_compile deploy/runtime/apply-local-migrations.py
python3 -m py_compile deploy/runtime/service-lifecycle.py
python3 -m unittest discover -s tests -p 'test_service_lifecycle.py'
python3 -m unittest discover -s tests -p 'test_tunnel_start.py'
python3 -m py_compile deploy/runtime/compile-sync.py scripts/install_compile_sync.py
python3 "${manifest}" validate >/dev/null
bash -n \
  scripts/setup_public_tunnel.sh \
  scripts/install_public_service.sh \
  scripts/install_control_app.sh \
  scripts/public_status.sh \
  deploy/runtime/run-services.sh \
  deploy/runtime/recovery-watchdog.sh \
  deploy/runtime/service-control.sh

install_root="/Users/test/Library/Application Support/DeepShovel"
python3 "${manifest}" generate-runtime --output "${verification_root}/one" --install-root "${install_root}"
python3 "${manifest}" generate-runtime --output "${verification_root}/two" --install-root "${install_root}"
diff -qr "${verification_root}/one" "${verification_root}/two" >/dev/null
plutil -lint "${verification_root}/one/ControlInfo.plist" >/dev/null

python3 "${manifest}" generate-plists \
  --output "${verification_root}/plists" \
  --install-root "${install_root}" \
  --cloudflared /opt/homebrew/bin/cloudflared \
  --control-executable "/Users/test/Applications/DeepShovel Control.app/Contents/MacOS/DeepShovelControl"
for plist in "${verification_root}/plists/"*.plist; do plutil -lint "${plist}" >/dev/null; done

python3 - "${verification_root}/cloudflared.yml" <<'PY'
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("public_manifest", "scripts/public_manifest.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
manifest = module.load_manifest()
manifest["public_url"] = "https://deepshovel.example.com"
module.generate_tunnel(
    manifest,
    Path(sys.argv[1]),
    "00000000-0000-4000-8000-000000000001",
    Path("/tmp/deepshovel-placeholder-credential.json"),
)
PY
grep -Fq "      keepAliveTimeout: 30s" "${verification_root}/cloudflared.yml"
if command -v cloudflared >/dev/null 2>&1; then
  cloudflared tunnel --config "${verification_root}/cloudflared.yml" ingress validate >/dev/null
fi

swiftc \
  -parse-as-library \
  -target "$(uname -m)-apple-macos13.0" \
  control_app/ServiceControl.swift \
  "${verification_root}/one/GeneratedConfig.swift" \
  -framework AppKit \
  -framework SwiftUI \
  -o "${verification_root}/DeepShovelControl"

echo "Public deployment bundle verified."
