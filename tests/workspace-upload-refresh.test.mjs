import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = ts.createSourceFile("workspace.tsx",
  readFileSync(new URL("../app/workspace.tsx", import.meta.url), "utf8"),
  ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);

function workspaceFunction(name, scope) {
  let initializer;
  function visit(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(source) === name) initializer = node.initializer;
    ts.forEachChild(node, visit);
  }
  visit(source);
  assert.ok(initializer, `${name} exists in the production component`);
  if (ts.isCallExpression(initializer)) initializer = initializer.arguments[0]; // useCallback
  const { outputText } = ts.transpileModule(`const handler = ${initializer.getText(source)};`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
  });
  return new Function("scope", `with (scope) { ${outputText}; return handler; }`)(scope);
}

test("a library upload refreshes the pending task list after switching views", async () => {
  const events = [];
  const scope = {
    activeView: "library",
    activeViewRef: { current: "library" },
    activityFilterRef: { current: "processing" },
    targetArtifact: { current: null },
    artifactInput: { current: { value: "file" } },
    workspaceMounted: { current: true },
    compileJobsRef: { current: {} },
    submittingVersions: { current: new Set() },
    artifactIdsByVersion: { current: new Map() },
    uploadedPendingArtifacts: { current: new Map() },
    uploadControllers: { current: new Map() },
    uploadScheduler: { run: async (_signal, work) => work() },
    consumeUploadSelection: (files, clear) => { clear(); return [...files]; },
    uploadArtifact: async (file) => ({ id: "version:qa", artifactId: "artifact:qa", fileName: file.name }),
    rememberArtifactIdentity() {},
    setError() {},
    setActivityFilter: value => events.push(["filter", value]),
    setActiveView: value => { scope.activeViewRef.current = value; },
    addActivity() {},
    patchActivity() {},
    setPendingTaskArtifacts() {},
    loadTaskPendingArtifacts: async () => events.push(["loaded", "pending"]),
    loadLibrary: async () => events.push(["loaded", "library"]),
    loadCompletedLibrary: async () => {},
    loadSnapshots: async () => {},
    libraryQueryRef: { current: "" },
  };
  scope.refreshData = workspaceFunction("refreshData", scope);
  await workspaceFunction("uploadArtifacts", scope)([{ name: "acceptance.pdf" }]);
  assert.deepEqual(events, [["filter", "pending"], ["loaded", "pending"]]);
  assert.equal(scope.uploadControllers.current.size, 0);
});
