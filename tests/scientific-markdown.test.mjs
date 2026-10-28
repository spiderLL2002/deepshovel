import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import { normalizeScientificMarkdown, prepareScientificMarkdown } from "../app/scientific-markdown.ts";

function renderScientific(value) {
  return renderToStaticMarkup(createElement(ReactMarkdown,
    { remarkPlugins: [remarkMath], rehypePlugins: [rehypeKatex] },
    normalizeScientificMarkdown(value)));
}

test("metadata author markers render supported TeX while preserving extracted names", () => {
  const value = String.raw`Mark Nestmeyer $\cdot 1; 2 \boxtimes _ { \mathbf { P } }$ Alex J. McCoy-West $1 { } 3 _ { \oplus }$ Yuan Mei2 $\textcircled{1}$ Kevin W. Burton4`;
  const html = renderScientific(value);
  assert.doesNotMatch(html, /katex-error/u);
  assert.equal((html.match(/<math /gu) ?? []).length, 3);
  for (const name of ["Mark Nestmeyer", "Alex J. McCoy-West", "Yuan Mei2", "Kevin W. Burton4"]) {
    assert.ok(html.includes(name));
  }
});

test("truncated source formulas stay literal without nested math delimiters or red errors", () => {
  const values = [
    String.raw`Lastly, seawater $( \Delta ^ { 9 8 } \mathrm { M o } _ { \mathrm { s o l u t i o n } } .$ - $\mathrm { \Gamma _ { o x i d e } = 0 .`,
    String.raw`2 2$ and $\mathrm { F e } _ { \mathrm { P Y } } /$ $\mathrm { F e } _ { \mathrm { H R } } ~ < ~ 0 .`,
  ];
  for (const value of values) {
    assert.equal(prepareScientificMarkdown(value).incompleteMath, true);
    const html = renderScientific(value);
    assert.doesNotMatch(html, /katex-error|color:\s*#[fF]00/u);
    assert.match(html, /<code>/u);
    assert.match(html, /<annotation encoding="application\/x-tex">/u);
  }
  assert.equal((renderScientific(values[0]).match(/<annotation /gu) ?? []).length, 1);
});

test("an orphan closing dollar cannot swallow prose or the following intact isotope formula", () => {
  const quote = String.raw`0 \text{‰}$ in these shales (9/21 samples) with the likelihood of an even heavier seawater $\delta ^ { 9 8 } \mathrm { { M o } }$ during their deposition.`;
  const html = renderScientific(quote);
  assert.doesNotMatch(html, /katex-error/u);
  const formulas = [...html.matchAll(/<annotation encoding="application\/x-tex">([^<]*)<\/annotation>/gu)].map((match) => match[1]);
  assert.equal(formulas.length, 1);
  assert.match(formulas[0], /\\delta/u);
  assert.doesNotMatch(formulas.join(" "), /these shales|during|seawater/u);
  assert.match(html, /9\/21 samples/u);
  assert.match(html, /0 \\text\{‰\}\$/u);
  const splitDecimal = renderScientific("6 9$ Ga and 2.50 Ga was commonly $> 1 .");
  assert.doesNotMatch(splitDecimal, /katex|<math/u);
  assert.match(splitDecimal, /Ga and 2\.50 Ga was commonly/u);
  assert.match(splitDecimal, /6 9\$/u);
  const citation = renderScientific("7 0$ (Smith, 2020). $x^2=1$");
  const annotations = [...citation.matchAll(/<annotation encoding="application\/x-tex">([^<]*)<\/annotation>/gu)].map((match) => match[1]);
  assert.deepEqual(annotations, ["x^2=1"]);
  assert.match(citation, /\(Smith, 2020\)\./u);
});

test("valid TeX labels, currency and code are not reinterpreted as incomplete evidence", () => {
  const valid = String.raw`$x_{\mathrm{in}} = \text{Ga and 2.50 Ga}$`;
  assert.equal(prepareScientificMarkdown(valid).incompleteMath, false);
  assert.match(renderScientific(valid), /<math/u);
  assert.doesNotMatch(renderScientific(valid), /katex-error/u);
  assert.equal(normalizeScientificMarkdown("Code: `$x_{` and `$y$`."), "Code: `$x_{` and `$y$`.");
  const currency = renderScientific("Costs $5 and $10.");
  assert.match(currency, /Costs \$5 and \$10\./u);
  assert.doesNotMatch(currency, /katex|<code>/u);
  for (const formula of [String.raw`$x_{\rm in}=2$`, String.raw`$x=\mbox{the sample}$`]) {
    assert.equal(normalizeScientificMarkdown(formula), formula);
    assert.match(renderScientific(formula), /<math/u);
  }
  const fenced = "~~~tex\n$x_{\n~~~\n\nNormal text after fence.";
  assert.equal(normalizeScientificMarkdown(fenced), fenced);
});

test("scientific markdown normalizes common TeX delimiters", () => {
  assert.equal(
    normalizeScientificMarkdown("角度为 \\(\\theta_0\\)，且 \\[x^2+y^2=1\\]。"),
    "角度为 $\\theta_0$，且 $x^2+y^2=1$。",
  );
  assert.equal(normalizeScientificMarkdown("\\[x^2+y^2=1\\]"), "$$\nx^2+y^2=1\n$$");
  assert.equal(normalizeScientificMarkdown("\\[x^2+y^2=1\\]", true), "$x^2+y^2=1$");
});

test("formula-only parser output receives a bounded math wrapper", () => {
  const formula = "n \\left( y \\right) = n _ { 0 } \\mathrm { e } ^ { - \\lambda y }";
  assert.equal(normalizeScientificMarkdown(formula), `$$\n${formula}\n$$`);
  assert.equal(normalizeScientificMarkdown(formula, true), `$${formula}$`);
});

test("prose and already-delimited math remain unchanged", () => {
  const prose = "路径 C:\\data 不应被当作公式。";
  const math = "折射率为 $n_0$。";
  assert.equal(normalizeScientificMarkdown(prose), prose);
  assert.equal(normalizeScientificMarkdown(math), math);
});

test("scientific numeric ranges preserve single tildes while explicit deletion remains available", () => {
  const componentSource = readFileSync(new URL("../app/components/scientific-content.tsx", import.meta.url), "utf8");
  assert.match(componentSource, /\[\s*remarkGfm\s*,\s*\{\s*singleTilde\s*:\s*false\s*\}\s*\]/u);

  const plugins = [[remarkGfm, { singleTilde: false }], remarkMath];
  const ranges = "水深2400~3450 m，结核形成时代1.17~8.51 Ma，10Be/9Be比值(41.35~53.62)×10-12。";
  const html = renderToStaticMarkup(createElement(
    ReactMarkdown,
    { remarkPlugins: plugins },
    normalizeScientificMarkdown(ranges),
  ));
  assert.doesNotMatch(html, /<del>/u);
  assert.match(html, /2400~3450/u);
  assert.match(html, /1\.17~8\.51/u);
  assert.match(html, /41\.35~53\.62/u);

  const intentionalDeletion = renderToStaticMarkup(createElement(
    ReactMarkdown,
    { remarkPlugins: plugins },
    "~~withdrawn~~",
  ));
  assert.match(intentionalDeletion, /<del>withdrawn<\/del>/u);
});

test("mixed display delimiters reach nonempty KaTeX without a visible dollar marker", () => {
  const normalized = normalizeScientificMarkdown("角度为 \\[x^2+y^2=1\\]。");
  const html = renderToStaticMarkup(createElement(
    ReactMarkdown,
    { remarkPlugins: [remarkMath], rehypePlugins: [rehypeKatex] },
    normalized,
  ));

  assert.doesNotMatch(html, /\$\$/u);
  assert.match(html, /<annotation encoding="application\/x-tex">x\^2\+y\^2=1<\/annotation>/u);
  assert.match(html, /<msup>/u);

  const inlineHtml = renderToStaticMarkup(createElement(
    ReactMarkdown,
    { remarkPlugins: [remarkMath], rehypePlugins: [rehypeKatex] },
    normalizeScientificMarkdown("\\[x^2+y^2=1\\]", true),
  ));
  assert.doesNotMatch(inlineHtml, /class="katex-display"/u);
  assert.match(inlineHtml, /<annotation encoding="application\/x-tex">x\^2\+y\^2=1<\/annotation>/u);
});

test("sentence-level parser TeX is segmented conservatively and rendered", () => {
  const equation = "当 n \\left(y\\right)=n_0 e^{-\\lambda y} 时成立。";
  const symbols = "视角 \\theta 与 \\lambda 有关。";
  assert.equal(
    normalizeScientificMarkdown(equation),
    "当 $n \\left(y\\right)=n_0 e^{-\\lambda y}$ 时成立。",
  );
  assert.equal(normalizeScientificMarkdown(symbols), "视角 $\\theta$ 与 $\\lambda$ 有关。");

  for (const value of [equation, symbols]) {
    const html = renderToStaticMarkup(createElement(
      ReactMarkdown,
      { remarkPlugins: [remarkMath], rehypePlugins: [rehypeKatex] },
      normalizeScientificMarkdown(value),
    ));
    const visibleHtml = html.replace(/<span class="katex-mathml">[\s\S]*?<\/span>/gu, "");
    assert.match(html, /<annotation encoding="application\/x-tex">[^<]+<\/annotation>/u);
    assert.doesNotMatch(visibleHtml, /\\(?:left|right|theta|lambda)\b/u);
  }
});

test("recognized commands inside paths, URLs, escapes, and code stay literal", () => {
  const value = "路径 C:\\theta\\data、URL https://example.test/\\lambda、转义 \\\\theta、未知 \\notACommand 与代码 `\\lambda` 不是公式。";
  assert.equal(normalizeScientificMarkdown(value), value);
  const prose = "angle n \\left(y\\right)=value changes.";
  assert.equal(normalizeScientificMarkdown(prose), prose);
});

test("a real parser-style bare formula reaches KaTeX instead of visible TeX", () => {
  const formula = "y ( x ) = C _ { 2 } \\mathrm { + } { \\frac { \\ln [ \\cos ( \\lambda x + C _ { 1 } ) ] } { \\lambda } }";
  const html = renderToStaticMarkup(createElement(
    ReactMarkdown,
    { remarkPlugins: [remarkMath], rehypePlugins: [rehypeKatex] },
    normalizeScientificMarkdown(formula),
  ));

  assert.match(html, /class="katex-display"/u);
  assert.match(html, /<mfrac>/u);
  assert.match(html, /<mi>λ<\/mi>/u);
});

import { expandQuoteMathContext } from "../app/scientific-markdown.ts";

test("source context restores only the two bisected isotope-number formula boundaries", () => {
  const source = String.raw`Previous sentence. It is now evident from our updated shale dataset that seawater $\delta ^ { 9 8 } \mathrm { { M o } }$ between $\sim 2 . 6 9$ Ga and 2.50 Ga was commonly $> 1 . 0 \text{‰}$ (and also apparent in Fig. 9), much more so than was previously recognized.`;
  const quote = "6 9$ Ga and 2.50 Ga was commonly $> 1 .";
  const expected = String.raw`$\sim 2 . 6 9$ Ga and 2.50 Ga was commonly $> 1 . 0 \text{‰}$`;
  assert.equal(expandQuoteMathContext(source, quote), expected);
  assert.ok(source.includes(expected));
  assert.equal(expandQuoteMathContext(source, expected), null);
});

test("source context restores a real quote ending inside a balanced TeX group", () => {
  const quote = String.raw`Lastly, Fe oxide minerals preferentially remove lighter-mass Mo isotopes from seawater $( \Delta ^ { 9 8 } \mathrm { M o } _ { \mathrm { s o l u t i o n } } .$ - $\mathrm { \Gamma _ { o x i d e } = 0 .`;
  const expected = quote + String.raw` 8 3 \% - 2 . 1 9 \% 0 }$`;
  assert.equal(expandQuoteMathContext(`Prior text. ${expected} (Goldberg et al., 2009)). Next sentence.`, quote), expected);
});

test("source context restores a real quote beginning inside a per-mille formula", () => {
  const quote = String.raw`2 9 \text{‰}$ (see Fig.`;
  const expected = String.raw`$1 . 2 9 \text{‰}$ (see Fig.`;
  const source = `During deposition, seawater reached a value at least as heavy as ${expected} 9). Following sentence.`;
  assert.equal(expandQuoteMathContext(source, quote), expected);
});

test("source context accepts display math but rejects duplicate, missing, incomplete and non-math matches", () => {
  assert.equal(expandQuoteMathContext("Before $$\nx^{2} = 10\n$$ after", "^{2} = 1"), "$$\nx^{2} = 10\n$$");
  for (const [source, quote] of [
    ["$x^2 = 1$ then $x^2 = 1$", "x^2"],
    ["$x^2 = 1$", "x ^ 2"],
    ["$x^{2 = 1$", "2 = 1"],
    ["$x^2 = 1", "x^2"],
    ["$x^2$ and $y^{2$", "2$ and $y^{"],
    ["Costs $5 and $10.", "5 and"],
    ["Costs $5 for $10.", "5 for"],
    ["Use `$x^2$` as code.", "x^2"],
    ["Use ``a `$x^2$` b`` as code.", "x^2"],
    ["```tex\n$x^2$\n```", "x^2"],
    ["~~~tex\n$x^2$\n~~~", "x^2"],
    ["```tex\n$x^2$", "x^2"],
    [String.raw`Literal \$x^2\$`, "x^2"],
  ]) assert.equal(expandQuoteMathContext(source, quote), null, source);
});
