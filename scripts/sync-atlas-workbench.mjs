import { access, readFile, writeFile } from "node:fs/promises";
import { applyAtlasWorkbenchTheme } from "./atlas-workbench-theme.mjs";

const file = new URL("../public/geochemical-atlas/world-atlas.web.html", import.meta.url);
// A code-only clone intentionally contains no Atlas dataset. Existing local
// reference data stays untouched; ordinary builds never download or create it.
try {
  await access(new URL("../public/geochemical-atlas/world-atlas.samples.manifest.json", import.meta.url));
} catch (error) {
  if (error.code !== "ENOENT") throw error;
  console.log("Atlas reference data is not installed; building the application without it.");
  process.exit(0);
}
const before = await readFile(file, "utf8");
const after = await applyAtlasWorkbenchTheme(before);
if (after !== before) await writeFile(file, after);
