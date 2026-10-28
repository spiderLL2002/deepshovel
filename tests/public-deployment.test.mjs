import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";

const root = new URL("../", import.meta.url);
const rootPath = decodeURIComponent(root.pathname);
const manifestPath = join(rootPath, "deploy/local-public.json");
const generator = join(rootPath, "scripts/public_manifest.py");
const artifactValidator = join(rootPath, "scripts/validate-artifact.sh");
const migrationRunner = join(rootPath, "deploy/runtime/apply-local-migrations.py");
const runtimeSwap = join(rootPath, "deploy/runtime/runtime-swap.sh");

test("manual service lifecycle starts and releases idle-sleep prevention", () => {
  const temporary = mkdtempSync(join(tmpdir(), "deepshovel-awake-"));
  try {
    const install = join(temporary, "installed");
    const runtime = join(install, "app/deploy/runtime");
    const generated = join(install, "app/deploy/generated");
    const bin = join(temporary, "bin");
    const state = join(temporary, "state");
    for (const path of [runtime, generated, bin, state]) mkdirSync(path, { recursive: true });
    const script = join(runtime, "service-control.sh");
    writeFileSync(script, readFileSync(join(rootPath, "deploy/runtime/service-control.sh")));
    writeFileSync(join(generated, "deployment.env"), [
      "SERVICE_LABEL=example.service", "TUNNEL_LABEL=example.tunnel", "WATCHDOG_LABEL=example.watchdog",
      "LOCAL_PORT=8100", "COMPILER_PORT=8011", "LOCAL_HEALTH_URL=http://unused", "PUBLIC_HEALTH_URL=http://unused",
    ].join("\n"));
    writeFileSync(join(bin, "python3"), "#!/bin/sh\nexit 0\n", { mode: 0o755 });
    writeFileSync(join(bin, "launchctl"), `#!${process.execPath}
const fs = require('node:fs');
const path = require('node:path');
const [action, ...args] = process.argv.slice(2);
const label = path.basename(args.at(-1)).replace(/\\.plist$/, '');
const file = path.join(process.env.FAKE_STATE, label);
if (action === 'print') {
  if (!fs.existsSync(file)) process.exit(1);
  console.log('state = ' + fs.readFileSync(file, 'utf8'));
} else if (action === 'bootstrap') fs.writeFileSync(file, 'not running');
else if (action === 'kickstart') fs.writeFileSync(file, 'running');
else if (action === 'bootout') fs.rmSync(file, { force: true });
else process.exit(2);
`, { mode: 0o755 });
    const home = join(temporary, "home");
    mkdirSync(join(home, "Library/LaunchAgents"), { recursive: true });
    writeFileSync(join(home, "Library/LaunchAgents/example.compile-sync.plist"), "fixture");
    writeFileSync(join(state, "example.control"), "running");
    const run = action => execFileSync("/bin/bash", [script, action, "--locked"], {
      env: { ...process.env, HOME: home, PATH: `${bin}:${process.env.PATH}`, FAKE_STATE: state },
    });
    const awake = join(state, "example.awake");
    for (const [start, stop] of [["start", "stop"], ["start-service", "stop-service"], ["restart", "stop"]]) {
      run(start);
      assert.equal(readFileSync(awake, "utf8"), "running");
      run(stop);
      assert.equal(existsSync(awake), false);
      assert.equal(readFileSync(join(state, "example.control"), "utf8"), "running");
    }
  } finally {
    rmSync(temporary, { recursive: true, force: true });
  }
});

function generatedDigest(directory) {
  const hash = createHash("sha256");
  for (const name of readdirSync(directory).sort()) {
    hash.update(name);
    hash.update(readFileSync(join(directory, name)));
  }
  return hash.digest("hex");
}

