import assert from "node:assert/strict";
import test from "node:test";

import {
  BrowserProbeError,
  USER_INTERFACE_ACCEPTANCE_SCHEMA,
  browserReadExpression,
  buildBrowserProbePlan,
  createBrowserSkillPage,
  runBrowserProbes,
} from "../scripts/acceptance-user-interface.mjs";

const SOURCE_TRIGGER = "#paper-panel-source > div:nth-of-type(1) > button:nth-of-type(1)";
const PDF_FOCUS = ["#pdf-original-link", "#pdf-close", "#pdf-frame"];
const NAVIGATION = {
  文献库: "library",
  证据图谱: "knowledgeGraph",
  人工复核: "review",
  当前任务: "activity",
  观点校验: "search",
};
const ACTIVITY_LABELS = ["分析中", "失败", "待 Compile"];
const REVIEW_LABELS = ["全部", "未复核", "已通过"];

function fakePage({
  broken = "",
  approximations = ["2400~3450", "~95%"],
  completedRows = 3,
  existingSearchResults = true,
  hydrationInspections = 0,
} = {}) {
  const operations = [];
  const state = {
    view: "search",
    workflow: "search",
    paperTab: "claims",
    pdf: false,
    activityStatus: "分析中",
    review: "全部",
    queryDraft: "",
    querySubmitted: "",
    paperOrigin: "library",
    graphDepth: 0,
    graphClaimSelected: false,
    claimSeed: false,
    taxonomyScope: false,
    searchResults: existingSearchResults,
    searchQuery: "",
    modelSubmitted: false,
    taskKeyboardActivated: false,
    focus: "body",
    width: 1440,
    height: 900,
    hydrationInspections,
  };

  const focusId = () => state.focus.startsWith("#") ? state.focus.slice(1) : "";
  const rowCount = () => {
    if (state.querySubmitted) return 0;
    return completedRows;
  };
  const framePage = broken === "pdfPage" ? 8 : 9;
  const frameSrc = `/api/artifacts/${"a".repeat(64)}?disposition=inline#page=${framePage}&zoom=page-fit`;

  const snapshot = () => {
    const pdfInside = state.pdf && PDF_FOCUS.includes(state.focus);
    const isPaper = state.view === "paper";
    const isLibrary = state.view === "library";
    const isGraph = state.view === "knowledgeGraph";
    const levelOrder = ["domain", "field", "subfield", "topic"];
    const currentRows = isLibrary ? rowCount() : 0;
    return {
      metadata: {
        lang: broken === "language" ? "en" : "zh-CN",
        title: "DeepShovel · Evidence Bench",
        hasIcon: true,
      },
      readiness: {
        workspaceState: broken === "neverHydrated" || state.hydrationInspections > 0 ? "syncing" : "ready",
      },
      viewport: {
        width: state.width,
        height: state.height,
        clientWidth: state.width,
        scrollWidth: state.width + (broken === "overflow" && state.width === 390 && state.view === "review" ? 24 : 0),
        horizontalOverflow: broken === "overflow" && state.width === 390 && state.view === "review" ? 24 : 0,
      },
      activeView: state.view,
      navigation: Object.entries(NAVIGATION).map(([label, value]) => ({
        label: `${label}：功能说明`,
        current: state.view === value,
      })),
      focus: {
        id: focusId(),
        selector: state.focus,
        taskButton: state.focus === '.workspace-nav-item[aria-label^="当前任务"]',
        insidePdf: pdfInside,
      },
      workflow: {
        tabs: ["search", "paper"].map((id) => ({
          id: `search-task-tab-${id}`,
          selected: id === state.workflow,
          tabIndex: id === state.workflow ? 0 : -1,
          controls: `search-task-panel-${id}`,
          panelExists: true,
          panelHidden: id !== state.workflow,
        })),
        selected: `search-task-tab-${state.workflow}`,
        paperOptionCount: broken === "corpusLeak" && state.querySubmitted ? 0 : completedRows,
      },
      activity: {
        present: state.view === "activity",
        title: state.view === "activity" ? "当前任务" : "",
        status: state.view === "activity" ? state.activityStatus : "",
        filterLabels: state.view === "activity" ? [...ACTIVITY_LABELS] : [],
        hasUploadButton: state.view === "activity",
        dialogCount: broken === "taskDialog" && state.view === "activity" ? 1 : 0,
      },
      library: {
        present: isLibrary,
        loading: false,
        legacyActionLabels: isLibrary && broken === "legacyLibraryActions"
          ? ["待 Compile", "删除全部文献"] : [],
        queryValue: isLibrary ? state.queryDraft : "",
        hasClearButton: isLibrary && Boolean(state.querySubmitted),
        rowCount: currentRows,
        emptyTitle: isLibrary && state.querySubmitted ? "没有匹配的文献"
          : isLibrary && !currentRows ? (broken === "emptyCopy" ? "错误空状态" : "还没有可查看的文献") : "",
        emptyRecoveryLabels: isLibrary && !currentRows && !state.querySubmitted
          ? ["上传文献"] : [],
        evidenceTriggerSelector: isLibrary && rowCount() ? "#first-evidence-button" : "",
      },
      paper: {
        present: isPaper,
        title: isPaper ? "深海稀土证据论文" : "",
        tabs: isPaper ? ["claims", "source", "run"].map((id) => ({
          id: `paper-tab-${id}`,
          selected: id === state.paperTab,
          tabIndex: id === state.paperTab ? 0 : -1,
          controls: `paper-panel-${id}`,
          panelExists: id === state.paperTab,
        })) : [],
        selected: isPaper ? `paper-tab-${state.paperTab}` : "",
        pdfTriggerSelector: isPaper && state.paperTab === "source" ? SOURCE_TRIGGER : "",
        pdfTriggerPage: isPaper && state.paperTab === "source" ? 9 : null,
        compareTriggerSelector: isPaper && state.paperTab === "claims" ? "#paper-compare-existing-claim" : "",
      },
      pdf: {
        open: state.pdf,
        modal: state.pdf,
        title: state.pdf ? "深海稀土证据论文" : "",
        frameSrc: state.pdf ? frameSrc : "",
        frameTitle: state.pdf ? `深海稀土证据论文，第 ${framePage} 页` : "",
        fullSourceHref: state.pdf ? frameSrc : "",
        fullSourceSafe: state.pdf && broken !== "unsafeOriginalLink",
        scrollLocked: state.pdf,
        focusableSelectors: state.pdf ? [...PDF_FOCUS] : [],
        backdropPoint: state.pdf ? { x: 12, y: 12 } : null,
        bounds: state.pdf ? {
          left: broken === "modalOverflow" && state.width === 390 ? -5 : 16,
          top: 16,
          right: state.width - 16,
          bottom: state.height - 16,
        } : null,
      },
      review: {
        present: state.view === "review",
        selected: state.view === "review" ? state.review : "",
        filters: state.view === "review" ? [...REVIEW_LABELS] : [],
        empty: state.view === "review" && state.review !== "全部",
        emptyText: state.view === "review" && state.review !== "全部" ? "当前筛选条件下没有文献。" : "",
      },
      graph: {
        present: isGraph,
        loading: false,
        taxonomyColumns: isGraph ? levelOrder.slice(0, Math.min(state.graphDepth + 1, levelOrder.length)).map((level) => ({
          level,
          firstMainSelector: broken === "graphPath" && level === "subfield" ? "" : `#taxonomy-${level}-main`,
          firstSearchSelector: `#taxonomy-${level}-search`,
        })) : [],
        claimCount: isGraph && state.graphDepth >= 4 ? 6 : 0,
        claimSelector: isGraph && state.graphDepth >= 4 ? "#graph-existing-claim" : "",
        claimEvidenceSelector: isGraph && state.graphDepth >= 4 ? "#graph-claim-evidence" : "",
        neighborhoodPresent: isGraph && state.graphClaimSelected,
        neighborhoodEvidenceSelector: isGraph && state.graphClaimSelected ? "#graph-neighborhood-evidence" : "",
        edgeCount: isGraph && state.graphClaimSelected ? 2 : 0,
        resetSelector: isGraph ? "#graph-reset" : "",
      },
      search: {
        claimSeed: state.view === "search" && state.claimSeed,
        taxonomyScope: state.view === "search" && state.taxonomyScope,
        existingResultSelector: state.view === "search" && state.searchResults && !state.claimSeed && !state.taxonomyScope
          ? "#existing-search-result" : "",
        busy: false,
        error: state.modelSubmitted && broken === "modelFailure" ? "provider request_failed" : "",
        degraded: state.modelSubmitted && broken === "modelDegraded",
        previewCount: state.searchResults ? 3 : 0,
        resultCount: state.searchResults ? 2 : 0,
      },
      scientific: {
        nodeCount: isPaper ? 12 : 0,
        approximateSamples: isPaper ? [...approximations] : [],
        deletedNumericCount: broken === "numericDeletion" ? 1 : 0,
        forbiddenElementCount: broken === "xss" ? 1 : 0,
        dangerousLinkCount: broken === "unsafeScientificLink" ? 1 : 0,
      },
    };
  };

  const page = {
    operations,
    state,
    async goto(url) {
      operations.push({ action: "goto", url });
      state.view = "search";
      state.workflow = "search";
      state.focus = "body";
    },
    async inspect() {
      operations.push({ action: "inspect" });
      const observation = snapshot();
      if (state.hydrationInspections > 0) state.hydrationInspections -= 1;
      return observation;
    },
    async click(selector) {
      operations.push({ action: "click", selector });
      for (const [label, view] of Object.entries(NAVIGATION)) {
        if (selector.includes(`aria-label^="${label}"`)) {
          if (view === "knowledgeGraph" && state.view !== "knowledgeGraph") {
            state.graphDepth = 0;
            state.graphClaimSelected = false;
          }
          if (view === "activity" && broken === "taskClick") return;
          if (view === "library" && broken === "taskReturn" && state.taskKeyboardActivated) return;
          state.view = view;
          state.focus = "body";
          return;
        }
      }
      if (selector === "button.brand") {
        state.view = "search";
        state.focus = "body";
        if (broken !== "claimBrand") state.claimSeed = false;
        if (broken !== "scopeBrand") state.taxonomyScope = false;
        return;
      }
      if (selector.startsWith("#search-task-tab-")) {
        state.workflow = selector.replace("#search-task-tab-", "");
        state.focus = selector;
        return;
      }
      if (selector === '.library-search button[type="submit"]') {
        state.querySubmitted = state.queryDraft;
        return;
      }
      if (selector === '.evidence-search .search-form button[type="submit"]') {
        state.modelSubmitted = true;
        if (!["modelNoResults", "modelFailure", "modelDegraded"].includes(broken)) state.searchResults = true;
        return;
      }
      if (selector === ".library-toolbar > button") {
        state.queryDraft = "";
        state.querySubmitted = "";
        return;
      }
      if (selector === "#first-evidence-button") {
        state.paperOrigin = "library";
        state.view = "paper";
        state.paperTab = "claims";
        return;
      }
      const taxonomyMain = selector.match(/^#taxonomy-(domain|field|subfield|topic)-main$/u);
      if (taxonomyMain) {
        state.graphDepth = ["domain", "field", "subfield", "topic"].indexOf(taxonomyMain[1]) + 1;
        return;
      }
      const taxonomySearch = selector.match(/^#taxonomy-(domain|field|subfield|topic)-search$/u);
      if (taxonomySearch) {
        state.view = "search";
        state.taxonomyScope = true;
        state.claimSeed = false;
        return;
      }
      if (selector === "#graph-existing-claim") {
        state.graphClaimSelected = true;
        return;
      }
      if (selector === "#graph-neighborhood-evidence" || selector === "#graph-claim-evidence") {
        state.paperOrigin = broken === "graphReturn" ? "library" : "knowledgeGraph";
        state.view = "paper";
        state.paperTab = "claims";
        return;
      }
      if (selector === "#graph-reset") {
        state.graphDepth = 0;
        state.graphClaimSelected = false;
        return;
      }
      if (selector === "#paper-compare-existing-claim") {
        state.view = "search";
        state.claimSeed = true;
        state.taxonomyScope = false;
        return;
      }
      if (selector === 'button[aria-label="移除观点起点"]') {
        state.claimSeed = false;
        return;
      }
      if (selector === 'button[aria-label="移除 OpenAlex 检索范围"]') {
        state.taxonomyScope = false;
        return;
      }
      if (selector === "#existing-search-result") {
        state.paperOrigin = "search";
        state.view = "paper";
        state.paperTab = "claims";
        return;
      }
      if (selector.startsWith("#paper-tab-")) {
        state.paperTab = selector.replace("#paper-tab-", "");
        state.focus = selector;
        return;
      }
      if (selector === SOURCE_TRIGGER) {
        state.pdf = true;
        state.focus = "#pdf-close";
        return;
      }
      if (selector.includes('aria-label="关闭 PDF 预览"')) {
        if (broken !== "pdfButton") state.pdf = false;
        state.focus = broken === "pdfReturn" ? "body" : SOURCE_TRIGGER;
        return;
      }
      if (selector === ".paper-view button.back-button") {
        state.view = state.paperOrigin;
        if (state.view === "knowledgeGraph") {
          state.graphDepth = 0;
          state.graphClaimSelected = false;
        }
        return;
      }
      const review = selector.match(/\.review-filter button:nth-of-type\((\d+)\)/u);
      if (review) {
        if (broken !== "reviewFilter") state.review = REVIEW_LABELS[Number(review[1]) - 1];
        return;
      }
      throw new Error(`Unexpected or disallowed browser click: ${selector}`);
    },
    async fill(selector, value) {
      operations.push({ action: "fill", selector, value });
      if (selector === "#library-query") state.queryDraft = value;
      else if (selector === ".evidence-search .search-form textarea") state.searchQuery = value;
      else assert.fail(`Unexpected browser input selector ${selector}`);
    },
    async clickPoint(point) {
      operations.push({ action: "clickPoint", ...point });
      if (state.pdf) {
        if (broken !== "pdfBackdrop") state.pdf = false;
        state.focus = broken === "pdfReturn" ? "body" : SOURCE_TRIGGER;
      } else {
        assert.fail("Real backdrop pointer interaction has no visible modal");
      }
    },
    async press(selector, key) {
      operations.push({ action: "press", selector, key });
      if (selector === "#library-query" && key === "Enter") {
        state.querySubmitted = state.queryDraft;
        return;
      }
      if (selector === '.workspace-nav-item[aria-label^="当前任务"]' && key === "Enter") {
        if (broken !== "taskKeyboard") state.view = "activity";
        state.taskKeyboardActivated = true;
        state.focus = selector;
        return;
      }
      if (selector.startsWith("#search-task-tab-")) {
        const ids = ["search", "paper"];
        const current = selector.replace("#search-task-tab-", "");
        const position = ids.indexOf(current);
        const next = key === "ArrowRight" ? (position + 1) % ids.length
          : key === "ArrowLeft" ? (position + ids.length - 1) % ids.length
            : key === "Home" ? 0 : ids.length - 1;
        state.workflow = ids[next];
        state.focus = broken === "workflowKeyboard" ? "body" : `#search-task-tab-${ids[next]}`;
        return;
      }
      if (selector.startsWith("#paper-tab-")) {
        const ids = ["claims", "source", "run"];
        const current = selector.replace("#paper-tab-", "");
        const position = ids.indexOf(current);
        const next = key === "ArrowRight" ? (position + 1) % ids.length
          : key === "ArrowLeft" ? (position + ids.length - 1) % ids.length
            : key === "Home" ? 0 : ids.length - 1;
        state.paperTab = ids[next];
        state.focus = broken === "paperKeyboard" ? "body" : `#paper-tab-${ids[next]}`;
        return;
      }
      if (state.pdf && selector === PDF_FOCUS[0] && key === "Shift+Tab") {
        state.focus = broken === "pdfTrap" ? "body" : PDF_FOCUS.at(-1);
        return;
      }
      if (state.pdf && selector === PDF_FOCUS.at(-1) && key === "Tab") {
        state.focus = broken === "pdfTrap" ? "body" : PDF_FOCUS[0];
        return;
      }
      if (state.pdf && selector.includes('aria-label="关闭 PDF 预览"') && key === "Tab") {
        state.focus = broken === "pdfTrap" ? "body" : PDF_FOCUS.at(-1);
        return;
      }
      if (state.pdf && selector.includes('aria-label="关闭 PDF 预览"') && key === "Escape") {
        state.pdf = false;
        state.focus = broken === "pdfReturn" ? "body" : SOURCE_TRIGGER;
        return;
      }
      throw new Error(`Unexpected or disallowed keyboard operation: ${selector} ${key}`);
    },
    async setViewport(device) {
      operations.push({ action: "viewport", ...device });
      state.width = device.width;
      state.height = device.height;
    },
  };

  return page;
}

function options(page, extra = {}) {
  return {
    page,
    baseUrl: "https://deepshovel.example.test",
    settleAttempts: 2,
    settleDelayMs: 0,
    ...extra,
  };
}

async function failsAt(broken, id, pattern, extra = {}) {
  const page = fakePage({ broken, ...extra });
  await assert.rejects(runBrowserProbes(options(page)), (error) => {
    assert.ok(error instanceof BrowserProbeError);
    assert.equal(error.checkId, id);
    assert.equal(error.report.passed, false);
    assert.equal(error.report.results.at(-1).id, id);
    assert.equal(error.report.results.at(-1).status, "failed");
    assert.match(error.message, pattern);
    return true;
  });
}

test("browser acceptance pre-registers 28 honest checks and 28 responsive cases", () => {
  const plan = buildBrowserProbePlan();
  assert.equal(plan.schema_version, USER_INTERFACE_ACCEPTANCE_SCHEMA);
  assert.equal(plan.non_mutating, true);
  assert.equal(plan.checks.length, 28);
  assert.equal(new Set(plan.checks.map((item) => item.id)).size, 28);
  assert.deepEqual(plan.viewports.map((item) => item.width), [390, 768, 1440, 1024]);
  assert.equal(plan.responsive_cases.length, 28);
  assert.deepEqual(plan.prohibited_actions, ["compile", "model_request", "upload", "delete", "review_submission"]);
  for (const item of plan.checks) assert.ok(item.inventoryIds.length);
  assert.doesNotThrow(() => JSON.parse(JSON.stringify(plan)));
});

test("browser acceptance validates bounded and customizable device viewports", () => {
  const plan = buildBrowserProbePlan({ viewports: [{ width: 1024, height: 480 }] });
  assert.deepEqual(plan.viewports, [{ width: 1024, height: 480, mobile: false }]);
  assert.equal(plan.responsive_cases.length, 7);
  for (const value of [[], [{ width: 100, height: 480 }], [{ width: 390, height: 20 }], Array(9).fill({ width: 390, height: 844 })]) {
    assert.throws(() => buildBrowserProbePlan({ viewports: value }), /viewport|dimensions/u);
  }
  const authorized = buildBrowserProbePlan({ allowModelInteractions: true });
  assert.equal(authorized.non_mutating, false);
  assert.ok(!authorized.prohibited_actions.includes("model_request"));
  assert.deepEqual(authorized.prohibited_actions, ["compile", "upload", "delete", "review_submission"]);
  assert.throws(() => buildBrowserProbePlan({ allowModelInteractions: "true" }), /explicitly true or false/u);
});

test("browser DOM evaluation is bounded and read-only; user interaction is not scripted", () => {
  const expression = browserReadExpression();
  assert.match(expression, /^JSON\.stringify\(/u);
  assert.match(expression, /getClientRects|querySelector/u);
  assert.doesNotMatch(expression, /\.(?:click|fill|press)\s*\(|dispatchEvent|KeyboardEvent|MouseEvent/u);
  assert.doesNotMatch(expression, /localStorage|sessionStorage|document\.cookie|indexedDB|fetch\s*\(|XMLHttpRequest/u);
  assert.doesNotMatch(expression, /innerHTML\s*=|outerHTML\s*=|insertAdjacentHTML|appendChild|removeChild/u);
});

test("browser skill adapter uses actual locators, readonly evaluate and supported viewport CDP", async () => {
  const operations = [];
  let result = JSON.stringify({ activeView: "search" });
  const tab = {
    async goto(url) { operations.push(["goto", url]); },
    playwright: {
      async evaluate(input) {
        operations.push(["evaluate", input]);
        return result;
      },
      locator(selector) {
        return {
          async click() { operations.push(["locator.click", selector]); },
          async fill(value) { operations.push(["locator.fill", selector, value]); },
          async press(key) { operations.push(["locator.press", selector, key]); },
        };
      },
    },
    cua: { async click(point) { operations.push(["cua.click", point]); } },
    cdp: { async send(method, payload) { operations.push(["cdp.send", method, payload]); } },
  };
  const page = createBrowserSkillPage(tab);
  await page.goto("https://deepshovel.example.test/");
  assert.deepEqual(await page.inspect(), { activeView: "search" });
  result = { result: JSON.stringify({ activeView: "library" }) };
  assert.deepEqual(await page.inspect(), { activeView: "library" });
  result = { activeView: "review" };
  assert.deepEqual(await page.inspect(), { activeView: "review" });
  await page.click('.workspace-nav-item[aria-label^="当前任务"]');
  await page.fill("#library-query", "南海");
  await page.press("#library-query", "Enter");
  await page.clickPoint({ x: 12, y: 20 });
  await page.setViewport({ width: 390, height: 844, mobile: true });
  assert.deepEqual(operations.at(-1), ["cdp.send", "Emulation.setDeviceMetricsOverride", {
    width: 390,
    height: 844,
    deviceScaleFactor: 1,
    mobile: true,
  }]);
  assert.ok(operations.some(([action]) => action === "locator.click"));
  assert.ok(operations.some(([action]) => action === "locator.fill"));
  assert.ok(operations.some(([action]) => action === "locator.press"));
  assert.ok(operations.some(([action]) => action === "cua.click"));
  for (const [action, input] of operations.filter(([kind]) => kind === "evaluate")) {
    assert.equal(action, "evaluate");
    assert.equal(typeof input, "string");
    assert.doesNotMatch(input, /dispatchEvent|\.click\s*\(/u);
  }
});

test("browser skill adapter accepts the in-app browser's separate viewport capability", async () => {
  const operations = [];
  const tab = {
    async goto() {},
    playwright: {
      locator() { return {}; },
      async evaluate() { return JSON.stringify({ activeView: "search" }); },
    },
  };
  const viewport = {
    async set(size) { operations.push(["viewport.set", size]); },
  };

  const page = createBrowserSkillPage(tab, { viewport });
  await page.setViewport({ width: 390, height: 844, mobile: true });
  assert.deepEqual(operations, [["viewport.set", { width: 390, height: 844 }]]);
  assert.throws(() => createBrowserSkillPage(tab), /viewport capability/u);
  assert.throws(() => page.clickPoint({ x: 2, y: 2 }), /cua\.click/u);
});

test("browser skill adapter rejects unavailable interfaces and malformed readonly evaluation", async () => {
  assert.throws(() => createBrowserSkillPage({}), /playwright\.locator\/evaluate/u);
  const page = createBrowserSkillPage({
    async goto() {},
    playwright: { locator() {}, async evaluate() { return null; } },
    cdp: { async send() {} },
  });
  await assert.rejects(page.inspect(), /invalid or empty/u);
});

test("seeded browser protocol executes all 28 checks, 28 layouts and no destructive actions", async () => {
  const page = fakePage();
  const progress = [];
  const result = await runBrowserProbes(options(page, {
    requireScientificApproximation: true,
    onProgress: (event) => progress.push(event),
  }));

  assert.equal(result.passed, true);
  assert.equal(result.schema_version, USER_INTERFACE_ACCEPTANCE_SCHEMA);
  assert.equal(result.total_checks, 28);
  assert.equal(result.completed_checks, 28);
  assert.equal(result.blocked_checks, 0);
  assert.equal(result.responsive_cases, 28);
  assert.equal(result.results.length, 28);
  assert.equal(progress.length, 28);
  assert.ok(result.results.every((item) => item.status === "passed"));
  assert.equal(result.results.find((item) => item.id === "UI-001").details.workspace_state, "ready");
  assert.equal(result.results.find((item) => item.id === "UI-012").details.synthesis_paper_options, 3);
  assert.equal(result.results.find((item) => item.id === "UI-017").details.page, 9);
  assert.deepEqual(result.results.find((item) => item.id === "UI-020").details.approximate_samples, ["2400~3450", "~95%"]);
  assert.equal(result.results.find((item) => item.id === "UI-023").details.cases.length, 28);
  assert.equal(result.results.find((item) => item.id === "UI-025").details.returned_view, "knowledgeGraph");
  assert.equal(result.results.find((item) => item.id === "UI-028").details.returned_view, "search");
  assert.equal(page.state.view, "search");
  assert.deepEqual(page.operations.filter((operation) => operation.action === "viewport").map((item) => item.width), [390, 768, 1440, 1024]);

  for (const operation of page.operations.filter((item) => ["click", "press"].includes(item.action))) {
    assert.doesNotMatch(operation.selector, /compile-button|danger|delete|upload|normalize|comparison/u);
  }
});

test("browser acceptance waits for post-hydration ready state before the first real click", async () => {
  const page = fakePage({ hydrationInspections: 2 });
  const result = await runBrowserProbes(options(page, { settleAttempts: 3 }));

  assert.equal(result.passed, true);
  assert.equal(result.results[0].details.workspace_state, "ready");
  const firstClick = page.operations.findIndex((item) => item.action === "click");
  assert.ok(firstClick > 0);
  assert.ok(page.operations.slice(0, firstClick).filter((item) => item.action === "inspect").length >= 3);
  assert.equal(page.operations[firstClick].selector, '.workspace-nav-item[aria-label^="文献库"]');
});

test("server-rendered search markup cannot pass acceptance before workspace hydration", async () => {
  const page = fakePage({ broken: "neverHydrated" });

  await assert.rejects(runBrowserProbes(options(page)), (error) => {
    assert.ok(error instanceof BrowserProbeError);
    assert.equal(error.checkId, "UI-001");
    assert.match(error.message, /Hydrated and interactive search workspace/u);
    assert.equal(error.report.results.at(-1).status, "failed");
    return true;
  });
  assert.equal(page.operations.some((item) => item.action === "click"), false);
});

test("task navigation is exercised as a normal workspace destination", async () => {
  const page = fakePage();
  const result = await runBrowserProbes(options(page));

  assert.equal(result.passed, true);
  assert.ok(page.operations.some((item) => (
    item.action === "click" && item.selector === '.workspace-nav-item[aria-label^="当前任务"]'
  )));
  assert.equal(result.results.find((item) => item.id === "UI-007").details.modal, false);
});

test("browser acceptance refuses to pretend an unseeded corpus was tested", async () => {
  await failsAt("", "UI-010", /at least one completed, seeded PDF/u, { completedRows: 0 });
});

test("browser acceptance catches missing keyboard focus movement in both tabsets", async () => {
  await failsAt("workflowKeyboard", "UI-006", /Keyboard focus did not move/u);
  await failsAt("paperKeyboard", "UI-016", /Keyboard focus did not move/u);
});

test("browser acceptance catches broken task workspace navigation", async () => {
  await failsAt("taskClick", "UI-003", /任务 navigation/u);
  await failsAt("taskKeyboard", "UI-008", /Keyboard activity navigation/u);
  await failsAt("taskReturn", "UI-009", /Leaving activity workspace/u);
  await failsAt("taskDialog", "UI-007", /still renders drawer controls/u);
});

test("browser acceptance catches removed library actions and global corpus contamination", async () => {
  await failsAt("legacyLibraryActions", "UI-010", /still exposes removed management actions/u);
  await failsAt("corpusLeak", "UI-012", /replaced the global synthesis corpus \(0\/3\)/u);
});

test("browser acceptance independently compares the iframe page with its source anchor", async () => {
  await failsAt("pdfPage", "UI-017", /source page 9/u);
  await failsAt("unsafeOriginalLink", "UI-018", /isolated link/u);
});

test("browser acceptance catches PDF modal keyboard escapes and incorrect restored focus", async () => {
  await failsAt("pdfTrap", "UI-018", /backward focus wrap/u);
  await failsAt("pdfReturn", "UI-019", /focus restoration/u);
  await failsAt("pdfButton", "UI-019", /button close/u);
  await failsAt("pdfBackdrop", "UI-019", /backdrop close/u);
});

test("browser acceptance catches graph provenance and actual scope or Claim cleanup failures", async () => {
  await failsAt("graphPath", "UI-025", /subfield column/u);
  await failsAt("graphReturn", "UI-025", /real knowledge-graph source/u);
  await failsAt("scopeBrand", "UI-026", /clears the real OpenAlex scope/u);
  await failsAt("claimBrand", "UI-027", /clears a real prior Claim seed/u);
});

test("readonly browser run reports unavailable existing search results as blocked, never passed", async () => {
  const page = fakePage({ existingSearchResults: false });
  const result = await runBrowserProbes(options(page));
  assert.equal(result.passed, false);
  assert.equal(result.allow_model_interactions, false);
  assert.equal(result.blocked_checks, 1);
  assert.equal(result.results.find((item) => item.id === "UI-028").status, "blocked");
  assert.equal(result.results.find((item) => item.id === "UI-023").status, "passed");
  assert.equal(result.results.find((item) => item.id === "UI-024").status, "passed");
  assert.equal(page.operations.some((item) => item.selector === '.evidence-search .search-form button[type="submit"]'), false);
});

test("only explicit authorization performs one genuine search to verify result-origin return", async () => {
  const page = fakePage({ existingSearchResults: false });
  const result = await runBrowserProbes(options(page, {
    allowModelInteractions: true,
    modelSettleAttempts: 2,
    modelSettleDelayMs: 0,
  }));
  assert.equal(result.passed, true);
  assert.equal(result.allow_model_interactions, true);
  assert.equal(result.blocked_checks, 0);
  assert.equal(result.results.find((item) => item.id === "UI-028").details.explicitly_authorized_model_search, true);
  assert.equal(page.operations.filter((item) => item.selector === '.evidence-search .search-form button[type="submit"]').length, 1);
  assert.equal(page.state.modelSubmitted, true);
});

test("explicitly authorized search fails immediately on provider errors, degradation or absent results", async () => {
  for (const [broken, pattern] of [
    ["modelFailure", /provider failure or degraded/u],
    ["modelDegraded", /provider failure or degraded/u],
    ["modelNoResults", /no completed evidence result/u],
  ]) {
    const page = fakePage({ broken, existingSearchResults: false });
    await assert.rejects(runBrowserProbes(options(page, {
      allowModelInteractions: true,
      modelSettleAttempts: 2,
      modelSettleDelayMs: 0,
    })), (error) => {
      assert.equal(error.checkId, "UI-028");
      assert.match(error.message, pattern);
      return true;
    });
  }
});

test("browser acceptance rejects executable HTML, unsafe science links and numeric deletion", async () => {
  await failsAt("xss", "UI-020", /executable or injected HTML/u);
  await failsAt("unsafeScientificLink", "UI-020", /dangerous scheme or unsafe external opener/u);
  await failsAt("numericDeletion", "UI-020", /numeric scientific value/u);
});

test("browser acceptance does not claim numeric-tilde coverage without observing a real example", async () => {
  const page = fakePage({ approximations: [] });
  await assert.rejects(runBrowserProbes(options(page, { requireScientificApproximation: true })), (error) => {
    assert.equal(error.checkId, "UI-020");
    assert.match(error.message, /no observable numeric single-tilde case/u);
    return true;
  });
});

test("browser acceptance catches incorrect review filters and true mobile overflow", async () => {
  await failsAt("reviewFilter", "UI-022", /human-review filter/u);
  await failsAt("overflow", "UI-023", /overflows horizontally by 24px at 390px/u);
  await failsAt("modalOverflow", "UI-023", /clipped outside/u);
});

test("browser acceptance validates base URLs and remains bounded before visiting anything", async () => {
  for (const baseUrl of ["", "mailto:test@example.test", "http://example.test", "https://user:password@example.test", "https://example.test/?token=secret"]) {
    const page = fakePage();
    await assert.rejects(runBrowserProbes(options(page, { baseUrl })), /baseUrl|HTTPS|HTTP\(S\)/u);
    assert.equal(page.operations.length, 0);
  }
  await assert.rejects(runBrowserProbes(options(fakePage(), { settleAttempts: 41 })), /bounded/u);
  await assert.rejects(runBrowserProbes(options(fakePage(), { settleDelayMs: 3000 })), /bounded/u);
  await assert.rejects(runBrowserProbes(options(fakePage(), { modelSettleAttempts: 81 })), /60-second/u);
  await assert.rejects(runBrowserProbes(options(fakePage(), { modelSettleAttempts: 80, modelSettleDelayMs: 1000 })), /60-second/u);
  await assert.rejects(runBrowserProbes(options(fakePage(), { noMatchQuery: "x".repeat(181) })), /180/u);
  await assert.rejects(runBrowserProbes(options(fakePage(), { modelSearchQuery: "x".repeat(181) })), /180/u);

  const local = await runBrowserProbes(options(fakePage(), {
    baseUrl: "http://127.0.0.1:8787",
    viewports: [{ width: 1024, height: 480 }],
  }));
  assert.equal(local.passed, true);
  assert.equal(local.responsive_cases, 7);
});

test("browser acceptance propagates precise failing IDs and never reports later checks", async () => {
  const page = fakePage({ broken: "language" });
  const progress = [];
  await assert.rejects(runBrowserProbes(options(page, { onProgress: (item) => progress.push(item) })), (error) => {
    assert.equal(error.checkId, "UI-002");
    assert.deepEqual(error.inventoryIds, ["WS-012"]);
    assert.equal(error.report.completed_checks, 2);
    assert.deepEqual(error.report.results.map((item) => item.status), ["passed", "failed"]);
    return true;
  });
  assert.deepEqual(progress.map((item) => item.status), ["passed", "failed"]);
});
