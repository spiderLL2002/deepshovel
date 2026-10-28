"use client";

import { useEffect, useRef, useState } from "react";

const ATLAS_URL = "/geochemical-atlas/world-atlas.web?embedded=1&tab=map&v=map-first-3";
const VIEWS = [
  { id: "mapView", label: "元素分布" },
  { id: "databaseView", label: "测定数据库" },
  { id: "combinationView", label: "元素组合对比" },
  { id: "sourceView", label: "来源与置信度" },
  { id: "anomalyView", label: "候选异常" },
] as const;
type AtlasView = typeof VIEWS[number]["id"];

/** The iframe has its own document: explicitly share the host's current theme
 * and font faces instead of relying on a stale copy of foundation.css. */
function syncFrameTheme(frame: HTMLIFrameElement) {
  const doc = frame.contentDocument;
  if (!doc) return;
  const theme = getComputedStyle(document.documentElement);
  for (const property of Array.from(theme)) {
    if (property.startsWith("--")) doc.documentElement.style.setProperty(property, theme.getPropertyValue(property));
  }
  doc.documentElement.style.fontSize = theme.fontSize;
  doc.documentElement.style.setProperty("--atlas-map-height", `${Math.max(260, Math.min(560, window.innerHeight * 0.52))}px`);
  if (doc.getElementById("atlas-host-fonts")) return;
  const fonts = doc.createElement("style");
  fonts.id = "atlas-host-fonts";
  const rules: string[] = [];
  for (const sheet of Array.from(document.styleSheets)) {
    try {
      for (const rule of Array.from(sheet.cssRules)) {
        if (rule.type !== CSSRule.FONT_FACE_RULE) continue;
        rules.push(rule.cssText.replace(/url\(["']?([^"')]+)["']?\)/g, (_, url: string) =>
          `url("${new URL(url, sheet.href ?? document.baseURI).href}")`));
      }
    } catch { /* Cross-origin stylesheets cannot contribute host font faces. */ }
  }
  fonts.textContent = rules.join("\n");
  doc.head.append(fonts);
}

export function GeochemicalAtlas() {
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [selectedView, setSelectedView] = useState<AtlasView>("mapView");
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const selectedViewRef = useRef<AtlasView>("mapView");
  const dataReadyRef = useRef(false);
  const presentationReadyRef = useRef(false);
  const timeoutRef = useRef<number | null>(null);
  const observerRef = useRef<MutationObserver | null>(null);

  const reveal = () => {
    if (!dataReadyRef.current || !presentationReadyRef.current) return;
    if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current);
    const doc = iframeRef.current?.contentDocument;
    const tab = doc?.querySelector<HTMLButtonElement>(`.tab[data-view="${selectedViewRef.current}"]`);
    if (tab && !tab.classList.contains("active")) tab.click();
    setLoaded(true);
    setError(false);
  };

  useEffect(() => {
    const handleMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin || event.source !== iframeRef.current?.contentWindow) return;
      if (event.data?.type === "deepshovel:geochemical-atlas-ready") {
        dataReadyRef.current = true;
        reveal();
      }
    };
    window.addEventListener("message", handleMessage);
    timeoutRef.current = window.setTimeout(() => setError(true), 45_000);
    const resize = () => { if (iframeRef.current) syncFrameTheme(iframeRef.current); };
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("message", handleMessage);
      window.removeEventListener("resize", resize);
      if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current);
      observerRef.current?.disconnect();
    };
  }, [attempt]);

  const handleFrameLoad = async () => {
    const frame = iframeRef.current;
    const doc = frame?.contentDocument;
    if (!frame || !doc) return;
    syncFrameTheme(frame);
    const updateView = () => {
      const view = doc.querySelector<HTMLElement>(".tabs .tab.active")?.dataset.view;
      if (VIEWS.some((item) => item.id === view)) {
        selectedViewRef.current = view as AtlasView;
        setSelectedView(view as AtlasView);
      }
      if (doc.querySelector('#atlasBootStatus[role="alert"]')) setError(true);
    };
    observerRef.current?.disconnect();
    observerRef.current = new MutationObserver(updateView);
    const tabs = doc.querySelector(".tabs");
    if (tabs) observerRef.current.observe(tabs, { subtree: true, attributes: true, attributeFilter: ["class"] });
    const boot = doc.getElementById("atlasBootStatus");
    if (boot) observerRef.current.observe(boot, { attributes: true, attributeFilter: ["role"] });
    await doc.fonts.ready;
    await new Promise<void>((resolve) => window.requestAnimationFrame(() => window.requestAnimationFrame(() => resolve())));
    if (iframeRef.current !== frame || frame.contentDocument !== doc) return;
    presentationReadyRef.current = true;
    dataReadyRef.current ||= Boolean(doc.body.dataset.gcaMapReadyMs);
    reveal();
  };

  const selectView = (view: AtlasView) => {
    selectedViewRef.current = view;
    setSelectedView(view);
    if (!loaded) return;
    iframeRef.current?.contentDocument?.querySelector<HTMLButtonElement>(`.tab[data-view="${view}"]`)?.click();
    iframeRef.current?.contentWindow?.scrollTo(0, 0);
  };

  const retry = () => {
    dataReadyRef.current = false;
    presentationReadyRef.current = false;
    setLoaded(false);
    setError(false);
    setAttempt((value) => value + 1);
  };

  return (
    <section className="geochemical-atlas-view" aria-labelledby="geochemical-atlas-title">
      <header className="view-header page-intro">
        <div><h1 id="geochemical-atlas-title">元素图谱</h1></div>
      </header>
      <nav className="paper-tabs geochemical-atlas-tabs" aria-label="元素图谱视图">
        {VIEWS.map((view) => (
          <button key={view.id} type="button" aria-current={selectedView === view.id ? "page" : undefined}
            onClick={() => selectView(view.id)}>{view.label}</button>
        ))}
      </nav>
      <div className="geochemical-atlas-content" aria-busy={!loaded && !error}>
        {!loaded || error ? (
          <div className="geochemical-atlas-loading">
            <div className="geochemical-atlas-skeleton" role={error ? "alert" : "status"}>
              <p>{error ? "地图与测定数据加载失败，请重试。" : "正在加载地图与测定数据…"}</p>
              {error ? <button className="button secondary" type="button" onClick={retry}>重新加载</button> : null}
            </div>
          </div>
        ) : null}
        <iframe key={attempt} className={loaded && !error ? "is-loaded" : undefined}
          onLoad={handleFrameLoad} ref={iframeRef} referrerPolicy="no-referrer"
          tabIndex={loaded && !error ? 0 : -1} aria-hidden={!loaded || error}
          sandbox="allow-downloads allow-forms allow-popups allow-popups-to-escape-sandbox allow-same-origin allow-scripts"
          src={ATLAS_URL} title="元素图谱数据与交互地图" />
      </div>
    </section>
  );
}
