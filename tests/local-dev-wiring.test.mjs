import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { chmod, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const root = new URL("../", import.meta.url);

async function source(path) {
  return readFile(new URL(path, root), "utf8");
}

test("local Vite and Compiler share the explicit Wrangler development boundary", async () => {
  const [vite, wrangler, compilerLauncher, ignore, sharedExample, privateExample] = await Promise.all([
    source("vite.config.ts"),
    source("wrangler.local.jsonc"),
    source("compiler/scripts/dev-local.sh"),
    source(".gitignore"),
    source(".dev.vars.example"),
    source("compiler/.env.example"),
  ]);

  assert.match(vite, /command === "serve"[\s\S]*configPath: "\.\/wrangler\.local\.jsonc"/u);
  assert.match(vite, /: \{ config: localBindingConfig \}/u);
  assert.match(wrangler, /"COMPILER_BASE_URL": "http:\/\/127\.0\.0\.1:8011"/u);
  assert.match(wrangler, /"required": \["COMPILER_SHARED_SECRET"\]/u);

  const sharedIndex = compilerLauncher.indexOf('source "${worker_environment_file}"');
  const compilerIndex = compilerLauncher.indexOf('source "${environment_file}"');
  assert.ok(sharedIndex >= 0 && compilerIndex > sharedIndex);
  assert.match(ignore, /^\.dev\.vars$/mu);
  assert.match(ignore, /^\.env\*$/mu);
  assert.match(ignore, /^!\.dev\.vars\.example$/mu);
  assert.match(ignore, /^!\/compiler\/\.env\.example$/mu);
  assert.match(sharedExample, /^COMPILER_SHARED_SECRET=/mu);
  assert.doesNotMatch(sharedExample, /GPT_API_KEY/u);
  assert.match(privateExample, /^GPT_API_KEY=/mu);
  assert.doesNotMatch(privateExample, /COMPILER_SHARED_SECRET=/u);
});

test("Compiler launcher preserves the root shared secret as the single source", async () => {
  const temporary = await mkdtemp(join(tmpdir(), "deepshovel-local-wiring-"));
  const sharedFile = join(temporary, "shared.env");
  const privateFile = join(temporary, "compiler.env");
  const conflictingFile = join(temporary, "conflicting.env");
  const executable = join(temporary, "fake-compiler.sh");
  const sharedSecret = "s".repeat(64);
  const launcher = fileURLToPath(new URL("../compiler/scripts/dev-local.sh", import.meta.url));

  try {
    await Promise.all([
      writeFile(sharedFile, `COMPILER_SHARED_SECRET=${sharedSecret}\n`, { mode: 0o600 }),
      writeFile(privateFile, "GPT_API_KEY=fixed-test-key\nCOMPILER_ENV=production\n", { mode: 0o600 }),
      writeFile(
        conflictingFile,
        `GPT_API_KEY=fixed-test-key\nCOMPILER_SHARED_SECRET=${"x".repeat(64)}\n`,
        { mode: 0o600 },
      ),
      writeFile(
        executable,
        `#!/usr/bin/env bash\n[[ \"\${COMPILER_SHARED_SECRET}\" == \"${sharedSecret}\" ]]\n[[ \"\${GPT_API_KEY}\" == \"fixed-test-key\" ]]\n`,
        { mode: 0o700 },
      ),
    ]);
    await chmod(executable, 0o700);

    const environment = {
      ...process.env,
      COMPILER_SHARED_ENV_FILE: sharedFile,
      COMPILER_ENV_FILE: privateFile,
      COMPILER_EXECUTABLE: executable,
    };
    const accepted = spawnSync("bash", [launcher], { env: environment, encoding: "utf8" });
    assert.equal(accepted.status, 0, accepted.stderr);

    const rejected = spawnSync("bash", [launcher], {
      env: { ...environment, COMPILER_ENV_FILE: conflictingFile },
      encoding: "utf8",
    });
    assert.equal(rejected.status, 78);
    assert.match(rejected.stderr, /must not define COMPILER_SHARED_SECRET/u);
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
});