test("public deployment manifest is secret-free and generated runtime is deterministic", () => {
  const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
  assert.equal(manifest.schema_version, 1);
  assert.equal(manifest.local_port, 8100);
  assert.equal(manifest.compiler_port, 8011);
  assert.equal(manifest.health_path, "/api/health");
  assert.equal(manifest.tunnel_protocol, "auto");
  assert.equal(manifest.requires_cloudflare_access, false);
  assert.doesNotMatch(JSON.stringify(manifest), /API_KEY|SHARED_SECRET|credentials-contents/i);
  execFileSync("python3", [generator, "validate"], { cwd: rootPath });

  const temporary = mkdtempSync(join(tmpdir(), "deepshovel-public-test-"));
  try {
    const first = join(temporary, "first");
    const second = join(temporary, "second");
    const installRoot = "/Users/test/Library/Application Support/DeepShovel";
    for (const output of [first, second]) {
      execFileSync("python3", [generator, "generate-runtime", "--output", output, "--install-root", installRoot], { cwd: rootPath });
    }
    assert.equal(generatedDigest(first), generatedDigest(second));

    const wrangler = JSON.parse(readFileSync(join(first, "wrangler.public.json"), "utf8"));
    assert.equal(wrangler.main, "../../dist/server/index.js");
    assert.equal(wrangler.assets.binding, "ASSETS");
    assert.equal(wrangler.assets.run_worker_first, false);
    assert.equal(wrangler.vars.COMPILER_BASE_URL, "http://127.0.0.1:8011");
    assert.equal(wrangler.d1_databases[0].binding, "DB");
    assert.equal(wrangler.r2_buckets[0].binding, "BUCKET");
    assert.doesNotMatch(JSON.stringify(wrangler), /COMPILER_SHARED_SECRET|GPT_API_KEY/);

    const tunnelConfig = join(temporary, "cloudflared.yml");
    execFileSync("python3", [
      generator,
      "generate-tunnel",
      "--output", tunnelConfig,
      "--tunnel-id", "00000000-0000-4000-8000-000000000001",
      "--credentials-file", "/tmp/deepshovel-placeholder-credential.json",
    ], { cwd: rootPath });
    assert.match(readFileSync(tunnelConfig, "utf8"), /^protocol: auto$/m);
    assert.match(readFileSync(tunnelConfig, "utf8"), /^\s+keepAliveTimeout: 30s$/m);
    const tunnelConfigModifiedAt = statSync(tunnelConfig, { bigint: true }).mtimeNs;
    execFileSync("python3", [
      generator,
      "generate-tunnel",
      "--output", tunnelConfig,
      "--tunnel-id", "00000000-0000-4000-8000-000000000001",
      "--credentials-file", "/tmp/deepshovel-placeholder-credential.json",
    ], { cwd: rootPath });
    assert.equal(statSync(tunnelConfig, { bigint: true }).mtimeNs, tunnelConfigModifiedAt);

    const info = readFileSync(join(first, "ControlInfo.plist"), "utf8");
    assert.match(info, /<key>LSUIElement<\/key>\s*<true\/>/);
    assert.match(readFileSync(join(first, "GeneratedConfig.swift"), "utf8"), /refreshSeconds: TimeInterval = 7/);

    const plists = join(temporary, "plists");
    execFileSync("python3", [
      generator,
      "generate-plists",
      "--output", plists,
      "--install-root", installRoot,
      "--cloudflared", "/opt/homebrew/bin/cloudflared",
      "--control-executable", "/Users/test/Applications/DeepShovel Control.app/Contents/MacOS/DeepShovelControl",
    ], { cwd: rootPath });
    const syncPlist = readFileSync(join(plists, "xyz.iiot-shxt.deepshovel.compile-sync.plist"), "utf8");
    assert.match(syncPlist, /deploy\/runtime\/compile-sync\.py/);
    assert.match(syncPlist, /<key>KeepAlive<\/key>\s*<false\/>/);
    const syncConfig = JSON.parse(readFileSync(join(first, "compile-sync.json"), "utf8"));
    assert.equal(syncConfig.web_base_url, "http://127.0.0.1:8100");
    assert.equal(syncConfig.secret_file, `${installRoot}/secrets/web.env`);
    assert.deepEqual(Object.keys(syncConfig).sort(), ["secret_file", "status_file", "web_base_url"]);
    const servicePlist = readFileSync(join(plists, "xyz.iiot-shxt.deepshovel.service.plist"), "utf8");
    const tunnelPlist = readFileSync(join(plists, "xyz.iiot-shxt.deepshovel.tunnel.plist"), "utf8");
    const watchdogPlist = readFileSync(join(plists, "xyz.iiot-shxt.deepshovel.watchdog.plist"), "utf8");
    const controlPlist = readFileSync(join(plists, "xyz.iiot-shxt.deepshovel.control.plist"), "utf8");
    assert.match(servicePlist, /<key>KeepAlive<\/key>\s*<false\/>/);
    assert.match(servicePlist, /<key>ProcessType<\/key>\s*<string>Interactive<\/string>/);
    assert.match(tunnelPlist, /<key>KeepAlive<\/key>\s*<false\/>/);
    assert.match(tunnelPlist, /deploy\/runtime\/start-tunnel\.py/);
    assert.match(tunnelPlist, /<key>ProcessType<\/key>\s*<string>Background<\/string>/);
    assert.match(watchdogPlist, /recovery-watchdog\.sh/);
    assert.match(watchdogPlist, /<key>KeepAlive<\/key>\s*<false\/>/);
    assert.match(watchdogPlist, /<key>ProcessType<\/key>\s*<string>Background<\/string>/);
    assert.match(controlPlist, /<key>KeepAlive<\/key>\s*<false\/>/);
    assert.match(controlPlist, /<key>ProcessType<\/key>\s*<string>Interactive<\/string>/);
    const awakePlist = readFileSync(join(plists, "xyz.iiot-shxt.deepshovel.awake.plist"), "utf8");
    assert.match(awakePlist, /<string>\/usr\/bin\/caffeinate<\/string>/);
    assert.match(awakePlist, /<string>-i<\/string>/);
    assert.match(awakePlist, /<string>-s<\/string>/);
    for (const plist of [servicePlist, tunnelPlist, watchdogPlist, syncPlist, awakePlist]) {
      assert.match(plist, /<key>RunAtLoad<\/key>\s*<false\/>/);
    }
    assert.match(controlPlist, /<key>RunAtLoad<\/key>\s*<true\/>/);
    assert.match(awakePlist, /<key>KeepAlive<\/key>\s*<false\/>/);
  } finally {
    rmSync(temporary, { recursive: true, force: true });
  }
});

test("deployment registry ignores non-dictionary property lists from unrelated apps", () => {
  const temporary = mkdtempSync(join(tmpdir(), "deepshovel-registry-test-"));
  try {
    const launchAgents = join(temporary, "Library", "LaunchAgents");
    const applications = join(temporary, "Applications", "Unrelated.app", "Contents");
    mkdirSync(launchAgents, { recursive: true });
    mkdirSync(applications, { recursive: true });
    const booleanPlist = '<?xml version="1.0" encoding="UTF-8"?>\n' +
      '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n' +
      '<plist version="1.0"><true/></plist>\n';
    writeFileSync(join(launchAgents, "unrelated.plist"), booleanPlist);
    writeFileSync(join(applications, "Info.plist"), booleanPlist);
    execFileSync("python3", [
      generator,
      "registry-check",
      "--install-root", join(temporary, "DeepShovel"),
    ], {
      cwd: rootPath,
      env: { ...process.env, HOME: temporary },
    });
  } finally {
    rmSync(temporary, { recursive: true, force: true });
  }
});

