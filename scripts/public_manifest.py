#!/usr/bin/env python3
"""Validate the deployment manifest and generate runtime-only configuration."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import shlex
import shutil
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "deploy" / "local-public.json"
IDENTIFIER = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
REVERSE_DNS = re.compile(r"^[A-Za-z][A-Za-z0-9-]*(\.[A-Za-z][A-Za-z0-9-]*){2,}$")
SAFE_RELATIVE = re.compile(r"^[A-Za-z0-9._/-]+$")
SAFE_DENY = re.compile(r"^/[A-Za-z0-9._/-]*$")

REQUIRED_KEYS = {
    "schema_version",
    "project_id",
    "app_slug",
    "display_name",
    "dns_zone",
    "subdomain",
    "bundle_id",
    "service_namespace",
    "local_port",
    "compiler_port",
    "health_path",
    "compiler_health_path",
    "public_url",
    "install_directory",
    "tunnel_name",
    "tunnel_protocol",
    "build_command",
    "start_command",
    "runtime_paths",
    "persistent_paths",
    "public_deny_paths",
    "requires_cloudflare_access",
}


def load_manifest(*, require_public: bool = False) -> dict[str, Any]:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != REQUIRED_KEYS:
        raise SystemExit("deployment manifest keys do not match schema_version 1")
    if payload["schema_version"] != 1:
        raise SystemExit("deployment manifest schema_version must be 1")
    for key in ("project_id", "app_slug", "subdomain", "tunnel_name"):
        if not isinstance(payload[key], str) or not IDENTIFIER.fullmatch(payload[key]):
            raise SystemExit(f"{key} is unsafe")
    if payload["tunnel_protocol"] not in {"auto", "quic", "http2"}:
        raise SystemExit("tunnel_protocol must be auto, quic, or http2")
    if payload["dns_zone"] != "iiot-shxt.xyz":
        raise SystemExit("dns_zone must use the existing iiot-shxt.xyz zone")
    for key in ("bundle_id", "service_namespace"):
        if not isinstance(payload[key], str) or not REVERSE_DNS.fullmatch(payload[key]):
            raise SystemExit(f"{key} must be a reverse-DNS identifier")
    for key in ("display_name", "install_directory", "build_command"):
        if (
            not isinstance(payload[key], str)
            or not payload[key].strip()
            or len(payload[key]) > 120
        ):
            raise SystemExit(f"{key} is invalid")
    if "/" in payload["install_directory"] or payload["install_directory"] in {
        ".",
        "..",
    }:
        raise SystemExit("install_directory must be one path component")
    for key in ("local_port", "compiler_port"):
        if type(payload[key]) is not int or not 1024 <= payload[key] <= 65535:
            raise SystemExit(f"{key} must be an unprivileged TCP port")
    if payload["local_port"] == payload["compiler_port"]:
        raise SystemExit("Web and Compiler ports must differ")
    for key in ("health_path", "compiler_health_path"):
        if not isinstance(payload[key], str) or not SAFE_DENY.fullmatch(payload[key]):
            raise SystemExit(f"{key} must be a safe absolute path")
    parsed = urlsplit(payload["public_url"])
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise SystemExit("public_url must be an origin-only HTTPS URL")
    if require_public and parsed.hostname.endswith(".invalid"):
        raise SystemExit(
            "public_url is not configured; edit deploy/local-public.json with the final Cloudflare hostname"
        )
    if parsed.hostname != f"{payload['subdomain']}.{payload['dns_zone']}":
        raise SystemExit("public_url must exactly match subdomain.dns_zone")
    for key in (
        "start_command",
        "runtime_paths",
        "persistent_paths",
        "public_deny_paths",
    ):
        if not isinstance(payload[key], list) or not payload[key]:
            raise SystemExit(f"{key} must be a non-empty array")
    for key in ("start_command", "runtime_paths", "persistent_paths"):
        for value in payload[key]:
            if (
                not isinstance(value, str)
                or not SAFE_RELATIVE.fullmatch(value)
                or value.startswith(("/", "../"))
                or "/../" in value
            ):
                raise SystemExit(f"unsafe path in {key}")
    for value in payload["public_deny_paths"]:
        if not isinstance(value, str) or not SAFE_DENY.fullmatch(value) or value == "/":
            raise SystemExit("unsafe public deny path")
    if type(payload["requires_cloudflare_access"]) is not bool:
        raise SystemExit("requires_cloudflare_access must be boolean")
    return payload


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def swift_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def generate_runtime(
    manifest: dict[str, Any], output: Path, install_root: Path
) -> None:
    app_root = install_root / "app"
    local_health_url = (
        f"http://127.0.0.1:{manifest['local_port']}{manifest['health_path']}"
    )
    compiler_health_url = f"http://127.0.0.1:{manifest['compiler_port']}{manifest['compiler_health_path']}"
    public_health_url = manifest["public_url"].rstrip("/") + manifest["health_path"]
    control_script = str(app_root / manifest["start_command"][0]).replace(
        "run-services.sh", "service-control.sh"
    )
    wrangler = {
        "$schema": str(app_root / "node_modules" / "wrangler" / "config-schema.json"),
        "name": f"{manifest['app_slug']}-local-public",
        "main": "../../dist/server/index.js",
        "compatibility_date": "2026-05-22",
        "compatibility_flags": ["nodejs_compat"],
        "assets": {
            "directory": "../../dist/client",
            "binding": "ASSETS",
            "run_worker_first": False,
        },
        "vars": {"COMPILER_BASE_URL": f"http://127.0.0.1:{manifest['compiler_port']}"},
        "d1_databases": [
            {
                "binding": "DB",
                "database_name": f"{manifest['app_slug']}-local",
                "database_id": "00000000-0000-4000-8000-000000000000",
                "migrations_dir": "../../dist/runtime/drizzle",
            }
        ],
        "r2_buckets": [
            {"binding": "BUCKET", "bucket_name": f"{manifest['app_slug']}-local"}
        ],
    }
    write_json(output / "wrangler.public.json", wrangler)
    # The work pump only calls the loopback Web with the shared credential; it
    # never opens the Web or Compiler databases.
    write_json(output / "compile-sync.json", {
        "web_base_url": f"http://127.0.0.1:{manifest['local_port']}",
        "secret_file": str(install_root / "secrets/web.env"),
        "status_file": str(install_root / "data/compile-sync/status.json"),
    })
    constants = "\n".join(
        [
            "import Foundation",
            "",
            "enum DeploymentConfig {",
            f"    static let displayName = {swift_string(manifest['display_name'])}",
            f"    static let serviceLabel = {swift_string(manifest['service_namespace'] + '.service')}",
            f"    static let tunnelLabel = {swift_string(manifest['service_namespace'] + '.tunnel')}",
            f"    static let watchdogLabel = {swift_string(manifest['service_namespace'] + '.watchdog')}",
            f"    static let localHealthURL = URL(string: {swift_string(local_health_url)})!",
            f"    static let compilerHealthURL = URL(string: {swift_string(compiler_health_url)})!",
            f"    static let publicHealthURL = URL(string: {swift_string(public_health_url)})!",
            f"    static let publicURL = URL(string: {swift_string(manifest['public_url'])})!",
            f"    static let localPort = {manifest['local_port']}",
            f"    static let compilerPort = {manifest['compiler_port']}",
            f"    static let tunnelName = {swift_string(manifest['tunnel_name'])}",
            f"    static let awakeLabel = {swift_string(manifest['service_namespace'] + '.awake')}",
            f"    static let installRoot = {swift_string(str(install_root))}",
            f"    static let controlScript = {swift_string(control_script)}",
            "    static let refreshSeconds: TimeInterval = 7",
            "}",
            "",
        ]
    )
    (output / "GeneratedConfig.swift").write_text(constants, encoding="utf-8")
    with (output / "ControlInfo.plist").open("wb") as target:
        plistlib.dump(
            {
                "CFBundleDevelopmentRegion": "zh_CN",
                "CFBundleDisplayName": f"{manifest['display_name']} Control",
                "CFBundleExecutable": "DeepShovelControl",
                "CFBundleIdentifier": manifest["bundle_id"],
                "CFBundleInfoDictionaryVersion": "6.0",
                "CFBundleName": f"{manifest['display_name']} Control",
                "CFBundlePackageType": "APPL",
                "CFBundleShortVersionString": "1.0.0",
                "CFBundleVersion": "1",
                "LSMinimumSystemVersion": "13.0",
                "LSUIElement": True,
                "NSHighResolutionCapable": True,
                "NSPrincipalClass": "NSApplication",
            },
            target,
            sort_keys=True,
        )
    environment = {
        "SERVICE_LABEL": manifest["service_namespace"] + ".service",
        "TUNNEL_LABEL": manifest["service_namespace"] + ".tunnel",
        "WATCHDOG_LABEL": manifest["service_namespace"] + ".watchdog",
        "CONTROL_LABEL": manifest["service_namespace"] + ".control",
        "AWAKE_LABEL": manifest["service_namespace"] + ".awake",
        "SYNC_LABEL": manifest["service_namespace"] + ".compile-sync",
        "LOCAL_PORT": str(manifest["local_port"]),
        "COMPILER_PORT": str(manifest["compiler_port"]),
        "LOCAL_HEALTH_URL": local_health_url,
        "PUBLIC_URL": manifest["public_url"],
        "PUBLIC_HEALTH_URL": public_health_url,
        "INSTALL_ROOT": str(install_root),
        "NODE_EXECUTABLE": shutil.which("node") or "node",
    }
    (output / "deployment.env").write_text(
        "".join(f"{key}={shlex.quote(value)}\n" for key, value in environment.items()),
        encoding="utf-8",
    )
    write_json(
        output / "deployment-owner.json",
        {
            "schema_version": 1,
            "project_id": manifest["project_id"],
            "source_manifest": str(MANIFEST_PATH.resolve()),
        },
    )


def registry_path() -> Path:
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "Local Public Apps"
        / "registry.json"
    )


def deployment_entry(
    manifest: dict[str, Any], install_root: Path, tunnel_id: str | None = None
) -> dict[str, Any]:
    namespace = manifest["service_namespace"]
    return {
        "project_id": manifest["project_id"],
        "source_manifest": str(MANIFEST_PATH.resolve()),
        "public_hostname": urlsplit(manifest["public_url"]).hostname,
        "ports": sorted([manifest["local_port"], manifest["compiler_port"]]),
        "tunnel_name": manifest["tunnel_name"],
        "tunnel_id": tunnel_id,
        "launch_agent_labels": sorted(
            [
                f"{namespace}.service",
                f"{namespace}.tunnel",
                f"{namespace}.watchdog",
                f"{namespace}.control",
                f"{namespace}.awake",
                f"{namespace}.compile-sync",
            ]
        ),
        "bundle_id": manifest["bundle_id"],
        "install_directory": str(install_root),
    }


def load_registry() -> dict[str, Any]:
    path = registry_path()
    if not path.exists():
        return {"schema_version": 1, "projects": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(
            f"shared deployment registry is unreadable: {error}"
        ) from error
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "projects"}
        or payload["schema_version"] != 1
        or not isinstance(payload["projects"], dict)
    ):
        raise SystemExit("shared deployment registry has an unsupported shape")
    return payload


def registry_check(manifest: dict[str, Any], install_root: Path) -> None:
    def same_path(left: Any, right: Any) -> bool:
        if not isinstance(left, str) or not left or not isinstance(right, str) or not right:
            return False
        try:
            return Path(left).expanduser().resolve() == Path(right).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            return False

    incoming = deployment_entry(manifest, install_root)
    registry = load_registry()
    projects = registry["projects"]
    current = projects.get(manifest["project_id"])
    if current is not None and (
        current.get("project_id") != incoming["project_id"]
        or not same_path(current.get("source_manifest"), incoming["source_manifest"])
        or not same_path(current.get("install_directory"), incoming["install_directory"])
    ):
        raise SystemExit(
            "project registry ownership does not match this source manifest"
        )

    unique_keys = ("public_hostname", "tunnel_name", "bundle_id", "install_directory")
    for project_id, entry in projects.items():
        if project_id == manifest["project_id"] or not isinstance(entry, dict):
            continue
        for key in unique_keys:
            if (
                same_path(entry.get(key), incoming[key])
                if key == "install_directory"
                else entry.get(key) == incoming[key]
            ):
                raise SystemExit(f"deployment registry collision on {key}")
        if set(entry.get("ports", [])) & set(incoming["ports"]):
            raise SystemExit("deployment registry port collision")
        if set(entry.get("launch_agent_labels", [])) & set(
            incoming["launch_agent_labels"]
        ):
            raise SystemExit("deployment registry LaunchAgent collision")

    owner_path = install_root / "app" / "deploy" / "generated" / "deployment-owner.json"
    owner_matches = current is not None
    if install_root.exists() and current is None:
        try:
            owner = json.loads(owner_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SystemExit(
                "install directory exists without matching project ownership"
            ) from error
        if (
            owner.get("project_id") != manifest["project_id"]
            or not same_path(owner.get("source_manifest"), incoming["source_manifest"])
        ):
            raise SystemExit("install directory belongs to another source manifest")
        owner_matches = True

    agents = Path.home() / "Library" / "LaunchAgents"
    desired_labels = set(incoming["launch_agent_labels"])
    for plist_path in agents.glob("*.plist") if agents.exists() else []:
        try:
            with plist_path.open("rb") as source:
                plist = plistlib.load(source)
        except (OSError, plistlib.InvalidFileException):
            continue
        if not isinstance(plist, dict):
            continue
        label = plist.get("Label")
        if label in desired_labels and not owner_matches:
            raise SystemExit(
                f"LaunchAgent label already exists without registry ownership: {label}"
            )
        if label in desired_labels:
            continue
        values = [str(value) for value in plist.get("ProgramArguments", [])]
        values.extend(
            str(value) for value in plist.get("EnvironmentVariables", {}).values()
        )
        if any(str(port) in values for port in incoming["ports"]):
            raise SystemExit(
                f"LaunchAgent {label or plist_path.name} already reserves a requested port"
            )

    applications = Path.home() / "Applications"
    expected_app = applications / f"{manifest['display_name']} Control.app"
    for info_path in (
        applications.glob("*.app/Contents/Info.plist") if applications.exists() else []
    ):
        try:
            with info_path.open("rb") as source:
                info = plistlib.load(source)
        except (OSError, plistlib.InvalidFileException):
            continue
        if not isinstance(info, dict):
            continue
        if (
            info.get("CFBundleIdentifier") == manifest["bundle_id"]
            and expected_app not in info_path.parents
        ):
            raise SystemExit(
                "menu-bar bundle identifier is already owned by another application"
            )


def registry_write(
    manifest: dict[str, Any], install_root: Path, tunnel_id: str
) -> None:
    registry_check(manifest, install_root)
    registry = load_registry()
    registry["projects"][manifest["project_id"]] = deployment_entry(
        manifest, install_root, tunnel_id
    )
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="registry.", suffix=".json", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            json.dump(registry, target, ensure_ascii=False, indent=2, sort_keys=True)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def generate_tunnel(
    manifest: dict[str, Any], output: Path, tunnel_id: str, credentials: Path
) -> None:
    if not re.fullmatch(r"[0-9a-f-]{36}", tunnel_id):
        raise SystemExit("invalid Tunnel UUID")
    hostname = urlsplit(manifest["public_url"]).hostname
    lines = [
        f"tunnel: {tunnel_id}",
        f"credentials-file: {json.dumps(str(credentials))}",
        f"protocol: {manifest['tunnel_protocol']}",
        "no-autoupdate: true",
        "ingress:",
    ]
    for denied in manifest["public_deny_paths"]:
        path = denied.rstrip("/")
        lines.extend(
            [
                f"  - hostname: {hostname}",
                f"    path: ^{re.escape(path)}(/.*)?$",
                "    service: http_status:404",
            ]
        )
    lines.extend(
        [
            f"  - hostname: {hostname}",
            f"    service: http://127.0.0.1:{manifest['local_port']}",
            "    originRequest:",
            "      keepAliveTimeout: 30s",
            "  - service: http_status:404",
            "",
        ]
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(lines)
    if not output.exists() or output.read_text(encoding="utf-8") != content:
        output.write_text(content, encoding="utf-8")


def generate_plists(
    manifest: dict[str, Any],
    output: Path,
    install_root: Path,
    cloudflared: Path,
    control_executable: Path,
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    logs = install_root / "logs"
    app_root = install_root / "app"
    service_label = manifest["service_namespace"] + ".service"
    tunnel_label = manifest["service_namespace"] + ".tunnel"
    watchdog_label = manifest["service_namespace"] + ".watchdog"
    control_label = manifest["service_namespace"] + ".control"
    awake_label = manifest["service_namespace"] + ".awake"
    sync_label = manifest["service_namespace"] + ".compile-sync"
    # Login shows the controller only; explicit Start/Stop owns all services.
    common = {"RunAtLoad": False, "ProcessType": "Background"}
    plists = {
        service_label: {
            **common,
            "Label": service_label,
            "ProcessType": "Interactive",
            "ProgramArguments": [
                str(app_root / manifest["start_command"][0]),
                str(install_root),
            ],
            "WorkingDirectory": str(app_root),
            "KeepAlive": False,
            "ExitTimeOut": 20,
            "ThrottleInterval": 10,
            "StandardOutPath": str(logs / "service.stdout.log"),
            "StandardErrorPath": str(logs / "service.stderr.log"),
        },
        tunnel_label: {
            **common,
            "Label": tunnel_label,
            "ProgramArguments": [
                "/usr/bin/python3",
                str(app_root / "deploy/runtime/start-tunnel.py"),
                str(cloudflared),
                str(install_root / "cloudflared" / "config.yml"),
            ],
            "KeepAlive": False,
            "ThrottleInterval": 10,
            "StandardOutPath": str(logs / "tunnel.stdout.log"),
            "StandardErrorPath": str(logs / "tunnel.stderr.log"),
        },
        watchdog_label: {
            **common,
            "Label": watchdog_label,
            "ProgramArguments": [
                str(app_root / "deploy/runtime/recovery-watchdog.sh"),
                str(install_root),
            ],
            "KeepAlive": False,
            "ThrottleInterval": 10,
            "StandardOutPath": str(logs / "watchdog.stdout.log"),
            "StandardErrorPath": str(logs / "watchdog.stderr.log"),
        },
        control_label: {
            "Label": control_label,
            "ProgramArguments": [str(control_executable)],
            "RunAtLoad": True,
            "KeepAlive": False,
            "ProcessType": "Interactive",
            "ThrottleInterval": 5,
            "StandardOutPath": str(logs / "control.stdout.log"),
            "StandardErrorPath": str(logs / "control.stderr.log"),
        },
        sync_label: {
            **common,
            "Label": sync_label,
            "ProgramArguments": [
                "/usr/bin/python3",
                str(app_root / "deploy/runtime/compile-sync.py"),
                str(app_root / "deploy/generated/compile-sync.json"),
            ],
            "KeepAlive": False,
            "ThrottleInterval": 10,
            "StandardOutPath": str(logs / "compile-sync.stdout.log"),
            "StandardErrorPath": str(logs / "compile-sync.stderr.log"),
        },
        awake_label: {
            "Label": awake_label,
            "ProgramArguments": ["/usr/bin/caffeinate", "-i", "-s"],
            "RunAtLoad": False,
            "KeepAlive": False,
            "ProcessType": "Background",
        },
    }
    for label, payload in plists.items():
        with (output / f"{label}.plist").open("wb") as target:
            plistlib.dump(payload, target, sort_keys=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--require-public", action="store_true")
    get = subparsers.add_parser("get")
    get.add_argument("key")
    runtime = subparsers.add_parser("generate-runtime")
    runtime.add_argument("--output", type=Path, required=True)
    runtime.add_argument("--install-root", type=Path, required=True)
    tunnel = subparsers.add_parser("generate-tunnel")
    tunnel.add_argument("--output", type=Path, required=True)
    tunnel.add_argument("--tunnel-id", required=True)
    tunnel.add_argument("--credentials-file", type=Path, required=True)
    plists = subparsers.add_parser("generate-plists")
    plists.add_argument("--output", type=Path, required=True)
    plists.add_argument("--install-root", type=Path, required=True)
    plists.add_argument("--cloudflared", type=Path, required=True)
    plists.add_argument("--control-executable", type=Path, required=True)
    registry_check_parser = subparsers.add_parser("registry-check")
    registry_check_parser.add_argument("--install-root", type=Path, required=True)
    registry_write_parser = subparsers.add_parser("registry-write")
    registry_write_parser.add_argument("--install-root", type=Path, required=True)
    registry_write_parser.add_argument("--tunnel-id", required=True)
    args = parser.parse_args()
    manifest = load_manifest(
        require_public=getattr(args, "require_public", False)
        or args.command in {"generate-tunnel"}
    )
    if args.command == "validate":
        print("deployment manifest is valid")
    elif args.command == "get":
        if args.key not in manifest:
            raise SystemExit("unknown manifest key")
        value = manifest[args.key]
        if isinstance(value, list):
            print("\n".join(str(item) for item in value))
        elif isinstance(value, bool):
            print("true" if value else "false")
        else:
            print(value)
    elif args.command == "generate-runtime":
        generate_runtime(
            manifest, args.output, args.install_root.expanduser().resolve()
        )
    elif args.command == "generate-tunnel":
        generate_tunnel(
            manifest,
            args.output,
            args.tunnel_id,
            args.credentials_file.expanduser().resolve(),
        )
    elif args.command == "generate-plists":
        generate_plists(
            manifest,
            args.output,
            args.install_root.expanduser().resolve(),
            args.cloudflared.resolve(),
            args.control_executable.expanduser().resolve(),
        )
    elif args.command == "registry-check":
        registry_check(manifest, args.install_root.expanduser().resolve())
        print("shared deployment registry is available")
    elif args.command == "registry-write":
        if not re.fullmatch(r"[0-9a-f-]{36}", args.tunnel_id):
            raise SystemExit("invalid Tunnel UUID")
        registry_write(
            manifest, args.install_root.expanduser().resolve(), args.tunnel_id
        )
        print(str(registry_path()))


if __name__ == "__main__":
    main()
