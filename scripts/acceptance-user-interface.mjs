/**
 * Re-runnable, non-destructive browser acceptance for the visible DeepShovel UI.
 *
 * This module deliberately has no browser-package dependency. An operator can
 * supply any compatible page adapter, including createBrowserSkillPage(tab).
 * All user interaction goes through a real locator; page evaluation is a single
 * bounded, read-only inspection of visible document state.
 */

export const USER_INTERFACE_ACCEPTANCE_SCHEMA = "deepshovel-user-interface-acceptance/1.0";

const DEFAULT_VIEWPORTS = Object.freeze([
  Object.freeze({ width: 390, height: 844, mobile: true }),
  Object.freeze({ width: 768, height: 1024, mobile: false }),
  Object.freeze({ width: 1440, height: 900, mobile: false }),
  Object.freeze({ width: 1024, height: 480, mobile: false }),
]);

const NAVIGATION = Object.freeze([
  Object.freeze({ view: "library", label: "文献库", selector: '.workspace-nav-item[aria-label^="文献库"]' }),
  Object.freeze({ view: "knowledgeGraph", label: "证据图谱", selector: '.workspace-nav-item[aria-label^="证据图谱"]' }),
  Object.freeze({ view: "review", label: "人工复核", selector: '.workspace-nav-item[aria-label^="人工复核"]' }),
  Object.freeze({ view: "search", label: "观点校验", selector: '.workspace-nav-item[aria-label^="观点校验"]' }),
  Object.freeze({ view: "activity", label: "当前任务", selector: '.workspace-nav-item[aria-label^="当前任务"]' }),
]);

const CHECKS = Object.freeze([
  { id: "UI-001", name: "首页默认进入观点校验", inventoryIds: ["WS-001"] },
  { id: "UI-002", name: "中文语言、标题和图标", inventoryIds: ["WS-012"] },
  { id: "UI-003", name: "五个主导航入口与当前页语义", inventoryIds: ["WS-003", "REV-001", "WS-007"] },
  { id: "UI-004", name: "品牌按钮返回全局检索", inventoryIds: ["WS-002"] },
  { id: "UI-005", name: "两个检索工作流标签和面板", inventoryIds: ["WS-004"] },
  { id: "UI-006", name: "工作流 ArrowLeft、ArrowRight、Home、End", inventoryIds: ["WS-005"] },
  { id: "UI-007", name: "当前任务作为主工作区页面打开并提供任务状态筛选", inventoryIds: ["WS-007"] },
  { id: "UI-008", name: "键盘进入当前任务页面并保留触发焦点", inventoryIds: ["WS-008"] },
  { id: "UI-009", name: "当前任务页面可正常切换至其他主工作区", inventoryIds: ["WS-007", "WS-008"] },
  { id: "UI-010", name: "文献主页面只读管理入口（公网不可删除）", inventoryIds: ["LIB-002", "LIB-005"] },
  { id: "UI-011", name: "文件名筛选与真实无命中空状态", inventoryIds: ["LIB-003", "LIB-011"] },
  { id: "UI-012", name: "局部文献筛选不能污染论文综合全局语料", inventoryIds: ["LIB-013"] },
  { id: "UI-013", name: "清除筛选恢复原有完成论文", inventoryIds: ["LIB-003"] },
  { id: "UI-014", name: "从文献库打开真实论文证据", inventoryIds: ["LIB-009", "PAP-001"] },
  { id: "UI-015", name: "论文详情三个标签及关联面板", inventoryIds: ["PAP-003"] },
  { id: "UI-016", name: "论文详情 ArrowLeft、ArrowRight、Home、End", inventoryIds: ["PAP-003"] },
  { id: "UI-017", name: "原文 modal 的 SHA、实际 1-based 页码和滚动锁", inventoryIds: ["PDF-005"] },
  { id: "UI-018", name: "PDF modal Tab/Shift+Tab 焦点边界与同源外链", inventoryIds: ["PDF-007"] },
  { id: "UI-019", name: "PDF 按钮、Escape、背景关闭及焦点恢复", inventoryIds: ["PDF-006"] },
  { id: "UI-020", name: "科学内容近似符号、危险元素和外链隔离", inventoryIds: ["PDF-003", "PDF-014"] },
  { id: "UI-021", name: "论文详情返回真实来源文献库", inventoryIds: ["PAP-002"] },
  { id: "UI-022", name: "人工复核筛选状态", inventoryIds: ["REV-001", "REV-002"] },
  { id: "UI-025", name: "四级现有分类、Claim 图谱来源详情与返回", inventoryIds: ["KG-005", "KG-015", "KG-018", "PAP-001", "PAP-002"] },
  { id: "UI-026", name: "分类范围建立、单独移除和品牌清空", inventoryIds: ["KG-006", "RET-007", "RET-014", "WS-002"] },
  { id: "UI-027", name: "真实 Claim 种子建立、单独移除和品牌清空", inventoryIds: ["RET-005", "RET-014", "WS-002"] },
  { id: "UI-028", name: "真实检索结果来源详情与返回；只读无结果时明确阻塞", inventoryIds: ["PAP-001", "PAP-002"] },
  { id: "UI-023", name: "手机、平板、桌面及低高度六界面无横向溢出", inventoryIds: ["WS-010", "PDF-005"] },
  { id: "UI-024", name: "验收完成后回到观点校验首页", inventoryIds: ["WS-001"] },
].map((item) => Object.freeze({ ...item, inventoryIds: Object.freeze(item.inventoryIds) })));