test("deployment registry accepts source and runtime symlinks through CLI and direct calls", () => {
  const temporary = mkdtempSync(join(tmpdir(), "deepshovel-registry-links-"));
  try {
    execFileSync("python3", ["-c", `
import importlib.util, json, pathlib, subprocess, sys
spec = importlib.util.spec_from_file_location("deployment", sys.argv[1])
deployment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deployment)
home = pathlib.Path.home()
runtime = home / "runtime"
runtime.mkdir()
alias = home / "legacy-runtime"
alias.symlink_to(runtime, target_is_directory=True)
source = home / "legacy-source"
source.symlink_to(deployment.ROOT, target_is_directory=True)
manifest = deployment.load_manifest(require_public=True)
entry = deployment.deployment_entry(manifest, alias)
entry["source_manifest"] = str(source / "deploy/local-public.json")
registry = deployment.registry_path()
registry.parent.mkdir(parents=True)
registry.write_text(json.dumps({"schema_version": 1, "projects": {manifest["project_id"]: entry}}))
deployment.registry_check(manifest, runtime)
subprocess.run([sys.executable, sys.argv[1], "registry-check", "--install-root", str(alias)], check=True)
# Direct callers also work when the registry stores the physical runtime path.
entry["install_directory"] = str(runtime)
registry.write_text(json.dumps({"schema_version": 1, "projects": {manifest["project_id"]: entry}}))
deployment.registry_check(manifest, alias)
# First-registration ownership metadata may retain the historical source alias.
registry.unlink()
owner = runtime / "app/deploy/generated/deployment-owner.json"
owner.parent.mkdir(parents=True)
owner.write_text(json.dumps({"project_id": manifest["project_id"], "source_manifest": entry["source_manifest"]}))
deployment.registry_check(manifest, alias)
`, generator], { cwd: rootPath, env: { ...process.env, HOME: temporary } });
  } finally {
    rmSync(temporary, { recursive: true, force: true });
  }
});

test("deployment registry rejects different owners and directories behind symlinks", () => {
  const temporary = mkdtempSync(join(tmpdir(), "deepshovel-registry-conflicts-"));
  try {
    execFileSync("python3", ["-c", `
import importlib.util, json, pathlib, sys
spec = importlib.util.spec_from_file_location("deployment", sys.argv[1])
deployment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deployment)
home = pathlib.Path.home()
runtime = home / "runtime"
runtime.mkdir()
alias = home / "legacy-runtime"
alias.symlink_to(runtime, target_is_directory=True)
manifest = deployment.load_manifest(require_public=True)
entry = deployment.deployment_entry(manifest, runtime)
registry = deployment.registry_path()
registry.parent.mkdir(parents=True)
def rejected(projects, message):
    registry.write_text(json.dumps({"schema_version": 1, "projects": projects}))
    try:
        deployment.registry_check(manifest, alias)
    except SystemExit as error:
        assert message in str(error), str(error)
    else:
        raise AssertionError("Conflicting deployment was accepted")
for field, value in [("project_id", "other-project"), ("source_manifest", str(home / "other.json")), ("install_directory", str(home / "other-runtime"))]:
    rejected({manifest["project_id"]: {**entry, field: value}}, "ownership does not match")
other = {**entry, "project_id": "other-project", "public_hostname": "other.example", "tunnel_name": "other", "bundle_id": "example.other", "ports": [], "launch_agent_labels": [], "install_directory": str(alias)}
rejected({manifest["project_id"]: entry, "other-project": other}, "collision on install_directory")
`, generator], { cwd: rootPath, env: { ...process.env, HOME: temporary } });
  } finally {
    rmSync(temporary, { recursive: true, force: true });
  }
});

