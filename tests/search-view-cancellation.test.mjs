import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

test("leaving the retained search view aborts its request and invalidates late errors", () => {
  const source = readFileSync(new URL("../app/components/evidence-search.tsx", import.meta.url), "utf8");
  const tree = ts.createSourceFile("search.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let lifecycle;
  function visit(node) {
    if (ts.isCallExpression(node) && node.expression.getText(tree) === "useEffect"
      && node.arguments[0].getText(tree).includes("requestGeneration.current += 1")) lifecycle = node.getText(tree);
    ts.forEachChild(node, visit);
  }
  visit(tree);
  assert.ok(lifecycle, "exercise the actual search lifecycle effect");
  const controller = new AbortController();
  const requestController = { current: controller };
  const requestGeneration = { current: 7 };
  let busy = true;
  let aiBusy = true;
  let setup;
  let dependencies;
  const evaluate = new Function("active", "useEffect", "requestController", "requestGeneration", "setBusy", "setAiBusy", lifecycle);
  const run = (active) => evaluate(active, (callback, deps) => { setup = callback; dependencies = deps; },
    requestController, requestGeneration, (value) => { busy = value; }, (value) => { aiBusy = value; });
  run(true);
  assert.deepEqual(dependencies, [true]);
  const leave = setup();
  assert.equal(controller.signal.aborted, false);
  leave();
  assert.equal(controller.signal.aborted, true);
  assert.equal(requestController.current, null);
  assert.equal(requestGeneration.current, 8, "late request callbacks no longer match the current generation");
  assert.equal(busy, false);
  assert.equal(aiBusy, false);
  run(false);
  assert.deepEqual(dependencies, [false]);
  assert.equal(setup(), undefined, "a hidden view must not create a new request lifecycle");
});