/** This function is stringified and evaluated as a read-only DOM inspection. */
function inspectVisibleDocument() {
  const visible = (element) => Boolean(
    element
      && !element.closest("[hidden]")
      && !element.hidden
      && element.getClientRects().length,
  );
  const label = (element) => element?.textContent?.trim().slice(0, 160) ?? "";
  const selectorFor = (element) => {
    if (!(element instanceof Element)) return "";
    const segments = [];
    let current = element;
    while (current && current !== document.documentElement && segments.length < 12) {
      if (current.id) {
        segments.unshift(`#${CSS.escape(current.id)}`);
        break;
      }
      const siblings = current.parentElement
        ? Array.from(current.parentElement.children).filter((sibling) => sibling.tagName === current.tagName)
        : [];
      segments.unshift(`${current.tagName.toLowerCase()}:nth-of-type(${siblings.indexOf(current) + 1})`);
      current = current.parentElement;
    }
    return segments.join(" > ");
  };
  const focusablesIn = (root, includeFrames = false) => {
    if (!root) return [];
    const selector = includeFrames
      ? 'button:not([disabled]),a[href],iframe,[tabindex]:not([tabindex="-1"])'
      : 'button:not([disabled]),a[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
    return Array.from(root.querySelectorAll(selector)).filter(visible).map(selectorFor);
  };
  const backdropPoint = (layer, panel) => {
    if (!visible(layer) || !visible(panel)) return null;
    const bounds = panel.getBoundingClientRect();
    const candidates = [
      { x: Math.floor(bounds.left / 2), y: Math.floor(window.innerHeight / 2) },
      { x: Math.floor((bounds.right + window.innerWidth) / 2), y: Math.floor(window.innerHeight / 2) },
      { x: Math.floor(window.innerWidth / 2), y: Math.floor(bounds.top / 2) },
      { x: Math.floor(window.innerWidth / 2), y: Math.floor((bounds.bottom + window.innerHeight) / 2) },
    ];
    return candidates.find(({ x, y }) => x > 1 && y > 1
      && x < window.innerWidth - 1 && y < window.innerHeight - 1
      && (x < bounds.left || x > bounds.right || y < bounds.top || y > bounds.bottom)) ?? null;
  };
  const workflowTabs = Array.from(document.querySelectorAll('.search-local-tabs [role="tab"]')).map((tab) => {
    const panel = document.getElementById(tab.getAttribute("aria-controls") ?? "");
    return {
      id: tab.id,
      selected: tab.getAttribute("aria-selected") === "true",
      tabIndex: tab.tabIndex,
      controls: tab.getAttribute("aria-controls") ?? "",
      panelExists: Boolean(panel),
      panelHidden: panel?.hidden ?? true,
    };
  });
  const paperTabs = Array.from(document.querySelectorAll('.paper-tabs [role="tab"]')).map((tab) => ({
    id: tab.id,
    selected: tab.getAttribute("aria-selected") === "true",
    tabIndex: tab.tabIndex,
    controls: tab.getAttribute("aria-controls") ?? "",
    panelExists: Boolean(document.getElementById(tab.getAttribute("aria-controls") ?? "")),
  }));
  const activityView = document.querySelector(".activity-view");
  const pdf = document.querySelector('.pdf-preview-dialog[role="dialog"]');
  const rows = Array.from(document.querySelectorAll(".library-table article.library-row"));
  const evidenceTrigger = Array.from(document.querySelectorAll(".library-actions > button"))
    .find((button) => visible(button) && label(button) === "打开证据");
  const pageTrigger = Array.from(document.querySelectorAll(".paper-view button.pdf-preview-trigger"))
    .find((button) => visible(button) && label(button) === "打开原始页");
  const pageLabel = label(pageTrigger?.closest(".source-anchor-content")?.querySelector(".source-anchor-heading > span"));
  const pageMatch = pageLabel.match(/第\s*(\d+)\s*页/u);
  const scientificNodes = Array.from(document.querySelectorAll(".scientific-content"))
    .filter(visible)
    .slice(0, 160);
  const approximateSamples = scientificNodes.flatMap((node) => (
    (node.textContent ?? "").match(/(?:\d+(?:\.\d+)?\s*~\s*\d+(?:\.\d+)?|~\s*\d+(?:\.\d+)?%?)/gu) ?? []
  )).slice(0, 16);
  const deletedNumericCount = scientificNodes.reduce(
    (count, node) => count + Array.from(node.querySelectorAll("del")).filter((item) => /\d/u.test(item.textContent ?? "")).length,
    0,
  );
  const forbiddenElements = scientificNodes.reduce(
    (count, node) => count + node.querySelectorAll("script,iframe,object,embed,img,[onerror],[onload],[onclick]").length,
    0,
  );
  const scientificLinks = scientificNodes.flatMap((node) => Array.from(node.querySelectorAll("a[href]")));
  const dangerousLinks = scientificLinks.filter((link) => (
    /^(?:javascript|vbscript|data):/iu.test((link.getAttribute("href") ?? "").trim())
      || link.getAttribute("target") !== "_blank"
      || !/(?:^|\s)noopener(?:\s|$)/u.test(link.getAttribute("rel") ?? "")
      || !/(?:^|\s)noreferrer(?:\s|$)/u.test(link.getAttribute("rel") ?? "")
  )).length;
  const documentElement = document.documentElement;
  const active = document.activeElement;
  const frame = pdf?.querySelector("iframe");
  const pdfLink = pdf?.querySelector("a[href]");
  const sourceSelect = document.querySelector("#search-task-panel-paper .synthesis-source-control select");
  const taxonomyColumns = Array.from(document.querySelectorAll(".taxonomy-column[data-level]")).map((column) => {
    const entry = column.querySelector("article.taxonomy-entry");
    return {
      level: column.getAttribute("data-level") ?? "",
      firstMainSelector: selectorFor(entry?.querySelector("button.taxonomy-entry-main")),
      firstSearchSelector: selectorFor(entry?.querySelector("button.taxonomy-entry-search")),
    };
  });
  const claimEntries = Array.from(document.querySelectorAll("article.taxonomy-claim-entry"));
  const selectedClaim = claimEntries.find((entry) => entry.getAttribute("data-state") === "current");
  const usefulClaim = selectedClaim
    ?? claimEntries.find((entry) => Array.from(label(entry.querySelector("em")).matchAll(/\d+/gu))
      .some((match) => Number(match[0]) > 0))
    ?? claimEntries[0];
  const neighborhoodEvidence = Array.from(document.querySelectorAll(".neighborhood-node > button.text-button"))
    .find((button) => visible(button) && label(button) === "查看证据来源");
  const compareTrigger = Array.from(document.querySelectorAll(".paper-view .claim-actions > button"))
    .find((button) => visible(button) && label(button) === "查找支持与反例");
  const searchResult = Array.from(document.querySelectorAll(".preview-result button,.proposition-cluster button,.cluster-member button"))
    .find((button) => visible(button) && ["查看论文完整证据", "查看代表论文", "查看论文证据"].includes(label(button)));
  const pdfBounds = pdf?.getBoundingClientRect();

  return {
    metadata: {
      lang: documentElement.lang,
      title: document.title.slice(0, 160),
      hasIcon: Boolean(document.querySelector('link[rel~="icon"]')),
    },
    readiness: {
    },
    viewport: {
      width: window.innerWidth,
      height: window.innerHeight,
      clientWidth: documentElement.clientWidth,
      scrollWidth: documentElement.scrollWidth,
      horizontalOverflow: Math.max(0, documentElement.scrollWidth - documentElement.clientWidth),
    },
    activeView: document.querySelector(".app-stage")?.getAttribute("data-active-view") ?? "",
    navigation: Array.from(document.querySelectorAll(".workspace-nav-item")).map((item) => ({
      label: item.getAttribute("aria-label") ?? "",
      current: item.getAttribute("aria-current") === "page",
    })),
    focus: {
      id: active?.id ?? "",
      selector: selectorFor(active),
      taskButton: Boolean(active?.matches?.('.workspace-nav-item[aria-label^="当前任务"]')),
      insidePdf: Boolean(pdf?.contains(active)),
    },
    workflow: {
      tabs: workflowTabs,
      selected: workflowTabs.find((item) => item.selected)?.id ?? "",
      paperOptionCount: sourceSelect?.options.length ?? 0,
    },
    activity: {
      present: visible(activityView),
      title: label(activityView?.querySelector("#activity-view-title")),
      status: label(activityView?.querySelector('.status-tabs button[aria-pressed="true"]')),
      filterLabels: Array.from(activityView?.querySelectorAll(".status-tabs button") ?? []).map(label),
      hasUploadButton: Boolean(Array.from(activityView?.querySelectorAll(".activity-header-actions button") ?? []).find((item) => label(item) === "上传文献")),
      dialogCount: document.querySelectorAll('.activity-drawer,[aria-label="关闭任务面板"]').length,
    },
    library: {
      present: Boolean(document.querySelector(".library-view")),
      loading: Boolean(document.querySelector(".library-view .view-loading")),
      legacyActionLabels: Array.from(document.querySelectorAll(".library-view button"))
        .map(label)
        .filter((value) => ["待 Compile", "查看待 Compile", "删除全部文献", "删除选中", "彻底删除文献"].includes(value)),
      queryValue: document.querySelector("#library-query")?.value ?? "",
      hasClearButton: Boolean(Array.from(document.querySelectorAll(".library-toolbar > button")).find((item) => label(item) === "清除筛选")),
      rowCount: rows.length,
      emptyTitle: label(document.querySelector(".library-view .view-empty h2")),
      emptyRecoveryLabels: Array.from(document.querySelectorAll(".library-view .view-empty .empty-actions button")).map(label),
      evidenceTriggerSelector: selectorFor(evidenceTrigger),
    },
    paper: {
      present: Boolean(document.querySelector(".paper-view")),
      title: label(document.querySelector("#paper-title")),
      tabs: paperTabs,
      selected: paperTabs.find((item) => item.selected)?.id ?? "",
      pdfTriggerSelector: selectorFor(pageTrigger),
      pdfTriggerPage: pageMatch ? Number(pageMatch[1]) : null,
      compareTriggerSelector: selectorFor(compareTrigger),
    },
    pdf: {
      open: Boolean(pdf),
      modal: pdf?.getAttribute("aria-modal") === "true",
      title: label(pdf?.querySelector("h2")),
      frameSrc: frame?.getAttribute("src") ?? "",
      frameTitle: frame?.getAttribute("title") ?? "",
      fullSourceHref: pdfLink?.getAttribute("href") ?? "",
      fullSourceSafe: Boolean(pdfLink && pdfLink.getAttribute("target") === "_blank"
        && /(?:^|\s)noopener(?:\s|$)/u.test(pdfLink.getAttribute("rel") ?? "")
        && /(?:^|\s)noreferrer(?:\s|$)/u.test(pdfLink.getAttribute("rel") ?? "")),
      scrollLocked: documentElement.style.overflow === "hidden",
      focusableSelectors: focusablesIn(pdf, true),
      backdropPoint: backdropPoint(document.querySelector(".pdf-preview-layer"), pdf),
      bounds: pdfBounds ? {
        left: pdfBounds.left,
        top: pdfBounds.top,
        right: pdfBounds.right,
        bottom: pdfBounds.bottom,
      } : null,
    },
    review: {
      present: Boolean(document.querySelector(".review-view")),
      selected: label(document.querySelector('.review-filter button[aria-pressed="true"]')),
      filters: Array.from(document.querySelectorAll(".review-filter button")).map(label),
      empty: Boolean(document.querySelector(".review-view .queue-empty")),
      emptyText: label(document.querySelector(".review-view .queue-empty")),
    },
    graph: {
      present: Boolean(document.querySelector(".knowledge-graph-view")),
      loading: Boolean(document.querySelector(".knowledge-graph-view .view-loading,.taxonomy-loading")),
      taxonomyColumns,
      claimCount: claimEntries.length,
      claimSelector: selectorFor(usefulClaim?.querySelector("button.taxonomy-entry-main")),
      claimEvidenceSelector: selectorFor(usefulClaim?.querySelector(".taxonomy-entry-actions > button:first-of-type")),
      neighborhoodPresent: Boolean(document.querySelector(".neighborhood-section")),
      neighborhoodEvidenceSelector: selectorFor(neighborhoodEvidence),
      edgeCount: document.querySelectorAll(".neighborhood-edge-ledger li,.neighborhood-edge").length,
      resetSelector: selectorFor(document.querySelector(".taxonomy-ledger-header > button")),
    },
    search: {
      claimSeed: Boolean(document.querySelector('.query-claim-chip[aria-label="当前检索起点"]')),
      taxonomyScope: Boolean(document.querySelector('.taxonomy-scope-chip[aria-label="OpenAlex 检索范围"]')),
      existingResultSelector: selectorFor(searchResult),
      busy: document.querySelector('.ai-results[aria-busy="true"]') !== null
        || /正在召回|关系精排中/u.test(label(document.querySelector('.search-form button[type="submit"]'))),
      error: label(document.querySelector('.evidence-search [role="alert"],.evidence-search .error-message,.evidence-search .search-error')),
      degraded: Boolean(document.querySelector(".evidence-search .ai-stage-error")),
      previewCount: document.querySelectorAll(".preview-result").length,
      resultCount: document.querySelectorAll(".proposition-cluster,.cluster-member").length,
    },
    scientific: {
      nodeCount: scientificNodes.length,
      approximateSamples,
      deletedNumericCount,
      forbiddenElementCount: forbiddenElements,
      dangerousLinkCount: dangerousLinks,
    },
  };
}

