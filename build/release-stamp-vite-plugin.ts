import { execFileSync } from "node:child_process";
import type { Plugin } from "vite";

export const SOURCE_COMMIT_PLACEHOLDER = "__DEEPSHOVEL_SOURCE_COMMIT__";

function git(args: string[]) {
  return execFileSync("git", args, { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
}

/** The 12-digit source commit, marked `-dirty` when tracked files differ from it. */
export function sourceCommit() {
  try {
    const commit = git(["rev-parse", "--short=12", "HEAD"]);
    if (!/^[0-9a-f]{12}$/u.test(commit)) return "unknown";
    return git(["status", "--porcelain", "--untracked-files=no"]) ? `${commit}-dirty` : commit;
  } catch {
    return "unknown";
  }
}

/**
 * Stamp the release identity into `lib/release.ts` during a build. A plugin
 * (not `define`) because vinext carries user plugins into every environment.
 */
export function releaseStamp(): Plugin {
  let commit = "unknown";
  return {
    name: "deepshovel-release-stamp",
    apply: "build",
    buildStart() {
      commit = sourceCommit();
    },
    transform(code, id) {
      if (!id.replaceAll("\\", "/").endsWith("/lib/release.ts")) return null;
      return { code: code.replace(JSON.stringify(SOURCE_COMMIT_PLACEHOLDER), JSON.stringify(commit)), map: null };
    },
  };
}
