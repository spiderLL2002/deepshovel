import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..");
const SOURCE_ROOTS = ["app", "lib", "db", "worker"];

async function sourceFiles(directory) {
  const files = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) files.push(...await sourceFiles(absolute));
    else if (/\.(?:ts|tsx)$/.test(entry.name)) files.push(absolute);
  }
  return files;
}

function importSpecifiers(source) {
  const specifiers = [];
  const staticImport = /(?:import|export)\s+(?:type\s+)?(?:[^'";]*?\s+from\s+)?["']([^"']+)["']/g;
  const dynamicImport = /import\(\s*["']([^"']+)["']\s*\)/g;
  for (const match of source.matchAll(staticImport)) specifiers.push(match[1]);
  for (const match of source.matchAll(dynamicImport)) specifiers.push(match[1]);
  return specifiers;
}

function resolveProjectImport(from, specifier, knownFiles) {
  let base;
  if (specifier.startsWith("@/")) base = path.join(PROJECT_ROOT, specifier.slice(2));
  else if (specifier.startsWith(".")) base = path.resolve(path.dirname(from), specifier);
  else return null;
  for (const candidate of [
    base,
    `${base}.ts`,
    `${base}.tsx`,
    path.join(base, "index.ts"),
    path.join(base, "index.tsx"),
  ]) {
    if (knownFiles.has(candidate)) return candidate;
  }
  return null;
}

function isInside(root, target) {
  const relative = path.relative(root, target);
  return relative !== "" && !relative.startsWith(`..${path.sep}`) && relative !== ".." && !path.isAbsolute(relative);
}

function reachableProjectDependencies(graph, start) {
  const reachable = new Set();
  const pending = [...(graph.get(start) ?? [])];
  while (pending.length > 0) {
    const dependency = pending.pop();
    if (reachable.has(dependency)) continue;
    reachable.add(dependency);
    pending.push(...(graph.get(dependency) ?? []));
  }
  return reachable;
}

async function projectGraph() {
  const files = (await Promise.all(
    SOURCE_ROOTS.map((root) => sourceFiles(path.join(PROJECT_ROOT, root))),
  )).flat();
  const knownFiles = new Set(files);
  const graph = new Map();
  const sources = new Map();
  for (const file of files) {
    const source = await readFile(file, "utf8");
    sources.set(file, source);
    graph.set(file, importSpecifiers(source)
      .map((specifier) => resolveProjectImport(file, specifier, knownFiles))
      .filter(Boolean));
  }
  return { files, graph, sources };
}

test("application modules preserve the documented dependency direction", async () => {
  const { files, graph, sources } = await projectGraph();
  const graphRoot = path.join(PROJECT_ROOT, "lib/evidence-graph");
  const retrievalRoot = path.join(PROJECT_ROOT, "lib/evidence-retrieval");
  const apiRoot = path.join(PROJECT_ROOT, "app/api");
  const componentsRoot = path.join(PROJECT_ROOT, "app/components");
  const serverRoot = path.join(PROJECT_ROOT, "lib/server");
  // Generated cross-language constants: pure data with no imports, so the
  // domain modules may depend on it without losing self-containment.
  const sharedContract = path.join(PROJECT_ROOT, "lib/shared-contract.generated.ts");
  assert.deepEqual(importSpecifiers(sources.get(sharedContract)), [],
    "lib/shared-contract.generated.ts must remain an import-free leaf module");

  for (const file of files) {
    const relative = path.relative(PROJECT_ROOT, file);
    const source = sources.get(file);
    const specifiers = importSpecifiers(source);
    const dependencies = graph.get(file) ?? [];
    if (file.startsWith(`${graphRoot}${path.sep}`)) {
      assert.ok(
        specifiers.every((specifier) => specifier.startsWith("."))
          && dependencies.length === specifiers.length
          && dependencies.every((dependency) => isInside(graphRoot, dependency) || dependency === sharedContract),
        `${relative} must remain a pure, self-contained domain module`,
      );
    }
    if (file.startsWith(`${retrievalRoot}${path.sep}`)) {
      assert.ok(
        specifiers.every((specifier) => specifier.startsWith("."))
          && dependencies.length === specifiers.length
          && dependencies.every((dependency) => isInside(retrievalRoot, dependency)
            || isInside(graphRoot, dependency) || dependency === sharedContract),
        `${relative} may depend only on retrieval or evidence-graph modules`,
      );
    }
    if (file.startsWith(`${apiRoot}${path.sep}`)) {
      assert.ok(
        dependencies.every((dependency) => !isInside(componentsRoot, dependency)),
        `${relative} must not depend on browser components`,
      );
    }
    if (/^[\s\S]*?["']use client["'];/.test(source)) {
      const reachable = reachableProjectDependencies(graph, file);
      assert.ok(
        [...reachable].every((dependency) => !isInside(serverRoot, dependency)
          && !isInside(apiRoot, dependency)),
        `${relative} must not reach server or API implementation modules`,
      );
    }
  }
});

test("TypeScript application imports remain acyclic", async () => {
  const { graph } = await projectGraph();
  const visited = new Set();
  const active = new Set();
  const stack = [];

  function visit(file) {
    if (active.has(file)) {
      const start = stack.indexOf(file);
      const cycle = [...stack.slice(start), file]
        .map((item) => path.relative(PROJECT_ROOT, item))
        .join(" -> ");
      assert.fail(`circular application dependency: ${cycle}`);
    }
    if (visited.has(file)) return;
    visited.add(file);
    active.add(file);
    stack.push(file);
    for (const dependency of graph.get(file) ?? []) visit(dependency);
    stack.pop();
    active.delete(file);
  }

  for (const file of graph.keys()) visit(file);
});