export function browserReadExpression() {
  return `JSON.stringify((${inspectVisibleDocument.toString()})())`;
}

function decodeBrowserEvaluation(value) {
  if (typeof value === "string") return JSON.parse(value);
  if (value && typeof value === "object" && "result" in value) {
    return decodeBrowserEvaluation(value.result);
  }
  if (value && typeof value === "object" && !Array.isArray(value)) return value;
  throw new TypeError("Browser DOM inspection returned an invalid or empty response");
}

/** Adapt the already-open browser-skill tab without installing Playwright. */
export function createBrowserSkillPage(tab, { viewport } = {}) {
  if (!tab || typeof tab.goto !== "function"
    || typeof tab.playwright?.locator !== "function"
    || typeof tab.playwright?.evaluate !== "function") {
    throw new TypeError("A browser tab must expose goto and playwright.locator/evaluate");
  }
  if (typeof viewport?.set !== "function" && typeof tab.cdp?.send !== "function") {
    throw new TypeError("A browser tab requires a viewport capability or cdp.send");
  }

  return {
    goto: (url) => tab.goto(url),
    inspect: async () => decodeBrowserEvaluation(
      await tab.playwright.evaluate(browserReadExpression()),
    ),
    click: (selector) => tab.playwright.locator(selector).click(),
    clickPoint: ({ x, y }) => {
      if (typeof tab.cua?.click !== "function") {
        throw new TypeError("The browser tab requires cua.click for real modal-backdrop pointer interaction");
      }
      return tab.cua.click({ x, y });
    },
    fill: (selector, value) => tab.playwright.locator(selector).fill(value),
    press: (selector, key) => tab.playwright.locator(selector).press(key),
    setViewport: ({ width, height, mobile }) => (
      typeof viewport?.set === "function"
        ? viewport.set({ width, height })
        : tab.cdp.send("Emulation.setDeviceMetricsOverride", {
          width,
          height,
          deviceScaleFactor: 1,
          mobile: Boolean(mobile),
        })
    ),
  };
}

