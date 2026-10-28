import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { register } from "node:module";
import test from "node:test";

const storeStub = `
export class ApiError extends Error {
  constructor(status, code, message, details) {
    super(message); this.name = "ApiError"; this.status = status; this.code = code; this.details = details;
  }
}`;
const loader = `
let rootUrl;
let storeUrl;
export function initialize(data) { rootUrl = data.rootUrl; storeUrl = data.storeUrl; }
export async function resolve(specifier, context, nextResolve) {
  if (specifier === "@/lib/server/evidence-store") return { url: storeUrl, shortCircuit: true };
  if (specifier.startsWith("@/")) {
    let path = specifier.slice(2);
    if (path === "lib/evidence-graph" || path === "lib/evidence-retrieval") path += "/index.ts";
    else if (!/\\.[cm]?[jt]sx?$/.test(path)) path += ".ts";
    return { url: new URL(path, rootUrl).href, shortCircuit: true };
  }
  return nextResolve(specifier, context);
}`;
register(`data:text/javascript,${encodeURIComponent(loader)}`, {
  parentURL: import.meta.url,
  data: {
    rootUrl: new URL("../", import.meta.url).href,
    storeUrl: `data:text/javascript,${encodeURIComponent(storeStub)}`,
  },
});

const { parseOpenAlexClassification } = await import("../app/api/evidence-graphs/import-contract.ts");
const { parseEvidenceSearchRequest } = await import("../lib/evidence-retrieval/contract.ts");
const knowledgeGraphSource = readFileSync(new URL("../app/components/evidence-knowledge-graph.tsx", import.meta.url), "utf8");
const taxonomyPanoramaSource = readFileSync(new URL("../app/components/taxonomy-panorama.tsx", import.meta.url), "utf8");
const taxonomySceneSource = readFileSync(new URL("../app/components/taxonomy-scene.ts", import.meta.url), "utf8");

function topic(id = "T1048", score = 0.72) {
  return {
    id: `https://openalex.org/${id}`,
    display_name: "Evidence Synthesis",
    score,
    subfield: { id: "https://openalex.org/subfields/1702", display_name: "Artificial Intelligence" },
    field: { id: "https://openalex.org/fields/17", display_name: "Computer Science" },
    domain: { id: "https://openalex.org/domains/3", display_name: "Physical Sciences" },
  };
}

test("OpenAlex classification preserves the official hierarchy and primary Topic", () => {
  const first = topic();
  const parsed = parseOpenAlexClassification({
    source: "official_title_abstract_model",
    classifier: "OpenAlex/official-model",
    openalex_work_id: null,
    matched_doi: null,
    primary_topic: first,
    topics: [first, topic("T2048", 0.2)],
  });
  assert.equal(parsed.primaryTopic.id, "https://openalex.org/T1048");
  assert.equal(parsed.topics[0].domain.displayName, "Physical Sciences");
  assert.equal(parsed.topics.length, 2);
});

test("OpenAlex classification rejects a primary Topic that is not topics[0]", () => {
  assert.throws(() => parseOpenAlexClassification({
    source: "openalex_work",
    classifier: "OpenAlex Work API Topics",
    openalex_work_id: "https://openalex.org/W1",
    matched_doi: "10.1/example",
    primary_topic: topic("T2048", 0.2),
    topics: [topic()],
  }), /primary_topic/u);
});

test("existing retrieval request accepts an OpenAlex taxonomy scope without changing retrieval intent", () => {
  const request = parseEvidenceSearchRequest({
    stage: "preview",
    query: "evidence synthesis",
    intent: "claim_search",
    corpus: "cross_paper",
    openalex_scope: {
      level: "field",
      id: "https://openalex.org/fields/17",
    },
    limit: 12,
  });
  assert.deepEqual(request.openAlexScope, {
    level: "field",
    id: "https://openalex.org/fields/17",
  });
  assert.equal(request.intent, "claim_search");
  assert.equal(request.corpus, "cross_paper");
});

