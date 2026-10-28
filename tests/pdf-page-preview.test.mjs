import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import ts from "typescript";

const require = createRequire(import.meta.url);
const tick = () => new Promise((resolve) => setImmediate(resolve));

function viewerHarness({ initialPage = 4, waitForRender = false, failLoad = false } = {}) {
  const states = [];
  let stateIndex = 0;
  let refIndex = 0;
  let effect;
  let cleanup;
  let queued;
  let finishRender;
  const observed = { pages: [], rotations: [], releases: 0, cancels: 0, draws: 0, disconnected: 0 };
  const canvas = { style: {}, getContext: () => ({ drawImage: () => observed.draws++ }) };
  const refs = [{ current: { clientWidth: 1000, clientHeight: 700 } }, { current: canvas }];
  const pdf = {
    numPages: 8,
    async getPage(page) {
      observed.pages.push(page);
      return {
        rotate: 0,
        getViewport({ scale, rotation }) {
          observed.rotations.push(rotation);
          return { width: (rotation % 180 ? 800 : 600) * scale, height: (rotation % 180 ? 600 : 800) * scale };
        },
        render() {
          return {
            promise: waitForRender ? new Promise((resolve) => { finishRender = resolve; }) : Promise.resolve(),
            cancel: () => { observed.cancels++; finishRender?.(); },
          };
        },
      };
    },
  };
  const source = readFileSync(new URL("../app/components/pdf-page-preview.tsx", import.meta.url), "utf8") + "\nexport { PdfPageViewer };";
  const compiled = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS } }).outputText;
  const compiledModule = { exports: {} };
  const react = {
    ...require("react"),
    useState(value) {
      const index = stateIndex++;
      if (!(index in states)) states[index] = value;
      return [states[index], (next) => { states[index] = next; }];
    },
    useRef: () => refs[refIndex++],
    useEffect: (callback) => { effect = callback; },
  };
  const dependencies = (name) => name === "react" ? react
    : name === "./pdf-document" ? {
      acquireDocument: () => ({ promise: failLoad ? Promise.reject(new Error("network")) : Promise.resolve(pdf), release: () => observed.releases++ }),
      loadPdfModule: async () => ({ AnnotationMode: { DISABLE: 0 } }),
    } : name === "./ui-icon" ? { UiIcon: "span" } : require(name);
  new Function("require", "module", "exports", "window", "document", "ResizeObserver", "requestAnimationFrame", "cancelAnimationFrame", compiled)(
    dependencies, compiledModule, compiledModule.exports, { devicePixelRatio: 1 }, { createElement: () => ({}) },
    class { observe() {} disconnect() { observed.disconnected++; } },
    (callback) => { queued = callback; return 1; }, () => { queued = null; },
  );
  return {
    observed, states,
    tree() { stateIndex = 0; refIndex = 0; return compiledModule.exports.PdfPageViewer({ url: "/paper.pdf", initialPage, title: "Paper" }); },
    async run() { cleanup?.(); cleanup = effect(); queued?.(); await tick(); },
    async dispose() { cleanup?.(); await tick(); },
    finish() { finishRender?.(); },
    recover() { failLoad = false; },
    url: compiledModule.exports.pdfInlineUrl,
  };
}

function elements(tree) {
  if (!tree || typeof tree !== "object") return [];
  if (Array.isArray(tree)) return tree.flatMap(elements);
  return [tree, ...elements(tree.props?.children)];
}
function click(tree, label) {
  elements(tree).find((node) => node.type === "button" && node.props.children === label).props.onClick();
}

test("PDF canvas starts at cited page, supports page changes and rotation, and releases document leases", async () => {
  const viewer = viewerHarness();
  viewer.tree(); await viewer.run();
  assert.deepEqual(viewer.observed.pages, [4]);
  assert.equal(viewer.observed.draws, 1);
  assert.equal(viewer.observed.releases, 1);
  click(viewer.tree(), "下一页");
  viewer.tree(); await viewer.run();
  assert.deepEqual(viewer.observed.pages, [4, 5]);
  click(viewer.tree(), "旋转 90°");
  viewer.tree(); await viewer.run();
  assert.equal(viewer.observed.rotations.at(-1), 90);
  await viewer.dispose();
  assert.equal(viewer.observed.releases, 3);
  assert.match(viewer.url("sha", -1), /#page=1&/);
});

test("closing while a page is rendering cancels work and prevents stale canvas writes", async () => {
  const viewer = viewerHarness({ waitForRender: true });
  viewer.tree(); await viewer.run();
  assert.equal(viewer.observed.releases, 0);
  await viewer.dispose();
  assert.equal(viewer.observed.cancels, 1);
  assert.equal(viewer.observed.draws, 0);
  assert.equal(viewer.observed.releases, 1);
  assert.equal(viewer.observed.disconnected, 1);
});

test("out-of-range cited pages clamp safely and a failed load can retry", async () => {
  const viewer = viewerHarness({ initialPage: 99, failLoad: true });
  viewer.tree(); await viewer.run();
  const failed = viewer.tree();
  assert.ok(elements(failed).some((node) => node.props?.role === "alert"));
  click(failed, "重试加载"); viewer.recover();
  viewer.tree(); await viewer.run();
  assert.deepEqual(viewer.observed.pages, []);
  viewer.tree(); await viewer.run();
  assert.deepEqual(viewer.observed.pages, [8]);
  assert.equal(viewer.observed.draws, 1);
  await viewer.dispose();
});