function checkedViewports(viewports = DEFAULT_VIEWPORTS) {
  if (!Array.isArray(viewports) || viewports.length < 1 || viewports.length > 8) {
    throw new TypeError("Provide between one and eight browser viewport cases");
  }
  return viewports.map((item) => {
    if (!item || !Number.isSafeInteger(item.width) || !Number.isSafeInteger(item.height)
      || item.width < 240 || item.width > 3840 || item.height < 320 || item.height > 2160) {
      throw new TypeError("Viewport width/height must stay within bounded, valid device dimensions");
    }
    return { width: item.width, height: item.height, mobile: Boolean(item.mobile ?? item.width < 600) };
  });
}

export function buildBrowserProbePlan({ viewports, allowModelInteractions = false } = {}) {
  if (typeof allowModelInteractions !== "boolean") {
    throw new TypeError("allowModelInteractions must be explicitly true or false");
  }
  const devices = checkedViewports(viewports);
  return {
    schema_version: USER_INTERFACE_ACCEPTANCE_SCHEMA,
    non_mutating: !allowModelInteractions,
    prohibited_actions: ["compile", ...(allowModelInteractions ? [] : ["model_request"]), "upload", "delete", "review_submission"],
    checks: CHECKS.map((item) => ({ ...item, inventoryIds: [...item.inventoryIds] })),
    viewports: devices,
    responsive_cases: devices.flatMap((device) => [...NAVIGATION.map((item) => item.view), "paper", "pdf_modal"].map((view) => ({
      width: device.width,
      height: device.height,
      view,
    }))),
  };
}

function checkedBaseUrl(value) {
  if (typeof value !== "string" || !value.trim()) throw new TypeError("A deployed baseUrl is required");
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new TypeError("baseUrl must be a valid HTTP(S) URL");
  }
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
    throw new TypeError("baseUrl must use HTTP(S) without credentials, query or fragment");
  }
  const loopback = url.hostname === "localhost" || url.hostname === "127.0.0.1" || url.hostname === "[::1]";
  if (url.protocol === "http:" && !loopback) throw new TypeError("A public browser baseUrl must use HTTPS");
  return url.toString().replace(/\/$/u, "");
}

function ensurePage(page) {
  for (const method of ["goto", "inspect", "click", "clickPoint", "fill", "press", "setViewport"]) {
    if (typeof page?.[method] !== "function") throw new TypeError(`Browser page is missing ${method}()`);
  }
}

function requireCondition(value, message) {
  if (!value) throw new Error(message);
}

function requireActiveView(snapshot, view, label) {
  requireCondition(snapshot.activeView === view, `Expected ${view} view, received ${snapshot.activeView || "no active view"}`);
  const selected = snapshot.navigation.filter((item) => item.current);
  requireCondition(selected.length === 1 && selected[0].label.startsWith(label), `${label} has no unique aria-current navigation entry`);
}

function requireWorkflow(snapshot, id, { focused = false } = {}) {
  const target = `search-task-tab-${id}`;
  requireCondition(snapshot.workflow.tabs.length === 2, "The workflow must expose exactly two accessible tabs");
  requireCondition(snapshot.workflow.selected === target, `Expected workflow tab ${target}`);
  for (const tab of snapshot.workflow.tabs) {
    const active = tab.id === target;
    requireCondition(tab.selected === active && tab.tabIndex === (active ? 0 : -1), `Workflow tab ${tab.id} has inconsistent selection/tabIndex`);
    requireCondition(tab.panelExists && tab.panelHidden === !active, `Workflow tab ${tab.id} has an inconsistent tabpanel`);
  }
  if (focused) requireCondition(snapshot.focus.id === target, `Keyboard focus did not move to ${target}`);
}

function requirePaperTab(snapshot, id, { focused = false } = {}) {
  const target = `paper-tab-${id}`;
  requireCondition(snapshot.paper.tabs.length === 3 && snapshot.paper.selected === target, `Expected paper detail tab ${target}`);
  for (const tab of snapshot.paper.tabs) {
    const active = tab.id === target;
    requireCondition(tab.selected === active && tab.tabIndex === (active ? 0 : -1), `Paper tab ${tab.id} has inconsistent selection/tabIndex`);
    requireCondition(tab.panelExists === active, `Paper tab ${tab.id} has an inconsistent mounted tabpanel`);
  }
  if (focused) requireCondition(snapshot.focus.id === target, `Keyboard focus did not move to ${target}`);
}

export class BrowserProbeError extends Error {
  constructor(check, cause, report) {
    super(`${check.id} ${check.name}: ${cause instanceof Error ? cause.message : String(cause)}`);
    this.name = "BrowserProbeError";
    this.checkId = check.id;
    this.inventoryIds = [...check.inventoryIds];
    this.report = report;
    this.cause = cause;
  }
}

class BrowserProbeBlocked extends Error {
  constructor(message) {
    super(message);
    this.name = "BrowserProbeBlocked";
  }
}

/**
 * Execute all registered checks against an existing, seeded browser session.
 * Throws immediately on the first failed check: a missing observation is never
 * silently reclassified as passing or skipped.
 */
