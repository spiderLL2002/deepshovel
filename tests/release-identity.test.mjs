import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { SOURCE_COMMIT_PLACEHOLDER, releaseStamp, sourceCommit } from "../build/release-stamp-vite-plugin.ts";
import { WEB_RELEASE, WEB_RELEASE_VERSION, WEB_SOURCE_COMMIT } from "../lib/release.ts";

const read = (file) => fs.readFileSync(new URL(`../${file}`, import.meta.url), "utf8");
const projectRoot = fileURLToPath(new URL("..", import.meta.url));

test("Web and Compiler release versions have one value each", () => {
  const packageJson = JSON.parse(read("package.json"));
  const lock = JSON.parse(read("package-lock.json"));
  assert.equal(WEB_RELEASE_VERSION, packageJson.version);
  assert.equal(lock.version, packageJson.version);
  assert.equal(lock.packages[""].version, packageJson.version);
  const pyproject = /^version = "([^"]+)"$/mu.exec(read("compiler/pyproject.toml"))[1];
  const compilerVersion = /^__version__ = "([^"]+)"$/mu.exec(read("compiler/compiler_service/__init__.py"))[1];
  assert.equal(compilerVersion, pyproject);
});

test("outside a build the release reads development; a build stamps the source commit", () => {
  assert.equal(WEB_SOURCE_COMMIT, "development");
  assert.equal(WEB_RELEASE, `${WEB_RELEASE_VERSION}+development`);
  const source = read("lib/release.ts");
  assert.ok(source.includes(JSON.stringify(SOURCE_COMMIT_PLACEHOLDER)));
  const plugin = releaseStamp();
  plugin.buildStart.call({});
  const stamped = plugin.transform(source, "/project/lib/release.ts");
  assert.ok(stamped && !stamped.code.includes(SOURCE_COMMIT_PLACEHOLDER));
  assert.match(stamped.code, /const STAMPED_SOURCE_COMMIT: string = "(?:[0-9a-f]{12}(?:-dirty)?|unknown)";/u);
  assert.equal(plugin.transform(source, "/project/lib/other.ts"), null);
});

test("an update stamps the staged Compiler with the Web build's source commit", () => {
  const installer = read("scripts/install_public_service.sh");
  const start = installer.indexOf('source_commit="unknown"');
  const end = installer.indexOf("\n", installer.indexOf('compiler_service/SOURCE_COMMIT"', start));
  assert.ok(start > 0 && end > start);
  assert.ok(end < installer.indexOf("-m pip install"), "the stamp must precede the Compiler install");
  assert.match(read("compiler/compiler_service/release.py"), /^SOURCE_COMMIT_FILE = "SOURCE_COMMIT"$/mu);
  assert.equal(fs.existsSync(path.join(projectRoot, "compiler/compiler_service/SOURCE_COMMIT")), false);

  const staged = fs.mkdtempSync(path.join(os.tmpdir(), "deepshovel-stamp-"));
  try {
    fs.mkdirSync(path.join(staged, "compiler/compiler_service"), { recursive: true });
    execFileSync("bash", ["-c", `set -Eeuo pipefail\n${installer.slice(start, end)}`], {
      env: { ...process.env, project_root: projectRoot, next_app: staged },
    });
    const stamp = fs.readFileSync(path.join(staged, "compiler/compiler_service/SOURCE_COMMIT"), "utf8");
    assert.equal(stamp, `${sourceCommit()}\n`);
  } finally {
    fs.rmSync(staged, { recursive: true, force: true });
  }
});
