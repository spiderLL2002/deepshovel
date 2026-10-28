import { cp, mkdir, rm } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const packageRoot = join(projectRoot, "node_modules", "pdfjs-dist");
const publicRoot = join(projectRoot, "public", "pdfjs");
const assetDirectories = ["cmaps", "standard_fonts", "wasm"];

await mkdir(publicRoot, { recursive: true });
for (const directory of assetDirectories) {
  const source = join(packageRoot, directory);
  const destination = join(publicRoot, directory);
  await rm(destination, { recursive: true, force: true });
  await cp(source, destination, { recursive: true, force: true });
}
