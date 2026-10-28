import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

test("Atlas exports use a mounted same-origin POST form and keep a retry button without navigating the workbench", () => {
  const html = readFileSync(new URL("../public/geochemical-atlas/world-atlas.web.html", import.meta.url), "utf8");
  const source = html.slice(html.indexOf("function offerLocalJsonDownload("), html.indexOf("\nfunction exportDatabasePatches"));
  const importer = readFileSync(new URL("../scripts/import-geochemical-atlas-web.mjs", import.meta.url), "utf8");
  assert.ok(importer.includes(JSON.stringify(source)), "reimport preserves the helper");
  const nodes = new Map(), submissions = [];
  const createElement = tag => ({tag, children: [], setAttribute() {},
    append(...children) { this.children.push(...children); for (const child of children) child.parent = this; },
    replaceChildren(...children) { this.children = []; this.append(...children); },
    requestSubmit() { assert.ok(this.parent?.mounted); this.onsubmit(); submissions.push(this); },
  });
  nodes.set("exportDatabasePatches", {parentElement: {after(node) { node.mounted = true; nodes.set(node.id, node); }}});
  const context = {$: id => nodes.get(id), document: {createElement}, URLSearchParams};
  runInNewContext(source, context);
  const data = {operations: [{reason: "测试 & = 原因", proposed_value: "1"}]};
  context.offerLocalJsonDownload("exportDatabasePatches", "draft.json", data);
  const form = submissions[0], notice = nodes.get("exportDatabasePatchesDownload");
  assert.equal(form.method, "post"); assert.equal(form.action, "/api/atlas/export");
  assert.equal(form.target, notice.children[1].name);
  assert.equal(notice.children[1].hidden, true);
  assert.deepEqual(JSON.parse(form.children.find(child => child.name === "payload").value), data);
  assert.equal(form.children.at(-1).type, "submit");
  form.requestSubmit(); assert.equal(submissions.length, 2);
  notice.children[1].contentDocument = {body: {textContent: '{"error":"导出结构无效"}'}};
  notice.children[1].onload(); assert.match(notice.children[0].textContent, /导出失败/);
  context.offerLocalJsonDownload("exportDatabasePatches", "draft.json", {value: "中".repeat(300000)});
  assert.match(notice.children[0].textContent, /超过 2 MiB/);
  assert.equal(submissions.length, 2);
});
