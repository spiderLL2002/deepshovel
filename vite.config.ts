import vinext from "vinext";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import { releaseStamp } from "./build/release-stamp-vite-plugin";
import { runtimeAssets } from "./build/runtime-assets-vite-plugin";

const LOCAL_DATABASE_ID =
  "00000000-0000-4000-8000-000000000000";

// macOS Seatbelt blocks FSEvents, so Codex previews need polling for HMR.
const isCodexSeatbeltSandbox = process.env.CODEX_SANDBOX === "seatbelt";

const localBindingConfig = {
  main: "./worker/index.ts",
  compatibility_flags: ["nodejs_compat"],
  d1_databases: [{
    binding: "DB",
    database_name: "deepshovel-local",
    database_id: LOCAL_DATABASE_ID,
  }],
  r2_buckets: [{ binding: "BUCKET", bucket_name: "deepshovel-local" }],
};

const glBenchEsmPath = fileURLToPath(
  new URL("./node_modules/gl-bench/dist/gl-bench.module.js", import.meta.url),
);

function glBenchEsmCompat() {
  return {
    name: "deepshovel:gl-bench-esm",
    enforce: "pre" as const,
    resolveId(source: string) {
      return source === "gl-bench" ? glBenchEsmPath : null;
    },
  };
}

export default defineConfig(async ({ command }) => {
  // Keep Wrangler and Miniflare state project-local. These are non-secret tool
  // settings; application environment belongs in ignored `.env*` files.
  process.env.WRANGLER_WRITE_LOGS ??= "false";
  process.env.WRANGLER_LOG_PATH ??= ".wrangler/logs";
  process.env.MINIFLARE_REGISTRY_PATH ??= ".wrangler/registry";

  // Wrangler snapshots its log path while the Cloudflare plugin is imported.
  const { cloudflare } = await import("@cloudflare/vite-plugin");

  return {
    server: {
      host: "0.0.0.0",
      allowedHosts: ["terminal.local"],
      ...(isCodexSeatbeltSandbox
        ? { watch: { useFsEvents: false, usePolling: true } }
        : {}),
    },
    plugins: [
      // gl-bench advertises a CommonJS browser build ahead of its ESM build.
      // Keep this as a plugin because vinext carries user plugins, but not
      // resolve aliases, into its isolated client build environment.
      glBenchEsmCompat(),
      vinext(),
      runtimeAssets(),
      releaseStamp(),
      cloudflare({
        viteEnvironment: { name: "rsc", childEnvironments: ["ssr"] },
        inspectorPort: false,
        // Wrangler owns local Worker bindings and `.dev.vars`. Production
        // builds keep the hosting-derived inline config so a loopback URL can
        // never become a deployed binding accidentally.
        ...(command === "serve"
          ? { configPath: "./wrangler.local.jsonc" }
          : { config: localBindingConfig }),
      }),
    ],
  };
});
