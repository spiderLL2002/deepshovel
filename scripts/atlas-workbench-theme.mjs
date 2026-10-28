import { createHash } from "node:crypto";
import { readFile, readdir } from "node:fs/promises";
import { optimizeAtlasMapStartup } from "./atlas-map-startup.mjs";

/** Remove standalone-delivery prose from the embedded product interface. */
function simplifyAtlasCopy(html) {
  return html
    .replace(/<(details|div) id="coordinate-mode-banner"[^>]*>[\s\S]*?<\/\1>/g, '')
    .replace(/<p id="atlasSubtitle">[\s\S]*?<\/p>/g, '')
    .replace('$("atlasSubtitle").textContent=PROFILE.subtitle;', '')
    .replace(/<div class="eyebrow">[\s\S]*?<\/div>/g, '')
    .replace(/<span class="question-contract">[\s\S]*?<\/span>/g, '')
    .replace(/<p class="section-lead">[\s\S]*?<\/p>/g, '')
    .replace(/<div id="taskContext"[^>]*>[\s\S]*?<\/div>/g, '')
    .replace(/\$\("taskStory"\)\.textContent=[^;]*;/g, '')
    .replace(/\$\("profileStatus"\)\.textContent=[^;]*;/g, '')
    .replace('<span>数据库 CSV · 置信度报告 · 异常结果 · 运行报告</span>', '')
    .replace('更多筛选 · 严格证据层、地质、方法、来源与工作流可用性', '更多筛选');
}

/** Adapt the standalone atlas presentation without changing its data or analysis. */
export async function applyAtlasWorkbenchTheme(html) {
  html = await optimizeAtlasMapStartup(simplifyAtlasCopy(html));
  const assets = new Set(await readdir(new URL('../public/geochemical-atlas/', import.meta.url)));
  // Standalone exports may link to reports that the Web bundle does not ship.
  html = html.replace(/<a\b[^>]*\bhref="([^"/]+\.(?:csv|json))"[^>]*>[\s\S]*?<\/a>/g,
    (link, name) => assets.has(name) ? link : '');
  html = html.replace('pairUniverse=selected.length*(selected.length-1)/2',
    'pairUniverse=Math.max(0,selected.length*(selected.length-1)/2)');
  html = html.replace('$("atlasTitle").textContent=PROFILE.title', '$("atlasTitle").textContent="元素图谱"');
  html = html.replace(/<style>([\s\S]*?)<\/style>/, (_, css) => `<style>${css.replaceAll('font-size:0.875rem', 'font-size:var(--text-secondary)')}</style>`);
  const foundation = await readFile(new URL('../app/styles/foundation.css', import.meta.url), 'utf8');
  const workbench = await readFile(new URL('../public/geochemical-atlas/workbench.css', import.meta.url), 'utf8');
  const version = createHash('sha256').update(foundation + workbench).digest('hex').slice(0, 12);
  const stylesheet = `<link rel="stylesheet" href="/geochemical-atlas/workbench.css?v=${version}">`;
  if (!html.includes('id="atlas-embedded-mode"')) {
    html = html.replace('</head>', '<script id="atlas-embedded-mode">if(new URLSearchParams(location.search).get("embedded")==="1")document.documentElement.dataset.deepshovelEmbedded="true";</script>\n</head>');
  }
  if (html.includes('id="atlas-workbench-foundation"')) {
    return html.replace(/<style id="atlas-workbench-foundation">[\s\S]*?<\/style>/, `<style id="atlas-workbench-foundation">${foundation}</style>`)
      .replace(/<link rel="stylesheet" href="\/geochemical-atlas\/workbench\.css[^"]*">/, stylesheet);
  }
  html = html.replace(/<style>([\s\S]*?)<\/style>/, (_, css) => {
    // Legacy charts and generated detail panels use many compact rules. Normalize
    // their typography too, rather than fixing only the initial map screen.
    css = css.replace(/font-size:(\d+(?:\.\d+)?)px/g, (rule, size) =>
      Number(size) < 14 ? 'font-size:0.875rem' : rule);
    css = css.replace(/font-weight:([6-9]\d\d)/g, 'font-weight:600');
    return `<style>${css}</style>\n<style id="atlas-workbench-foundation">${foundation}</style>\n${stylesheet}`;
  });
  html = html.replace('<h1 id="atlasTitle">全球地球化学元素图谱</h1>', '<h1 id="atlasTitle">元素图谱</h1>');
  html = html.replace(/(<button class="tab[^>]*data-view="[^"]+"[^>]*>)([^<]+)/g, (_, start, label) => start + ({
    '可交互元素分布地图':'元素分布', '标准化地球化学数据库':'测定数据库',
    '数据来源与置信度说明':'来源与置信度', '异常区域识别结果':'候选异常',
  }[label] || label));
  // The 2D basemap is a reading surface. Scientific symbol scales stay intact.
  html = html.replace(/function renderBasemap\(\)\{[\s\S]*?(?=\nfunction )/, block => {
    const colors = {
      '#061923':'#edf2fa', '#082630':'#edf2fa', '#0a202b':'#edf2fa',
      '#174753':'#dce5f1', '#113a46':'#dce5f1', '#0d303c':'#dce5f1',
      'rgba(21,108,116,.16)':'rgba(47,100,232,0)', 'rgba(2,8,14,.4)':'rgba(47,100,232,0)',
      'rgba(99,229,216,.24)':'rgba(82,98,122,.3)', 'rgba(128,181,184,.105)':'rgba(82,98,122,.14)',
      'rgba(148,190,191,.5)':'#52627a', 'rgba(99,229,216,.3)':'#b9c6da',
      'rgba(55,218,202,.17)':'rgba(47,100,232,0)', 'rgba(64,150,148,.2)':'rgba(82,98,122,.15)',
      'rgba(125,215,206,.55)':'#8c9eb8',
    };
    for (const [before, after] of Object.entries(colors)) block = block.replaceAll(before, after);
    return block;
  });
  html = html.replace('ctx.strokeStyle=span>180?"rgba(196,224,222,.38)":"rgba(205,235,232,.58)"', 'ctx.strokeStyle=span>180?"#9aabc2":"#7186a3"');
  return html;
}