test("public deployment shell entrypoints are syntactically valid", () => {
  const scripts = [
    "scripts/setup_public_tunnel.sh",
    "scripts/install_public_service.sh",
    "scripts/install_control_app.sh",
    "scripts/public_status.sh",
    "scripts/validate-artifact.sh",
    "deploy/runtime/run-services.sh",
    "deploy/runtime/recovery-watchdog.sh",
    "deploy/runtime/service-control.sh",
    "deploy/runtime/runtime-swap.sh",
  ];
  execFileSync("bash", ["-n", ...scripts], { cwd: rootPath });
  execFileSync("python3", ["-m", "py_compile", migrationRunner], { cwd: rootPath });

  const installer = readFileSync(join(rootPath, "scripts/install_public_service.sh"), "utf8");
  assert.match(installer, /dist\/client\/assets.*-name '\*\.js'/);
  assert.match(installer, /local_asset_url/);
  assert.match(installer, /public_asset_url/);
  assert.match(installer, /venv\.next/);
  assert.match(installer, /content\.count\(old_prefix\) != 1/);
  assert.match(installer, /DeepShovel entrypoint.*did not contain exactly one staged venv path/);
  assert.match(installer, /trap 'on_install_signal 130' INT/);
  assert.match(installer, /trap 'on_install_signal 143' TERM/);
  assert.match(installer, /trap '' INT TERM HUP/);
  assert.match(installer, /runtime_swap_activate/);
  assert.match(installer, /runtime_swap_commit/);
  assert.match(installer, /Retrieval vector-store lock is still held after service shutdown/);
  assert.ok(installer.indexOf("retrieval_lock=") < installer.indexOf("runtime_swap_activate"));
  assert.match(installer, /port_wait < 180/);
  assert.match(installer, /for _ in \{1\.\.240\}; do/);
  assert.match(installer, /compiler_service\.retrieval_warmup/);
  assert.match(installer, /tunnel_config_needs_reload=0/);
  assert.match(installer, /tunnel_config_modified_epoch > tunnel_process_started_epoch/);
  assert.match(installer, /"\$\{tunnel_config_needs_reload\}" == "1"/);
  assert.ok(
    installer.indexOf("compiler_service.retrieval_warmup") < installer.indexOf('launchctl kickstart "${domain}/${service_label}"'),
  );
  // Agents do not start at load, so every agent the installer loads must also
  // be started explicitly (the compile-sync pump was once left stopped).
  const loaded = /for label in ((?:"\$\{[a-z_]+\}" ?)+); do\n {2}if ! launchctl print/u.exec(installer)[1].match(/[a-z_]+_label/gu);
  assert.ok(loaded.includes("sync_label"));
  for (const label of loaded) {
    assert.match(installer, new RegExp(`launchctl kickstart (?:-k )?"\\$\\{domain\\}/\\$\\{${label}\\}"`, "u"), label);
  }
  const serviceRunner = readFileSync(join(rootPath, "deploy/runtime/run-services.sh"), "utf8");
  assert.match(serviceRunner, /collect_process_tree/);
  assert.match(serviceRunner, /terminate_with_deadline/);
  assert.match(serviceRunner, /kill -KILL "\$\{pids\[@\]\}"/);
  assert.match(serviceRunner, /pgrep -P/);
  assert.match(serviceRunner, /start_web\nstart_compiler/);
  // Behavioral timing/reset coverage lives in test_supervisor_health.py.
  assert.match(serviceRunner, /--max-time 5/);
  assert.match(serviceRunner, /failed_for < 120/);
  assert.match(serviceRunner, /probe_health "http:\/\/127\.0\.0\.1:\$\{COMPILER_PORT\}\/health"/);
  assert.doesNotMatch(serviceRunner, /probe_health [^\n]*\/ready/);
  assert.match(serviceRunner, /retrying in \$\{web_retry_delay\}s/);
  const watchdog = readFileSync(join(rootPath, "deploy/runtime/recovery-watchdog.sh"), "utf8");
  assert.match(watchdog, /--retry 2 --retry-all-errors --retry-delay 1/);
  assert.match(watchdog, /Public health failed three checks while local Web stayed healthy/);
  assert.match(watchdog, /launchctl kickstart -k "\$\{domain\}\/\$\{TUNNEL_LABEL\}"/);
  const publicStatus = readFileSync(join(rootPath, "scripts/public_status.sh"), "utf8");
  assert.match(publicStatus, /--retry 2 --retry-all-errors --retry-delay 1/);
});

test("an installer failure rolls back under the macOS system bash", () => {
  // macOS ships bash 3.2, which has no BASHPID; the error trap once died on
  // it before rolling back. Only the main shell rolls back, never a subshell.
  const installer = readFileSync(join(rootPath, "scripts/install_public_service.sh"), "utf8");
  const handler = /^on_install_error\(\) \{\n[\s\S]*?\n\}\n/mu.exec(installer)[0];
  const result = spawnSync("/bin/bash", ["-c", [
    "set -Eeuo pipefail",
    'rollback_app() { echo "rollback $1" >&2; exit "$1"; }',
    handler,
    "trap on_install_error ERR",
    'value="$(false)"',
  ].join("\n")], { encoding: "utf8" });
  assert.equal(result.status, 1);
  assert.equal(result.stderr, "rollback 1\n");
});

test("runtime swap restores every interrupt-visible app and venv state", () => {
  const phases = ["before", "app-old-moved", "app-active", "venv-old-moved", "active"];
  for (const phase of phases) {
    const temporary = mkdtempSync(join(tmpdir(), `deepshovel-swap-${phase}-`));
    try {
      const script = `
set -Eeuo pipefail
source "$1"
root="$2"
mkdir -p "$root/app" "$root/app.next" "$root/venv" "$root/venv.next"
printf old-app >"$root/app/version"
printf new-app >"$root/app.next/version"
printf old-venv >"$root/venv/version"
printf new-venv >"$root/venv.next/version"
runtime_swap_app="$root/app"
runtime_swap_next_app="$root/app.next"
runtime_swap_rollback_app="$root/app.rollback"
runtime_swap_venv="$root/venv"
runtime_swap_next_venv="$root/venv.next"
runtime_swap_rollback_venv="$root/venv.rollback"
runtime_swap_app_had_previous=1
runtime_swap_venv_had_previous=1
runtime_swap_phase=swapping
case "$3" in
  before) ;;
  app-old-moved) mv "$root/app" "$root/app.rollback" ;;
  app-active) mv "$root/app" "$root/app.rollback"; mv "$root/app.next" "$root/app" ;;
  venv-old-moved) mv "$root/app" "$root/app.rollback"; mv "$root/app.next" "$root/app"; mv "$root/venv" "$root/venv.rollback" ;;
  active) mv "$root/app" "$root/app.rollback"; mv "$root/app.next" "$root/app"; mv "$root/venv" "$root/venv.rollback"; mv "$root/venv.next" "$root/venv"; runtime_swap_phase=active ;;
esac
runtime_swap_restore
test "$(cat "$root/app/version")" = old-app
test "$(cat "$root/venv/version")" = old-venv
test ! -e "$root/app.rollback"
test ! -e "$root/venv.rollback"
`;
      execFileSync("bash", ["-c", script, "fixture", runtimeSwap, temporary, phase], { cwd: rootPath });
    } finally {
      rmSync(temporary, { recursive: true, force: true });
    }
  }
});

