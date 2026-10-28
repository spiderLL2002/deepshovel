#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "${script_dir}/.." && pwd)"
manifest="${script_dir}/public_manifest.py"
install_root="${1:?installed runtime root is required}"
app_root="${install_root}/app"
generated="${app_root}/deploy/generated"
display_name="$(python3 "${manifest}" get display_name)"
bundle="${HOME}/Applications/${display_name} Control.app"
# Keep an existing Applications entry pointing at the consolidated project.
# Replace its real bundle during updates, rather than replacing the entry link.
bundle="$(python3 -c 'import sys; from pathlib import Path; print(Path(sys.argv[1]).resolve())' "${bundle}")"
staging="$(mktemp -d "${TMPDIR:-/tmp}/deepshovel-control.XXXXXX")"
trap 'rm -rf "${staging}"' EXIT

mkdir -p "${staging}/Contents/MacOS" "$(dirname "${bundle}")"
swiftc \
  -parse-as-library \
  -target "$(uname -m)-apple-macos13.0" \
  "${project_root}/control_app/ServiceControl.swift" \
  "${generated}/GeneratedConfig.swift" \
  -framework AppKit \
  -framework SwiftUI \
  -o "${staging}/Contents/MacOS/DeepShovelControl"
cp "${generated}/ControlInfo.plist" "${staging}/Contents/Info.plist"
codesign --force --sign - "${staging}" >/dev/null

replacement="${bundle}.new"
rm -rf "${replacement}"
mv "${staging}" "${replacement}"
trap - EXIT
rm -rf "${bundle}"
mv "${replacement}" "${bundle}"
echo "Installed menu-bar controller: ${bundle}"
