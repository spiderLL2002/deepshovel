import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { streamPendingCompile } from "../app/pending-compile-stream.ts";

test("explicit Compile-all traverses 500,000 items with only one bounded page and submission in flight", async () => {
  let handled = 0;
  let fetched = 0;
  let progress = 0;
  const count = await streamPendingCompile({
    signal: new AbortController().signal,
    read: async (cursor) => {
      assert.equal(handled, fetched, "do not prefetch while the current page is being submitted");
      const start = Number(cursor ?? 0);
      const items = Array.from({ length: 20 }, (_, i) => start + i);
      fetched += items.length;
      return { items, nextCursor: fetched < 500_000 ? String(fetched) : null };
    },
    waitForCapacity: async () => {},
    submit: async (item) => { assert.equal(item, handled); handled += 1; },
    onProgress: (n) => { progress = n; },
  });
  assert.equal(count, 500_000);
  assert.equal(progress, count);
});

test("cancelling during capacity wait stops all subsequent submissions and page reads", async () => {
  const controller = new AbortController();
  let reads = 0;
  let submissions = 0;
  await assert.rejects(streamPendingCompile({
    signal: controller.signal,
    read: async () => { reads++; return { items: [1, 2], nextCursor: "next" }; },
    waitForCapacity: async () => { controller.abort(); },
    submit: async () => { submissions++; },
    onProgress: () => {},
  }), { name: "AbortError" });
  assert.equal(reads, 1);
  assert.equal(submissions, 0);
});

test("a failed page aborts the batch instead of silently dropping pending papers", async () => {
  let submissions = 0;
  await assert.rejects(streamPendingCompile({
    signal: new AbortController().signal,
    read: async () => { throw new Error("page unavailable"); },
    waitForCapacity: async () => {},
    submit: async () => { submissions++; },
    onProgress: () => {},
  }), /page unavailable/);
  assert.equal(submissions, 0);
});

test("cursor controls retain forward navigation with unknown search totals and omit random jumps", () => {
  const source = readFileSync(new URL("../app/components/pagination-controls.tsx", import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS } }).outputText;
  const compiledModule = { exports: {} };
  new Function("require", "module", "exports", compiled)(createRequire(import.meta.url), compiledModule, compiledModule.exports);
  const html = renderToStaticMarkup(createElement(compiledModule.exports.PaginationControls, {
    page: 3, pageSize: 20, total: null, hasMore: true, cursorPagination: true,
    onPageChange() {}, onPageSizeChange() {},
  }));
  assert.match(html, /第 3 页/);
  assert.doesNotMatch(html, /跳至|跳转页码|NaN/);
  assert.match(html, /<button class="text-button" type="button">下一页<\/button>/);
});