test("runtime swap never reports a partial restore as successful", () => {
  const temporary = mkdtempSync(join(tmpdir(), "deepshovel-swap-restore-failure-"));
  try {
    const script = `
set -Eeuo pipefail
source "$1"
root="$2"
mkdir -p "$root/app" "$root/app.rollback" "$root/venv" "$root/venv.rollback"
printf new-app >"$root/app/version"
printf old-app >"$root/app.rollback/version"
printf new-venv >"$root/venv/version"
printf old-venv >"$root/venv.rollback/version"
mkdir -p "$root/app.failed.$$"
runtime_swap_app="$root/app"
runtime_swap_next_app="$root/app.next"
runtime_swap_rollback_app="$root/app.rollback"
runtime_swap_venv="$root/venv"
runtime_swap_next_venv="$root/venv.next"
runtime_swap_rollback_venv="$root/venv.rollback"
runtime_swap_app_had_previous=1
runtime_swap_venv_had_previous=1
runtime_swap_phase=active
if runtime_swap_restore; then exit 91; fi
test "$runtime_swap_phase" = active
test "$(cat "$root/app/version")" = new-app
test "$(cat "$root/venv/version")" = new-venv
test -d "$root/app.rollback"
test -d "$root/venv.rollback"
`;
    execFileSync("bash", ["-c", script, "fixture", runtimeSwap, temporary], { cwd: rootPath });
  } finally {
    rmSync(temporary, { recursive: true, force: true });
  }
});

test("runtime swap propagates an injected active-runtime move failure", () => {
  const temporary = mkdtempSync(join(tmpdir(), "deepshovel-swap-mv-failure-"));
  try {
    const script = `
set -Eeuo pipefail
source "$1"
root="$2"
mkdir -p "$root/app" "$root/app.rollback" "$root/venv" "$root/venv.rollback"
printf new-app >"$root/app/version"
printf old-app >"$root/app.rollback/version"
printf new-venv >"$root/venv/version"
printf old-venv >"$root/venv.rollback/version"
runtime_swap_app="$root/app"
runtime_swap_next_app="$root/app.next"
runtime_swap_rollback_app="$root/app.rollback"
runtime_swap_venv="$root/venv"
runtime_swap_next_venv="$root/venv.next"
runtime_swap_rollback_venv="$root/venv.rollback"
runtime_swap_app_had_previous=1
runtime_swap_venv_had_previous=1
runtime_swap_phase=active
mv() {
  if [[ "$1" == "$root/app" && "$2" == "$root/app.failed.$$" ]]; then
    return 71
  fi
  command mv "$@"
}
if runtime_swap_restore; then exit 91; fi
test "$runtime_swap_phase" = active
test "$(cat "$root/app/version")" = new-app
test "$(cat "$root/venv/version")" = new-venv
test "$(cat "$root/app.rollback/version")" = old-app
test "$(cat "$root/venv.rollback/version")" = old-venv
`;
    execFileSync("bash", ["-c", script, "fixture", runtimeSwap, temporary], { cwd: rootPath });
  } finally {
    rmSync(temporary, { recursive: true, force: true });
  }
});

test("venv restore failure compensates the already restored app", () => {
  const temporary = mkdtempSync(join(tmpdir(), "deepshovel-swap-venv-failure-"));
  try {
    const script = `
set -Eeuo pipefail
source "$1"
root="$2"
mkdir -p "$root/app" "$root/app.rollback" "$root/venv" "$root/venv.rollback"
printf new-app >"$root/app/version"
printf old-app >"$root/app.rollback/version"
printf new-venv >"$root/venv/version"
printf old-venv >"$root/venv.rollback/version"
runtime_swap_app="$root/app"
runtime_swap_next_app="$root/app.next"
runtime_swap_rollback_app="$root/app.rollback"
runtime_swap_venv="$root/venv"
runtime_swap_next_venv="$root/venv.next"
runtime_swap_rollback_venv="$root/venv.rollback"
runtime_swap_app_had_previous=1
runtime_swap_venv_had_previous=1
runtime_swap_phase=active
mv() {
  if [[ "$1" == "$root/venv" && "$2" == "$root/venv.failed.$$" ]]; then
    return 71
  fi
  command mv "$@"
}
if runtime_swap_restore; then exit 91; fi
test "$runtime_swap_phase" = active
test "$(cat "$root/app/version")" = new-app
test "$(cat "$root/venv/version")" = new-venv
test "$(cat "$root/app.rollback/version")" = old-app
test "$(cat "$root/venv.rollback/version")" = old-venv
`;
    execFileSync("bash", ["-c", script, "fixture", runtimeSwap, temporary], { cwd: rootPath });
  } finally {
    rmSync(temporary, { recursive: true, force: true });
  }
});

test("first-install venv restore failure reactivates the displaced app", () => {
  const temporary = mkdtempSync(join(tmpdir(), "deepshovel-swap-first-install-failure-"));
  try {
    const script = `
set -Eeuo pipefail
source "$1"
root="$2"
mkdir -p "$root/app" "$root/venv"
printf new-app >"$root/app/version"
printf new-venv >"$root/venv/version"
runtime_swap_app="$root/app"
runtime_swap_next_app="$root/app.next"
runtime_swap_rollback_app="$root/app.rollback"
runtime_swap_venv="$root/venv"
runtime_swap_next_venv="$root/venv.next"
runtime_swap_rollback_venv="$root/venv.rollback"
runtime_swap_app_had_previous=0
runtime_swap_venv_had_previous=0
runtime_swap_phase=active
mv() {
  if [[ "$1" == "$root/venv" && "$2" == "$root/venv.failed.$$" ]]; then
    return 71
  fi
  command mv "$@"
}
if runtime_swap_restore; then exit 91; fi
test "$runtime_swap_phase" = active
test "$(cat "$root/app/version")" = new-app
test "$(cat "$root/venv/version")" = new-venv
test ! -e "$root/app.failed.$$"
test ! -e "$root/app.rollback"
`;
    execFileSync("bash", ["-c", script, "fixture", runtimeSwap, temporary], { cwd: rootPath });
  } finally {
    rmSync(temporary, { recursive: true, force: true });
  }
});