test("OpenAlex navigation exposes the official hierarchy and accessible scoped search", () => {
  assert.match(knowledgeGraphSource, /const LEVEL_ORDER[^=]*= \["domain", "field", "subfield", "topic"\]/u);
  assert.match(taxonomyPanoramaSource, /const LEVEL_ORDER[^=]*= \["domain", "field", "subfield", "topic"\]/u);
  assert.match(taxonomyPanoramaSource, /role="region"[\s\S]*?aria-label=\{`OpenAlex 四级分类图谱/u);
  assert.match(taxonomyPanoramaSource, /new Worker\(new URL\("\.\/taxonomy-scene\.worker\.ts"/u);
  assert.match(taxonomyPanoramaSource, /import\("@cosmos\.gl\/graph"\)/u);
  assert.match(taxonomyPanoramaSource, /enableSimulation: false/u);
  assert.match(taxonomyPanoramaSource, /spaceToScreenPosition/u);
  assert.match(taxonomyPanoramaSource, /buildTaxonomyFocusLayout/u);
  assert.match(taxonomyPanoramaSource, /graph\.setPointPositions\(displayPositions/u);
  assert.match(taxonomyPanoramaSource, /setZoomTransformByPointPositions\(bounds/u);
  assert.match(taxonomyPanoramaSource, /focusCamera\(firstCameraFrame \? 0 : undefined, !firstCameraFrame\)/u);
  assert.match(taxonomyPanoramaSource, /preserveZoom \? graph\.getZoomLevel\(\) : undefined/u);
  assert.match(taxonomyPanoramaSource, /graph\.ready\.then[\s\S]*?\.catch/u);
  assert.doesNotMatch(taxonomyPanoramaSource, /graph\.fitView\(/u);
  assert.match(taxonomyPanoramaSource, /onClick=\{\(\) => onSearchTaxonomy\(focus\)\}/u);
  assert.match(taxonomyPanoramaSource, /className="taxonomy-cosmos-status"/u);
  assert.match(taxonomyPanoramaSource, /<strong>\{claimCount\}<\/strong> Claims/u);
  assert.match(taxonomyPanoramaSource, /<strong>\{relationCount\}<\/strong> Claim 关系/u);
  assert.match(taxonomyPanoramaSource, /if \(path\.length === 1\) onReset\(\);[\s\S]*?else onNavigatePath\(path\.length - 2\);/u);
  assert.match(taxonomyPanoramaSource, /className="taxonomy-cosmos-label-links"/u);
  assert.match(taxonomyPanoramaSource, /<span>\{node\.paperCount\} 篇论文<\/span>/u);
  assert.match(taxonomyPanoramaSource, /mobileDenseFrontier = frontier\.length > 8/u);
  assert.doesNotMatch(taxonomyPanoramaSource, /taxonomy-cosmos-fallback|当前浏览器无法启用高性能图谱渲染|setRendererError/u);
  assert.doesNotMatch(taxonomyPanoramaSource, />全部分类</u);
  assert.doesNotMatch(taxonomyPanoramaSource, /个可选节点/u);
  assert.match(taxonomySceneSource, /new Float32Array/u);
  assert.match(taxonomySceneSource, /TAXONOMY_SCENE_MAX_CLAIM_POINTS = 160_000/u);
  assert.match(knowledgeGraphSource, /path\.length === LEVEL_ORDER\.length/u);
  assert.doesNotMatch(knowledgeGraphSource, /taxonomy-spider-viewport|<svg className="taxonomy-spider"/u);
  assert.doesNotMatch(knowledgeGraphSource, /knowledge-maintenance|knowledge-summary(?:-action)?|status-tabs|taxonomy-spider-description/u);
  assert.doesNotMatch(knowledgeGraphSource, /const processOne|const processAll|const stopBatch|confirmManagementAction/u);
});

test("Claim neighborhood is a complete keyboard-accessible pan and zoom canvas", () => {
  assert.match(knowledgeGraphSource, /const GRAPH_RELATIONS = \["supports", "refines", "qualifies", "equivalent_to", "contradicts", "supplements"\]/u);
  assert.match(knowledgeGraphSource, /addEventListener\("wheel", handleWheel, \{ passive: false \}\)/u);
  assert.match(knowledgeGraphSource, /setPointerCapture\(event\.pointerId\)/u);
  assert.match(knowledgeGraphSource, /onPointerMove=\{handleCanvasPointerMove\}/u);
  assert.match(knowledgeGraphSource, /aria-label="缩小关系图"/u);
  assert.match(knowledgeGraphSource, /aria-label="放大关系图"/u);
  assert.match(knowledgeGraphSource, />适应窗口</u);
  assert.match(knowledgeGraphSource, /event\.key === "ArrowLeft"/u);
  assert.match(knowledgeGraphSource, /event\.key === "0"/u);
});
