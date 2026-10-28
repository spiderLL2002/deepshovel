import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("PDF region resize rendering cannot feed its loading state back into layout", () => {
  const component = readFileSync(new URL("../app/components/pdf-region-preview.tsx", import.meta.url), "utf8");
  const styles = readFileSync(new URL("../app/styles/library-paper-review.css", import.meta.url), "utf8");
  const loadingRule = styles.match(/\.pdf-region-loading\s*\{(?<body>[\s\S]*?)\}/u)?.groups?.body ?? "";

  assert.match(loadingRule, /position:\s*absolute/u);
  assert.match(loadingRule, /inset:\s*0/u);
  assert.doesNotMatch(loadingRule, /min-height/u);
  assert.match(component, /const nextCanvas = document\.createElement\("canvas"\)/u);
  assert.match(component, /drawImage\(nextCanvas, 0, 0\)/u);
  assert.match(component, /if \(!hasRendered\) setState\("loading"\)/u);
});