test("restore fails closed when an activated component lost its rollback", () => {
  const temporary = mkdtempSync(join(tmpdir(), "deepshovel-swap-missing-rollback-"));
  try {
    const script = `
set -Eeuo pipefail
source "$1"
root="$2"
mkdir -p "$root/app" "$root/venv" "$root/venv.rollback"
printf new-app >"$root/app/version"
printf new-venv >"$root/venv/version"
printf old-venv >"$root/venv.rollback/version"
runtime_swap_app="$root/app"
runtime_swap_next_app="$root/app.next"
runtime_swap_rollback_app="$root/app.rollback"
runtime_swap_venv="$root/venv"
runtime_swap_next_venv="$root/venv.next"
runtime_swap_rollback_venv="$root/venv.rollback"
runtime_swap_app_had_previous=1
runtime_swap_venv_had_previous=1
runtime_swap_phase=active
if runtime_swap_restore; then exit 91; fi
test "$runtime_swap_phase" = active
test "$(cat "$root/app/version")" = new-app
test "$(cat "$root/venv/version")" = new-venv
test -d "$root/venv.rollback"
`;
    execFileSync("bash", ["-c", script, "fixture", runtimeSwap, temporary], { cwd: rootPath });
  } finally {
    rmSync(temporary, { recursive: true, force: true });
  }
});

test("restore cleanup failure cannot split the restored app and venv", () => {
  const temporary = mkdtempSync(join(tmpdir(), "deepshovel-swap-cleanup-failure-"));
  try {
    const script = `
set -Eeuo pipefail
source "$1"
root="$2"
mkdir -p "$root/app" "$root/app.rollback" "$root/venv" "$root/venv.rollback"
printf new-app >"$root/app/version"
printf old-app >"$root/app.rollback/version"
printf new-venv >"$root/venv/version"
printf old-venv >"$root/venv.rollback/version"
runtime_swap_app="$root/app"
runtime_swap_next_app="$root/app.next"
runtime_swap_rollback_app="$root/app.rollback"
runtime_swap_venv="$root/venv"
runtime_swap_next_venv="$root/venv.next"
runtime_swap_rollback_venv="$root/venv.rollback"
runtime_swap_app_had_previous=1
runtime_swap_venv_had_previous=1
runtime_swap_phase=active
rm() {
  if [[ "$1" == "-rf" && "$3" == "$root/app.failed.$$" ]]; then
    return 74
  fi
  command rm "$@"
}
runtime_swap_restore
test "$runtime_swap_phase" = restored
test "$(cat "$root/app/version")" = old-app
test "$(cat "$root/venv/version")" = old-venv
test -d "$root/app.failed.$$"
test ! -e "$root/venv.failed.$$"
`;
    execFileSync("bash", ["-c", script, "fixture", runtimeSwap, temporary], { cwd: rootPath });
  } finally {
    rmSync(temporary, { recursive: true, force: true });
  }
});

test("a second termination signal is ignored across two-component restore", () => {
  const temporary = mkdtempSync(join(tmpdir(), "deepshovel-swap-restore-signal-"));
  try {
    const script = `
set -Eeuo pipefail
source "$1"
root="$2"
mkdir -p "$root/app" "$root/app.rollback" "$root/venv" "$root/venv.rollback"
printf new-app >"$root/app/version"
printf old-app >"$root/app.rollback/version"
printf new-venv >"$root/venv/version"
printf old-venv >"$root/venv.rollback/version"
runtime_swap_app="$root/app"
runtime_swap_next_app="$root/app.next"
runtime_swap_rollback_app="$root/app.rollback"
runtime_swap_venv="$root/venv"
runtime_swap_next_venv="$root/venv.next"
runtime_swap_rollback_venv="$root/venv.rollback"
runtime_swap_app_had_previous=1
runtime_swap_venv_had_previous=1
runtime_swap_phase=active
trap '' INT TERM HUP
sent=0
mv() {
  command mv "$@"
  if [[ "$1" == "$root/app.rollback" && "$2" == "$root/app" && "$sent" == 0 ]]; then
    sent=1
    kill -TERM $$
  fi
}
runtime_swap_restore
test "$runtime_swap_phase" = restored
test "$(cat "$root/app/version")" = old-app
test "$(cat "$root/venv/version")" = old-venv
`;
    execFileSync("bash", ["-c", script, "fixture", runtimeSwap, temporary], { cwd: rootPath });
  } finally {
    rmSync(temporary, { recursive: true, force: true });
  }
});

test("runtime swap commits the healthy replacement without a rollback window", () => {
  const temporary = mkdtempSync(join(tmpdir(), "deepshovel-swap-commit-"));
  try {
    const script = `
set -Eeuo pipefail
source "$1"
root="$2"
mkdir -p "$root/app" "$root/app.next" "$root/venv" "$root/venv.next"
printf old-app >"$root/app/version"
printf new-app >"$root/app.next/version"
printf old-venv >"$root/venv/version"
printf new-venv >"$root/venv.next/version"
runtime_swap_activate "$root/app" "$root/app.next" "$root/app.rollback" "$root/venv" "$root/venv.next" "$root/venv.rollback"
runtime_swap_commit
runtime_swap_restore
test "$(cat "$root/app/version")" = new-app
test "$(cat "$root/venv/version")" = new-venv
test ! -e "$root/app.rollback"
test ! -e "$root/venv.rollback"
`;
    execFileSync("bash", ["-c", script, "fixture", runtimeSwap, temporary], { cwd: rootPath });
  } finally {
    rmSync(temporary, { recursive: true, force: true });
  }
});

