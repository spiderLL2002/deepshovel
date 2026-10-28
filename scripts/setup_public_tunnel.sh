#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
manifest="${script_dir}/public_manifest.py"
install_root="${1:?installed runtime root is required}"

python3 "${manifest}" validate --require-public >/dev/null

if ! command -v cloudflared >/dev/null 2>&1; then
  if ! command -v brew >/dev/null 2>&1; then
    echo "cloudflared is missing and Homebrew is unavailable." >&2
    exit 69
  fi
  brew install cloudflared
fi
command -v dig >/dev/null 2>&1 || { echo "dig is required to verify the exact DNS target." >&2; exit 69; }

public_url="$(python3 "${manifest}" get public_url)"
hostname="${public_url#https://}"
hostname="${hostname%/}"
dns_zone="${hostname}"
nameservers=""
while [[ "${dns_zone}" == *.* ]]; do
  nameservers="$(dig +short NS "${dns_zone}" 2>/dev/null | tr '[:upper:]' '[:lower:]' || true)"
  [[ -z "${nameservers}" ]] || break
  dns_zone="${dns_zone#*.}"
done
if [[ -z "${nameservers}" ]] || ! grep -Eq '(^|[.])ns[.]cloudflare[.]com[.]?$' <<<"${nameservers}"; then
  echo "${hostname} is not an active Cloudflare-managed DNS zone; register and attach it before installation." >&2
  exit 69
fi

if [[ ! -f "${HOME}/.cloudflared/cert.pem" ]]; then
  echo "Cloudflare authorization is required once; a browser window will open."
  cloudflared tunnel login
fi

tunnel_name="$(python3 "${manifest}" get tunnel_name)"

tunnel_list="$(mktemp "${TMPDIR:-/tmp}/deepshovel-tunnels.XXXXXX")"
trap 'rm -f "${tunnel_list}"' EXIT
cloudflared tunnel list --output json >"${tunnel_list}"
tunnel_id="$(python3 - "${tunnel_list}" "${tunnel_name}" <<'PY'
import json, sys
items = json.load(open(sys.argv[1], encoding="utf-8"))
matches = [item["id"] for item in items if item.get("name") == sys.argv[2] and item.get("deleted_at", "0001").startswith("0001")]
if len(matches) > 1:
    raise SystemExit("multiple active tunnels use the configured name")
print(matches[0] if matches else "")
PY
)"

if [[ -z "${tunnel_id}" ]]; then
  cloudflared tunnel create "${tunnel_name}"
  cloudflared tunnel list --output json >"${tunnel_list}"
  tunnel_id="$(python3 - "${tunnel_list}" "${tunnel_name}" <<'PY'
import json, sys
items = json.load(open(sys.argv[1], encoding="utf-8"))
matches = [item["id"] for item in items if item.get("name") == sys.argv[2] and item.get("deleted_at", "0001").startswith("0001")]
if len(matches) != 1:
    raise SystemExit("new Tunnel identity could not be resolved uniquely")
print(matches[0])
PY
)"
fi

credentials="${HOME}/.cloudflared/${tunnel_id}.json"
if [[ ! -f "${credentials}" ]]; then
  echo "Tunnel credentials are missing: ${credentials}" >&2
  exit 78
fi

mkdir -p "${install_root}/cloudflared"
installed_tunnel_id="${install_root}/cloudflared/tunnel-id"
if [[ -f "${installed_tunnel_id}" ]]; then
  recorded_id="$(tr -d '\r\n' <"${installed_tunnel_id}")"
  if [[ "${recorded_id}" != "${tunnel_id}" ]]; then
    echo "Installed Tunnel identity differs from the configured named Tunnel; refusing to replace it." >&2
    exit 73
  fi
else
  existing_cname="$(dig +short CNAME "${hostname}" 2>/dev/null | tail -1 | tr -d '\r' || true)"
  expected_cname="${tunnel_id}.cfargotunnel.com."
  if [[ -n "${existing_cname}" && "${existing_cname}" != "${expected_cname}" ]]; then
    echo "DNS hostname already points elsewhere; refusing to overwrite ${hostname}." >&2
    exit 73
  fi
  if [[ -z "${existing_cname}" ]]; then
    cloudflared tunnel route dns "${tunnel_id}" "${hostname}"
  fi
  printf '%s\n' "${tunnel_id}" >"${installed_tunnel_id}"
fi

installed_credentials="${install_root}/cloudflared/credentials.json"
install -m 600 "${credentials}" "${installed_credentials}"
python3 "${manifest}" generate-tunnel \
  --output "${install_root}/cloudflared/config.yml" \
  --tunnel-id "${tunnel_id}" \
  --credentials-file "${installed_credentials}"
chmod 600 "${install_root}/cloudflared/config.yml" "${installed_tunnel_id}" "${installed_credentials}"
echo "Named Tunnel ${tunnel_name} is routed to ${hostname}."
