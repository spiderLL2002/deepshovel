import { cp, mkdir, rm } from "node:fs/promises";
import { resolve } from "node:path";
import type { Plugin } from "vite";

/** Copy the D1 migrations needed by the installed local-public runtime. */
export function runtimeAssets(): Plugin {
  let root = process.cwd();

  return {
    name: "deepshovel-runtime-assets",
    apply: "build",
    configResolved(config) {
      root = config.root;
    },
    async closeBundle() {
      const outputDirectory = resolve(root, "dist", "runtime", "drizzle");
      await rm(outputDirectory, { recursive: true, force: true });
      await mkdir(outputDirectory, { recursive: true });
      await cp(resolve(root, "drizzle"), outputDirectory, { recursive: true });
    },
  };
}