test("an interrupt during committed cleanup cannot recreate a rollback window", () => {
  const temporary = mkdtempSync(join(tmpdir(), "deepshovel-swap-commit-term-"));
  try {
    const script = `
set -Eeuo pipefail
source "$1"
root="$2"
mkdir -p "$root/app" "$root/app.next" "$root/venv" "$root/venv.next"
printf old-app >"$root/app/version"
printf new-app >"$root/app.next/version"
printf old-venv >"$root/venv/version"
printf new-venv >"$root/venv.next/version"
runtime_swap_activate "$root/app" "$root/app.next" "$root/app.rollback" "$root/venv" "$root/venv.next" "$root/venv.rollback"
trap 'exit 143' TERM
runtime_swap_remove_tree() { kill -TERM $$; }
runtime_swap_commit
exit 92
`;
    const result = spawnSync("bash", ["-c", script, "fixture", runtimeSwap, temporary], {
      cwd: rootPath,
      encoding: "utf8",
    });
    assert.equal(result.status, 143, result.stderr);
    assert.equal(readFileSync(join(temporary, "app/version"), "utf8"), "new-app");
    assert.equal(readFileSync(join(temporary, "venv/version"), "utf8"), "new-venv");
    assert.equal(existsSync(join(temporary, "app.rollback")), false);
    assert.equal(existsSync(join(temporary, "venv.rollback")), false);
    assert.equal(
      readdirSync(temporary).some((name) => name.startsWith("app.rollback.discard.")),
      true,
    );
  } finally {
    rmSync(temporary, { recursive: true, force: true });
  }
});

test("commit keeps a recoverable app path when both venv and reverse moves fail", () => {
  const temporary = mkdtempSync(join(tmpdir(), "deepshovel-swap-commit-failure-"));
  try {
    const script = `
set -Eeuo pipefail
source "$1"
root="$2"
mkdir -p "$root/app" "$root/app.next" "$root/venv" "$root/venv.next"
printf old-app >"$root/app/version"
printf new-app >"$root/app.next/version"
printf old-venv >"$root/venv/version"
printf new-venv >"$root/venv.next/version"
runtime_swap_activate "$root/app" "$root/app.next" "$root/app.rollback" "$root/venv" "$root/venv.next" "$root/venv.rollback"
fixed_app_rollback="$root/app.rollback"
mv() {
  if [[ "$1" == "$root/venv.rollback" && "$2" == "$root/venv.rollback.discard.$$" ]]; then
    return 72
  fi
  if [[ "$1" == "$root/app.rollback.discard.$$" && "$2" == "$fixed_app_rollback" ]]; then
    return 73
  fi
  command mv "$@"
}
if runtime_swap_commit; then exit 92; fi
case "$runtime_swap_rollback_app" in
  "$root/app.rollback.discard."*) ;;
  *) exit 93 ;;
esac
runtime_swap_restore
test "$runtime_swap_phase" = restored
test "$(cat "$root/app/version")" = old-app
test "$(cat "$root/venv/version")" = old-venv
`;
    execFileSync("bash", ["-c", script, "fixture", runtimeSwap, temporary], { cwd: rootPath });
  } finally {
    rmSync(temporary, { recursive: true, force: true });
  }
});

test("TERM during the runtime swap runs restoration and preserves the prior runtime", () => {
  const temporary = mkdtempSync(join(tmpdir(), "deepshovel-swap-term-"));
  try {
    const script = `
set -Eeuo pipefail
source "$1"
root="$2"
mkdir -p "$root/app" "$root/app.next" "$root/venv" "$root/venv.next"
printf old-app >"$root/app/version"
printf new-app >"$root/app.next/version"
printf old-venv >"$root/venv/version"
printf new-venv >"$root/venv.next/version"
trap 'trap - TERM; runtime_swap_restore; exit 143' TERM
runtime_swap_activate "$root/app" "$root/app.next" "$root/app.rollback" "$root/venv" "$root/venv.next" "$root/venv.rollback"
kill -TERM $$
`;
    const result = spawnSync("bash", ["-c", script, "fixture", runtimeSwap, temporary], {
      cwd: rootPath,
      encoding: "utf8",
    });
    assert.equal(result.status, 143, result.stderr);
    assert.equal(readFileSync(join(temporary, "app/version"), "utf8"), "old-app");
    assert.equal(readFileSync(join(temporary, "venv/version"), "utf8"), "old-venv");
    assert.equal(readdirSync(temporary).filter((name) => name.includes("rollback")).length, 0);
  } finally {
    rmSync(temporary, { recursive: true, force: true });
  }
});

test("local migration runner closes a completed Wrangler process that stays open", () => {
  const temporary = mkdtempSync(join(tmpdir(), "deepshovel-migration-runner-"));
  try {
    const migrations = join(temporary, "migrations");
    const persistence = join(temporary, "state");
    mkdirSync(migrations, { recursive: true });
    mkdirSync(persistence, { recursive: true });
    writeFileSync(join(migrations, "0000_fixture.sql"), "CREATE TABLE fixture (id INTEGER PRIMARY KEY);\n");
    const config = join(temporary, "wrangler.json");
    writeFileSync(config, JSON.stringify({
      d1_databases: [{ binding: "DB", migrations_dir: "./migrations" }],
    }));
    const fakeWrangler = join(temporary, "fake-wrangler.py");
    writeFileSync(fakeWrangler, `#!/usr/bin/env python3
import pathlib, signal, sqlite3, sys, time
signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
state = pathlib.Path(sys.argv[sys.argv.index("--persist-to") + 1]) / "v3" / "d1"
state.mkdir(parents=True, exist_ok=True)
with sqlite3.connect(state / "fixture.sqlite") as database:
    database.execute("CREATE TABLE d1_migrations (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    database.execute("INSERT INTO d1_migrations (name) VALUES ('0000_fixture.sql')")
time.sleep(60)
`);
    execFileSync("chmod", ["+x", fakeWrangler]);
    const startedAt = Date.now();
    execFileSync("python3", [
      migrationRunner,
      "--wrangler", fakeWrangler,
      "--config", config,
      "--persist-to", persistence,
      "--timeout-seconds", "10",
    ], { cwd: rootPath, timeout: 15_000 });
    assert.ok(Date.now() - startedAt < 10_000);
  } finally {
    rmSync(temporary, { recursive: true, force: true });
  }
});