export async function runBrowserProbes({
  page,
  baseUrl,
  viewports,
  noMatchQuery = "__deepshovel_browser_probe_no_match_9c32__",
  settleAttempts = 12,
  settleDelayMs = 80,
  allowModelInteractions = false,
  modelSettleAttempts = 60,
  modelSettleDelayMs = 900,
  modelSearchQuery = "深海沉积物 稀土 REY",
  requireScientificApproximation = false,
  onProgress,
} = {}) {
  ensurePage(page);
  const origin = checkedBaseUrl(baseUrl);
  const plan = buildBrowserProbePlan({ viewports, allowModelInteractions });
  if (typeof noMatchQuery !== "string" || !noMatchQuery.trim() || noMatchQuery.length > 180) {
    throw new TypeError("noMatchQuery must be a nonempty input of at most 180 characters");
  }
  if (!Number.isSafeInteger(settleAttempts) || settleAttempts < 1 || settleAttempts > 40
    || !Number.isSafeInteger(settleDelayMs) || settleDelayMs < 0 || settleDelayMs > 2000) {
    throw new TypeError("DOM settling must remain bounded");
  }
  if (!Number.isSafeInteger(modelSettleAttempts) || modelSettleAttempts < 1 || modelSettleAttempts > 80
    || !Number.isSafeInteger(modelSettleDelayMs) || modelSettleDelayMs < 0 || modelSettleDelayMs > 1000
    || modelSettleAttempts * modelSettleDelayMs > 60_000) {
    throw new TypeError("Model-backed search settling must stay within a bounded 60-second window");
  }
  if (typeof modelSearchQuery !== "string" || !modelSearchQuery.trim() || modelSearchQuery.length > 180) {
    throw new TypeError("modelSearchQuery must be a nonempty input of at most 180 characters");
  }
  if (onProgress !== undefined && typeof onProgress !== "function") {
    throw new TypeError("onProgress must be a function when provided");
  }

  const startedAt = new Date().toISOString();
  const results = [];
  let completedCount = 0;
  let openedTrigger = "";
  let openedPage = 0;
  let approximationSamples = [];

  const report = (passed) => ({
    schema_version: USER_INTERFACE_ACCEPTANCE_SCHEMA,
    base_url: origin,
    started_at: startedAt,
    finished_at: new Date().toISOString(),
    passed: passed && !results.some((item) => item.status !== "passed"),
    allow_model_interactions: allowModelInteractions,
    total_checks: plan.checks.length,
    completed_checks: results.length,
    blocked_checks: results.filter((item) => item.status === "blocked").length,
    responsive_cases: plan.responsive_cases.length,
    results: results.map((item) => ({ ...item, inventory_ids: [...item.inventory_ids] })),
  });

  const inspectUntil = async (predicate, explanation) => {
    let snapshot;
    for (let attempt = 0; attempt < settleAttempts; attempt += 1) {
      snapshot = await page.inspect();
      if (predicate(snapshot)) return snapshot;
      if (attempt + 1 < settleAttempts && settleDelayMs) {
        await new Promise((resolve) => setTimeout(resolve, settleDelayMs));
      }
    }
    throw new Error(`${explanation} did not settle after ${settleAttempts} bounded observations`);
  };

  const perform = async (id, operation) => {
    const check = CHECKS.find((item) => item.id === id);
    if (!check) throw new Error(`Unregistered browser probe ${id}`);
    try {
      const details = await operation();
      results.push({ id, name: check.name, status: "passed", inventory_ids: [...check.inventoryIds], details: details ?? {} });
      await onProgress?.({ id, status: "passed", completed: results.length, total: CHECKS.length });
      return details;
    } catch (cause) {
      if (cause instanceof BrowserProbeBlocked) {
        results.push({
          id,
          name: check.name,
          status: "blocked",
          inventory_ids: [...check.inventoryIds],
          error: cause.message,
        });
        await onProgress?.({ id, status: "blocked", completed: results.length, total: CHECKS.length });
        return null;
      }
      results.push({
        id,
        name: check.name,
        status: "failed",
        inventory_ids: [...check.inventoryIds],
        error: cause instanceof Error ? cause.message : String(cause),
      });
      await onProgress?.({ id, status: "failed", completed: results.length, total: CHECKS.length });
      throw new BrowserProbeError(check, cause, report(false));
    }
  };

  await perform("UI-001", async () => {
    await page.goto(`${origin}/`);
    const snapshot = await inspectUntil(
      (item) => item.activeView === "search" && item.readiness?.workspaceState === "ready",
      "Hydrated and interactive search workspace",
    );
    requireActiveView(snapshot, "search", "观点校验");
    requireWorkflow(snapshot, "search");
    return { active_view: snapshot.activeView, workspace_state: snapshot.readiness.workspaceState };
  });

  await perform("UI-002", async () => {
    const { metadata } = await page.inspect();
    requireCondition(/^zh(?:-|$)/iu.test(metadata.lang), "The document does not identify its Chinese language");
    requireCondition(/DeepShovel/u.test(metadata.title), "The visible document title omits DeepShovel");
    requireCondition(metadata.hasIcon, "The document does not advertise a favicon");
    return { language: metadata.lang };
  });

  await perform("UI-003", async () => {
    for (const destination of NAVIGATION) {
      await page.click(destination.selector);
      const snapshot = await inspectUntil((item) => item.activeView === destination.view, `${destination.label} navigation`);
      requireActiveView(snapshot, destination.view, destination.label);
    }
    return { visited_views: NAVIGATION.map((item) => item.view) };
  });

  await perform("UI-004", async () => {
    await page.click(NAVIGATION[0].selector);
    await inspectUntil((item) => item.activeView === "library", "Library before brand return");
    await page.click("button.brand");
    const snapshot = await inspectUntil((item) => item.activeView === "search", "Brand homepage return");
    requireActiveView(snapshot, "search", "观点校验");
    return { returned_view: snapshot.activeView };
  });

  await perform("UI-005", async () => {
    for (const id of ["paper", "search"]) {
      await page.click(`#search-task-tab-${id}`);
      const snapshot = await inspectUntil((item) => item.workflow.selected === `search-task-tab-${id}`, `${id} workflow panel`);
      requireWorkflow(snapshot, id);
    }
    return { tabs: ["paper", "search"] };
  });

  await perform("UI-006", async () => {
    for (const [current, key, expected] of [
      ["search", "ArrowRight", "paper"],
      ["paper", "End", "paper"],
      ["paper", "ArrowLeft", "search"],
      ["paper", "Home", "search"],
      ["search", "End", "paper"],
      ["paper", "ArrowRight", "search"],
    ]) {
      await page.press(`#search-task-tab-${current}`, key);
      const snapshot = await inspectUntil((item) => item.workflow.selected === `search-task-tab-${expected}`, `Workflow ${key} navigation`);
      requireWorkflow(snapshot, expected, { focused: true });
    }
    return { keys: ["ArrowRight", "ArrowLeft", "Home", "End"], wraparound: true };
  });

  await perform("UI-007", async () => {
    await page.click('.workspace-nav-item[aria-label^="当前任务"]');
    const snapshot = await inspectUntil((item) => item.activeView === "activity" && item.activity.present, "Activity workspace opening");
    requireCondition(snapshot.activity.title === "当前任务", "The task workspace lacks its page title");
    requireCondition(snapshot.activity.filterLabels.join("|") === "分析中|失败|待 Compile", "The task workspace lacks processing, failed, and pending filters");
    requireCondition(snapshot.activity.status === "分析中", "The task workspace does not default to processing tasks");
    requireCondition(snapshot.activity.hasUploadButton, "The task workspace lacks its upload action");
    requireCondition(snapshot.activity.dialogCount === 0, "The task workspace still renders drawer controls");
    requireActiveView(snapshot, "activity", "当前任务");
    return { workspace_view: true, filters: snapshot.activity.filterLabels, modal: false };
  });

  await perform("UI-008", async () => {
    await page.click(NAVIGATION.find((item) => item.view === "search").selector);
    await page.press('.workspace-nav-item[aria-label^="当前任务"]', "Enter");
    const snapshot = await inspectUntil((item) => item.activeView === "activity" && item.focus.taskButton, "Keyboard activity navigation");
    requireActiveView(snapshot, "activity", "当前任务");
    return { keyboard_activation: true, trigger_focus_retained: true };
  });

  await perform("UI-009", async () => {
    await page.click(NAVIGATION.find((item) => item.view === "library").selector);
    const snapshot = await inspectUntil((item) => item.activeView === "library" && !item.activity.present, "Leaving activity workspace");
    requireActiveView(snapshot, "library", "文献库");
    return { normal_workspace_switch: true };
  });

  await perform("UI-010", async () => {
    await page.click(NAVIGATION[0].selector);
    let snapshot = await inspectUntil((item) => item.library.present && !item.library.loading, "Initial completed library");
    requireCondition(snapshot.library.legacyActionLabels.length === 0,
      `The library still exposes removed management actions: ${snapshot.library.legacyActionLabels.join(", ")}`);
    requireCondition(snapshot.library.rowCount > 0, "Browser acceptance needs at least one completed, seeded PDF");
    completedCount = snapshot.library.rowCount;
    requireCondition(completedCount > 0, "The completed library unexpectedly became empty");
    return {
      legacy_management_actions: [],
      completed_rows: completedCount,
    };
  });

  await perform("UI-011", async () => {
    await page.fill("#library-query", noMatchQuery);
    await page.click('.library-search button[type="submit"]');
    const snapshot = await inspectUntil((item) => !item.library.loading && item.library.hasClearButton && item.library.rowCount === 0, "No-match library result");
    requireCondition(snapshot.library.queryValue === noMatchQuery, "The library query input did not retain the submitted value");
    requireCondition(snapshot.library.emptyTitle === "没有匹配的文献", "A no-match document query did not explain its empty state");
    return { matched_rows: 0, empty_state: snapshot.library.emptyTitle };
  });

  await perform("UI-012", async () => {
    await page.click(NAVIGATION[3].selector);
    await inspectUntil((item) => item.activeView === "search", "Search after local library filtering");
    await page.click("#search-task-tab-paper");
    const snapshot = await inspectUntil((item) => item.workflow.selected === "search-task-tab-paper", "Paper synthesis after no-match filtering");
    requireWorkflow(snapshot, "paper");
    requireCondition(snapshot.workflow.paperOptionCount >= completedCount,
      `The no-match library filter replaced the global synthesis corpus (${snapshot.workflow.paperOptionCount}/${completedCount})`);
    return { completed_library_rows: completedCount, synthesis_paper_options: snapshot.workflow.paperOptionCount };
  });

  await perform("UI-013", async () => {
    await page.click(NAVIGATION[0].selector);
    await inspectUntil((item) => item.library.present && item.library.hasClearButton, "Filtered library before clearing");
    await page.click(".library-toolbar > button");
    const snapshot = await inspectUntil((item) => !item.library.loading && !item.library.hasClearButton && item.library.rowCount > 0, "Restored completed library");
    requireCondition(snapshot.library.queryValue === "", "Clearing the library filter left a stale query value");
    requireCondition(snapshot.library.rowCount >= completedCount, "Clearing the query failed to restore the original completed library");
    return { restored_rows: snapshot.library.rowCount };
  });

  await perform("UI-014", async () => {
    let snapshot = await inspectUntil((item) => Boolean(item.library.evidenceTriggerSelector), "Completed paper evidence button");
    await page.click(snapshot.library.evidenceTriggerSelector);
    snapshot = await inspectUntil((item) => item.activeView === "paper" && item.paper.present && item.paper.tabs.length === 3, "Loaded immutable paper detail");
    requireCondition(Boolean(snapshot.paper.title), "The opened paper detail has no visible title");
    requirePaperTab(snapshot, "claims");
    return { initial_tab: snapshot.paper.selected };
  });

  await perform("UI-015", async () => {
    for (const id of ["source", "run", "claims"]) {
      await page.click(`#paper-tab-${id}`);
      const snapshot = await inspectUntil((item) => item.paper.selected === `paper-tab-${id}`, `${id} paper detail panel`);
      requirePaperTab(snapshot, id);
    }
    return { tabs: ["source", "run", "claims"] };
  });

  await perform("UI-016", async () => {
    for (const [current, key, expected] of [
      ["claims", "ArrowRight", "source"],
      ["source", "End", "run"],
      ["run", "ArrowLeft", "source"],
      ["source", "Home", "claims"],
      ["claims", "End", "run"],
      ["run", "ArrowRight", "claims"],
    ]) {
      await page.press(`#paper-tab-${current}`, key);
      const snapshot = await inspectUntil((item) => item.paper.selected === `paper-tab-${expected}`, `Paper detail ${key} navigation`);
      requirePaperTab(snapshot, expected, { focused: true });
    }
    await page.click("#paper-tab-source");
    const snapshot = await inspectUntil((item) => item.paper.selected === "paper-tab-source" && Boolean(item.paper.pdfTriggerSelector), "PDF source anchor");
    requireCondition(Number.isSafeInteger(snapshot.paper.pdfTriggerPage) && snapshot.paper.pdfTriggerPage > 0,
      "The selected source anchor has no independently observable, positive page number");
    openedTrigger = snapshot.paper.pdfTriggerSelector;
    openedPage = snapshot.paper.pdfTriggerPage;
    approximationSamples = snapshot.scientific.approximateSamples;
    return { keys: ["ArrowRight", "ArrowLeft", "Home", "End"], source_anchor_page: openedPage };
  });

  await perform("UI-017", async () => {
    await page.click(openedTrigger);
    const snapshot = await inspectUntil((item) => item.pdf.open && item.focus.insidePdf, "Accessible PDF preview modal");
    const framePattern = /^\/api\/artifacts\/[0-9a-f]{64}\?disposition=inline#page=([1-9]\d*)&zoom=page-fit$/iu;
    const match = snapshot.pdf.frameSrc.match(framePattern);
    requireCondition(snapshot.pdf.modal && snapshot.pdf.title, "The PDF preview is not an accessible, named modal");
    requireCondition(match && Number(match[1]) === openedPage, `The PDF iframe is not anchored at independently observed source page ${openedPage}`);
    requireCondition(snapshot.pdf.frameTitle.includes(`第 ${openedPage} 页`), "The PDF iframe title does not expose its actual source page");
    requireCondition(snapshot.pdf.scrollLocked, "Opening the PDF preview did not lock background scrolling");
    return { page: openedPage, modal: true, scroll_locked: true };
  });

  await perform("UI-018", async () => {
    let snapshot = await page.inspect();
    const first = snapshot.pdf.focusableSelectors[0];
    const last = snapshot.pdf.focusableSelectors.at(-1);
    requireCondition(first && last, "The PDF modal has no keyboard-focusable controls");
    requireCondition(snapshot.pdf.fullSourceSafe && snapshot.pdf.fullSourceHref === snapshot.pdf.frameSrc,
      "The complete-source link is not an isolated link to the exact same-origin PDF page");
    await page.press(first, "Shift+Tab");
    snapshot = await inspectUntil((item) => item.focus.insidePdf && item.focus.selector === last, "PDF modal backward focus wrap");
    await page.press(last, "Tab");
    snapshot = await inspectUntil((item) => item.focus.insidePdf && item.focus.selector === first, "PDF modal forward focus wrap");
    requireCondition(snapshot.focus.selector === first, "Tab escaped the PDF preview modal forward boundary");
    return { focusable_controls: snapshot.pdf.focusableSelectors.length, isolated_original_link: true, bidirectional_wrap: true };
  });

  await perform("UI-019", async () => {
    await page.press('.pdf-preview-dialog button[aria-label="关闭 PDF 预览"]', "Escape");
    let snapshot = await inspectUntil((item) => !item.pdf.open && item.focus.selector === openedTrigger, "PDF Escape close and exact trigger focus restoration");
    requireCondition(!snapshot.pdf.scrollLocked, "Closing the PDF preview did not restore background scrolling");
    requireCondition(snapshot.focus.selector === openedTrigger, "Closing the PDF preview focused a different source anchor");
    await page.click(openedTrigger);
    await inspectUntil((item) => item.pdf.open, "PDF modal reopened for close-button verification");
    await page.click('.pdf-preview-dialog button[aria-label="关闭 PDF 预览"]');
    snapshot = await inspectUntil((item) => !item.pdf.open && item.focus.selector === openedTrigger && !item.pdf.scrollLocked,
      "PDF button close and exact trigger focus restoration");
    await page.click(openedTrigger);
    snapshot = await inspectUntil((item) => item.pdf.open && Boolean(item.pdf.backdropPoint), "PDF modal reopened with a visible backdrop");
    await page.clickPoint(snapshot.pdf.backdropPoint);
    await inspectUntil((item) => !item.pdf.open && item.focus.selector === openedTrigger && !item.pdf.scrollLocked,
      "PDF real-pointer backdrop close and exact trigger focus restoration");
    return { escape: true, close_button: true, backdrop_pointer_close: true, scroll_restored: true, exact_trigger_focus_restored: true };
  });

  await perform("UI-020", async () => {
    const { scientific } = await page.inspect();
    requireCondition(scientific.nodeCount > 0, "No real scientific document content was observed");
    requireCondition(scientific.forbiddenElementCount === 0, "Rendered scientific content contains executable or injected HTML elements");
    requireCondition(scientific.dangerousLinkCount === 0, "A scientific-content link permits a dangerous scheme or unsafe external opener");
    requireCondition(scientific.deletedNumericCount === 0, "A numeric scientific value was incorrectly rendered as Markdown deletion");
    approximationSamples = scientific.approximateSamples.length ? scientific.approximateSamples : approximationSamples;
    if (requireScientificApproximation) {
      requireCondition(approximationSamples.length > 0, "The selected seeded PDF contains no observable numeric single-tilde case");
    }
    return { scientific_nodes: scientific.nodeCount, approximate_samples: approximationSamples, unsafe_elements: 0, unsafe_links: 0 };
  });

  await perform("UI-021", async () => {
    await page.click(".paper-view button.back-button");
    const snapshot = await inspectUntil((item) => item.activeView === "library" && item.library.present, "Paper return to the real source library");
    requireActiveView(snapshot, "library", "文献库");
    requireCondition(snapshot.library.rowCount > 0, "Returning to the source library lost the previously visible completed documents");
    return { returned_view: snapshot.activeView };
  });

  await perform("UI-022", async () => {
    await page.click(NAVIGATION[2].selector);
    let snapshot = await inspectUntil((item) => item.review.present, "Optional human-review workspace");
    requireCondition(snapshot.review.filters.join("|") === "全部|未复核|已通过", "The review workspace does not expose the three supported filters");
    const emptyFilters = [];
    for (const [index, expected] of [[2, "未复核"], [3, "已通过"], [1, "全部"]]) {
      await page.click(`.review-filter button:nth-of-type(${index})`);
      snapshot = await inspectUntil((item) => item.review.selected === expected, `${expected} human-review filter`);
      if (snapshot.review.empty) {
        requireCondition(snapshot.review.emptyText === "当前筛选条件下没有文献。", `${expected} review filter has an incorrect true empty state`);
        emptyFilters.push(expected);
      }
    }
    return { filters: snapshot.review.filters, empty_filters_observed: emptyFilters };
  });

  await perform("UI-025", async () => {
    await page.click(NAVIGATION[1].selector);
    let snapshot = await inspectUntil((item) => item.activeView === "knowledgeGraph"
      && item.graph.present && !item.graph.loading && item.graph.taxonomyColumns.length > 0,
    "Existing graph taxonomy without model processing");
    for (const level of ["domain", "field", "subfield", "topic"]) {
      const column = snapshot.graph.taxonomyColumns.find((item) => item.level === level);
      requireCondition(Boolean(column?.firstMainSelector), `Existing OpenAlex ${level} column has no safe, selectable node`);
      await page.click(column.firstMainSelector);
      snapshot = await inspectUntil((item) => item.activeView === "knowledgeGraph" && !item.graph.loading
        && (level === "topic" ? Boolean(item.graph.claimSelector) : item.graph.taxonomyColumns.length > snapshot.graph.taxonomyColumns.length),
      `Existing ${level} taxonomy node and its next non-mutating column`);
    }
    const claimCount = snapshot.graph.claimCount;
    requireCondition(claimCount > 0 && Boolean(snapshot.graph.claimSelector), "The existing taxonomy contains no safe Claim to inspect");
    await page.click(snapshot.graph.claimSelector);
    snapshot = await inspectUntil((item) => item.graph.neighborhoodPresent && Boolean(item.graph.neighborhoodEvidenceSelector),
      "Existing Claim relationship neighborhood and its safe evidence-source button");
    const edgeCount = snapshot.graph.edgeCount;
    await page.click(snapshot.graph.neighborhoodEvidenceSelector);
    snapshot = await inspectUntil((item) => item.activeView === "paper" && item.paper.present, "Graph-node source paper detail");
    await page.click(".paper-view button.back-button");
    snapshot = await inspectUntil((item) => item.activeView === "knowledgeGraph" && item.graph.present,
      "Paper detail return to its real knowledge-graph source");
    const resetSelector = snapshot.graph.resetSelector;
    requireCondition(Boolean(resetSelector), "The OpenAlex classification path exposes no reset control");
    const domain = snapshot.graph.taxonomyColumns.find((item) => item.level === "domain");
    requireCondition(Boolean(domain?.firstMainSelector), "The restored graph has no existing safe Domain node");
    await page.click(domain.firstMainSelector);
    await inspectUntil((item) => item.graph.taxonomyColumns.length >= 2, "Graph taxonomy path selected before reset");
    await page.click(resetSelector);
    snapshot = await inspectUntil((item) => item.graph.taxonomyColumns.length === 1
      && item.graph.taxonomyColumns[0].level === "domain", "OpenAlex classification path reset");
    return { taxonomy_levels: ["domain", "field", "subfield", "topic"], existing_claim_count: claimCount, edge_count: edgeCount,
      returned_view: snapshot.activeView, path_reset: true };
  });

  await perform("UI-026", async () => {
    let snapshot = await inspectUntil((item) => item.activeView === "knowledgeGraph"
      && Boolean(item.graph.taxonomyColumns.find((column) => column.level === "domain")?.firstSearchSelector),
    "Existing domain scope without a model request");
    const domainSearch = snapshot.graph.taxonomyColumns.find((column) => column.level === "domain").firstSearchSelector;
    await page.click(domainSearch);
    snapshot = await inspectUntil((item) => item.activeView === "search" && item.search.taxonomyScope && !item.search.claimSeed,
      "Domain-scoped evidence search without submission");
    await page.click('button[aria-label="移除 OpenAlex 检索范围"]');
    snapshot = await inspectUntil((item) => item.activeView === "search" && !item.search.taxonomyScope && !item.search.claimSeed,
      "Independent OpenAlex scope removal");
    await page.click(NAVIGATION[1].selector);
    snapshot = await inspectUntil((item) => item.graph.present
      && Boolean(item.graph.taxonomyColumns.find((column) => column.level === "domain")?.firstSearchSelector),
    "Existing domain scope recreated for brand reset");
    await page.click(snapshot.graph.taxonomyColumns.find((column) => column.level === "domain").firstSearchSelector);
    await inspectUntil((item) => item.search.taxonomyScope, "Observable OpenAlex scope before brand reset");
    await page.click("button.brand");
    snapshot = await inspectUntil((item) => item.activeView === "search" && !item.search.taxonomyScope && !item.search.claimSeed,
      "Brand reset clears the real OpenAlex scope");
    return { independent_scope_removal: true, brand_cleared_scope: true };
  });

  const seedRealClaimWithoutSubmission = async (explanation) => {
    await page.click(NAVIGATION[0].selector);
    let snapshot = await inspectUntil((item) => item.library.present && !item.library.loading
      && Boolean(item.library.evidenceTriggerSelector), `${explanation}: existing completed library evidence`);
    await page.click(snapshot.library.evidenceTriggerSelector);
    snapshot = await inspectUntil((item) => item.paper.present && Boolean(item.paper.compareTriggerSelector),
      `${explanation}: existing immutable Claim action`);
    await page.click(snapshot.paper.compareTriggerSelector);
    return inspectUntil((item) => item.activeView === "search" && item.search.claimSeed && !item.search.taxonomyScope,
      `${explanation}: observable Claim seed without search submission`);
  };

  await perform("UI-027", async () => {
    await seedRealClaimWithoutSubmission("Independent Claim seed removal");
    await page.click('button[aria-label="移除观点起点"]');
    await inspectUntil((item) => item.activeView === "search" && !item.search.claimSeed && !item.search.taxonomyScope,
      "Independent Claim seed removal");
    await seedRealClaimWithoutSubmission("Brand Claim seed reset");
    await page.click("button.brand");
    await inspectUntil((item) => item.activeView === "search" && !item.search.claimSeed && !item.search.taxonomyScope,
      "Brand reset clears a real prior Claim seed");
    return { independent_claim_removal: true, brand_cleared_claim: true };
  });

  await perform("UI-028", async () => {
    let snapshot = await inspectUntil((item) => item.activeView === "search", "Evidence search before result-origin inspection");
    let submitted = false;
    if (!snapshot.search.existingResultSelector) {
      if (!allowModelInteractions) {
        throw new BrowserProbeBlocked("No existing result exists after a fresh session; model-backed search is prohibited unless allowModelInteractions is explicitly true");
      }
      await page.fill(".evidence-search .search-form textarea", modelSearchQuery);
      await page.click('.evidence-search .search-form button[type="submit"]');
      submitted = true;
      let settled = false;
      for (let attempt = 0; attempt < modelSettleAttempts; attempt += 1) {
        snapshot = await page.inspect();
        requireCondition(!snapshot.search.error && !snapshot.search.degraded,
          "The explicitly authorized real search reported a provider failure or degraded result");
        if (!snapshot.search.busy && snapshot.search.existingResultSelector) {
          settled = true;
          break;
        }
        if (attempt + 1 < modelSettleAttempts && modelSettleDelayMs) {
          await new Promise((resolve) => setTimeout(resolve, modelSettleDelayMs));
        }
      }
      requireCondition(settled, `The explicitly authorized real search produced no completed evidence result after ${modelSettleAttempts} bounded observations`);
    }
    requireCondition(Boolean(snapshot.search.existingResultSelector), "No genuine evidence-search paper result was observable");
    const resultCount = snapshot.search.previewCount + snapshot.search.resultCount;
    await page.click(snapshot.search.existingResultSelector);
    await inspectUntil((item) => item.activeView === "paper" && item.paper.present, "Actual search-result source paper detail");
    await page.click(".paper-view button.back-button");
    snapshot = await inspectUntil((item) => item.activeView === "search", "Paper detail return to its real evidence-search source");
    requireActiveView(snapshot, "search", "观点校验");
    return { returned_view: snapshot.activeView, existing_result_count: resultCount, explicitly_authorized_model_search: submitted };
  });

  await perform("UI-023", async () => {
    const cases = [];
    for (const device of plan.viewports) {
      await page.setViewport(device);
      for (const destination of NAVIGATION) {
        await page.click(destination.selector);
        const snapshot = await inspectUntil((item) => item.activeView === destination.view, `${device.width}px ${destination.label} navigation`);
        requireActiveView(snapshot, destination.view, destination.label);
        requireCondition(Math.abs(snapshot.viewport.width - device.width) <= 2,
          `The browser did not apply the requested ${device.width}px viewport`);
        requireCondition(snapshot.viewport.horizontalOverflow <= 1,
          `${destination.label} overflows horizontally by ${snapshot.viewport.horizontalOverflow}px at ${device.width}px`);
        cases.push({ width: device.width, view: destination.view, overflow: snapshot.viewport.horizontalOverflow });
      }
      await page.click(NAVIGATION[0].selector);
      let snapshot = await inspectUntil((item) => item.library.present && !item.library.loading
        && Boolean(item.library.evidenceTriggerSelector), `${device.width}px existing completed library paper`);
      await page.click(snapshot.library.evidenceTriggerSelector);
      snapshot = await inspectUntil((item) => item.activeView === "paper" && item.paper.present,
        `${device.width}px existing immutable paper detail`);
      requireCondition(snapshot.viewport.horizontalOverflow <= 1,
        `Paper detail overflows horizontally by ${snapshot.viewport.horizontalOverflow}px at ${device.width}px`);
      cases.push({ width: device.width, view: "paper", overflow: snapshot.viewport.horizontalOverflow });
      await page.click("#paper-tab-source");
      snapshot = await inspectUntil((item) => item.paper.selected === "paper-tab-source" && Boolean(item.paper.pdfTriggerSelector),
        `${device.width}px visible existing source anchor`);
      const trigger = snapshot.paper.pdfTriggerSelector;
      await page.click(trigger);
      snapshot = await inspectUntil((item) => item.pdf.open && item.pdf.scrollLocked && Boolean(item.pdf.bounds),
        `${device.width}px accessible PDF preview`);
      requireCondition(snapshot.viewport.horizontalOverflow <= 1,
        `PDF preview overflows horizontally by ${snapshot.viewport.horizontalOverflow}px at ${device.width}px`);
      requireCondition(snapshot.pdf.bounds.left >= -1 && snapshot.pdf.bounds.top >= -1
        && snapshot.pdf.bounds.right <= snapshot.viewport.width + 1
        && snapshot.pdf.bounds.bottom <= snapshot.viewport.height + 1,
      `PDF preview is clipped outside the ${snapshot.viewport.width}x${snapshot.viewport.height} viewport`);
      cases.push({ width: device.width, view: "pdf_modal", overflow: snapshot.viewport.horizontalOverflow });
      await page.click('.pdf-preview-dialog button[aria-label="关闭 PDF 预览"]');
      await inspectUntil((item) => !item.pdf.open && item.focus.selector === trigger && !item.pdf.scrollLocked,
        `${device.width}px PDF button close and source focus restoration`);
      await page.click(".paper-view button.back-button");
      await inspectUntil((item) => item.activeView === "library", `${device.width}px detail return to source library`);
    }
    return { cases };
  });

  await perform("UI-024", async () => {
    await page.click("button.brand");
    const snapshot = await inspectUntil((item) => item.activeView === "search", "Final evidence-search homepage");
    requireActiveView(snapshot, "search", "观点校验");
    return { active_view: snapshot.activeView };
  });

  return report(true);
}