test("local migration runner accepts a normally exiting Wrangler process", () => {
  const temporary = mkdtempSync(join(tmpdir(), "deepshovel-migration-exit-"));
  try {
    const migrations = join(temporary, "migrations");
    const persistence = join(temporary, "state");
    mkdirSync(migrations, { recursive: true });
    mkdirSync(persistence, { recursive: true });
    writeFileSync(join(migrations, "0000_fixture.sql"), "SELECT 1;\n");
    const config = join(temporary, "wrangler.json");
    writeFileSync(config, JSON.stringify({
      d1_databases: [{ binding: "DB", migrations_dir: "./migrations" }],
    }));
    const fakeWrangler = join(temporary, "fake-wrangler.py");
    writeFileSync(fakeWrangler, `#!/usr/bin/env python3
import pathlib, sqlite3, sys
state = pathlib.Path(sys.argv[sys.argv.index("--persist-to") + 1]) / "v3" / "d1"
state.mkdir(parents=True, exist_ok=True)
with sqlite3.connect(state / "fixture.sqlite") as database:
    database.execute("CREATE TABLE d1_migrations (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    database.execute("INSERT INTO d1_migrations (name) VALUES ('0000_fixture.sql')")
`);
    execFileSync("chmod", ["+x", fakeWrangler]);
    execFileSync("python3", [
      migrationRunner,
      "--wrangler", fakeWrangler,
      "--config", config,
      "--persist-to", persistence,
      "--timeout-seconds", "10",
    ], { cwd: rootPath, timeout: 15_000 });
  } finally {
    rmSync(temporary, { recursive: true, force: true });
  }
});

test("local migration runner rejects ambiguous D1 databases", () => {
  const temporary = mkdtempSync(join(tmpdir(), "deepshovel-migration-ambiguous-"));
  try {
    const migrations = join(temporary, "migrations");
    const persistence = join(temporary, "state");
    mkdirSync(migrations, { recursive: true });
    mkdirSync(persistence, { recursive: true });
    writeFileSync(join(migrations, "0000_fixture.sql"), "SELECT 1;\n");
    const config = join(temporary, "wrangler.json");
    writeFileSync(config, JSON.stringify({
      d1_databases: [{ binding: "DB", migrations_dir: "./migrations" }],
    }));
    execFileSync("python3", ["-c", `
import pathlib, sqlite3, sys
root = pathlib.Path(sys.argv[1])
for name in ("one.sqlite", "two.sqlite"):
    with sqlite3.connect(root / name) as database:
        database.execute("CREATE TABLE d1_migrations (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        database.execute("INSERT INTO d1_migrations (name) VALUES ('0000_fixture.sql')")
`, persistence]);
    const result = spawnSync("python3", [
      migrationRunner,
      "--wrangler", "/usr/bin/false",
      "--config", config,
      "--persist-to", persistence,
    ], { cwd: rootPath, encoding: "utf8" });
    assert.equal(result.status, 70);
    assert.match(result.stderr, /Multiple local D1 migration databases/);
  } finally {
    rmSync(temporary, { recursive: true, force: true });
  }
});

test("artifact validation requires every PDF.js runtime asset class", () => {
  const temporary = mkdtempSync(join(tmpdir(), "deepshovel-pdfjs-assets-"));
  const required = {
    cmap: ["cmaps/Identity-H.bcmap", "cmap"],
    font: ["standard_fonts/LiberationSans-Regular.ttf", "font"],
    wasm: ["wasm/openjpeg.wasm", "wasm"],
    fallback: ["wasm/openjpeg_nowasm_fallback.js", "fallback"],
  };
  const validate = (directory) => spawnSync(
    "bash",
    [artifactValidator, "--pdfjs-assets-only", directory],
    { cwd: rootPath, env: { ...process.env, DEEPSHOVEL_ENV_READY: "1" }, encoding: "utf8" },
  );

  try {
    for (const omitted of Object.keys(required)) {
      const fixture = join(temporary, `without-${omitted}`);
      for (const [key, [relative]] of Object.entries(required)) {
        if (key === omitted) continue;
        const path = join(fixture, relative);
        mkdirSync(dirname(path), { recursive: true });
        writeFileSync(path, "fixture");
      }
      const result = validate(fixture);
      assert.notEqual(result.status, 0, `validator accepted a bundle without ${required[omitted][1]}`);
    }

    const complete = join(temporary, "complete");
    for (const [relative] of Object.values(required)) {
      const path = join(complete, relative);
      mkdirSync(dirname(path), { recursive: true });
      writeFileSync(path, "fixture");
    }
    const result = validate(complete);
    assert.equal(result.status, 0, result.stderr);
  } finally {
    rmSync(temporary, { recursive: true, force: true });
  }
});

test("Web health is cheap, non-sensitive and non-cacheable", async () => {
  const { GET } = await import("../app/api/health/route.ts");
  const response = await GET();
  assert.equal(response.status, 200);
  // The release identity is public build metadata (version + source commit).
  assert.deepEqual(await response.json(), { status: "ok", service: "deepshovel", release: "1.1.1+development" });
  assert.equal(response.headers.get("cache-control"), "no-store");
});
