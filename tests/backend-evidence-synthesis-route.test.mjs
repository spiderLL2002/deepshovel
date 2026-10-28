import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { register } from "node:module";
import test from "node:test";

const PROJECT_ID = "DEEPSHOVEL-PROJECT";
const state = { scenario: null, calls: null };
globalThis.__evidenceSynthesisRouteTest = state;
globalThis.__evidenceSynthesisDigest = (value) => createHash("sha256").update(value).digest("hex");

const storeStub = `
export const PROJECT_ID=${JSON.stringify(PROJECT_ID)};
export class ApiError extends Error {constructor(status,code,message,details){super(message);this.status=status;this.code=code;this.details=details;}}
export function assertSameOrigin(request){const origin=request.headers.get("origin");if(origin&&origin!==new URL(request.url).origin)throw new ApiError(403,"CROSS_ORIGIN_WRITE_REJECTED","Cross-origin write rejected");}
export async function getBindings(){const fixture=globalThis.__evidenceSynthesisRouteTest;if(fixture.scenario.bindingError)throw fixture.scenario.bindingError;return {DB:fixture.scenario.db};}
export async function getCompilerConfiguration(){const fixture=globalThis.__evidenceSynthesisRouteTest;fixture.calls.configurations+=1;if(fixture.scenario.configurationError)throw fixture.scenario.configurationError;return structuredClone(fixture.scenario.configuration);}
export async function readJsonObject(request,maximum){const text=await request.text();if(new TextEncoder().encode(text).byteLength>maximum)throw new ApiError(413,"REQUEST_TOO_LARGE","request too large");let parsed;try{parsed=JSON.parse(text);}catch{throw new ApiError(400,"INVALID_JSON","invalid JSON");}if(!parsed||typeof parsed!=="object"||Array.isArray(parsed))throw new ApiError(400,"INVALID_JSON","body must be an object");return parsed;}
export function safeError(error){return Response.json({ok:false,error:{code:error?.code??"INTERNAL_ERROR",message:error instanceof Error?error.message:String(error),...(error?.details?{details:error.details}:{})}},{status:error?.status??500});}
`;
const graphStub = `
export async function parseEvidenceGraph(graph){const fixture=globalThis.__evidenceSynthesisRouteTest;if(fixture.scenario.graphError)throw fixture.scenario.graphError;return graph;}
export const sha256Hex=async(value)=>globalThis.__evidenceSynthesisDigest(value);
`;
const comparisonStub = `
export const CLAIM_COMPARISON_SCHEMA_VERSION="claim-comparison/1.0";
export function buildClaimComparisonEvidenceEntities(graph,claimId,assessment){globalThis.__evidenceSynthesisRouteTest.calls.entityLoads.push({claimId,assessment});return structuredClone(graph.entities?.[claimId]??[]);}
export function buildClaimComparisonSide(document,evidenceEntities){return {claim_id:document.claimId,canonical_claim:document.canonicalClaim,claim_type:document.claimType,polarity:document.polarity,modality:document.modality,scope:document.scope,source_snapshot_id:document.snapshotId,source_paper_id:document.paperId,source_paper_family_id:document.paperFamilyId,evidence_entities:evidenceEntities};}
`;
const retrievalStub = `
export class EvidenceRetrievalStoreError extends Error{constructor(status,code,message){super(message);this.status=status;this.code=code;}}
export class EvidenceSearchContractError extends Error{constructor(message,path){super(message);this.path=path;}}
export function parseEvidenceSearchRequest(request){const fixture=globalThis.__evidenceSynthesisRouteTest;fixture.calls.parsedSearches.push(structuredClone(request));if(fixture.scenario.contractError)throw new EvidenceSearchContractError("invalid retrieval contract","$.query");return {stage:request.stage,query:request.query??null,queryClaim:request.query_claim?{snapshotId:request.query_claim.snapshot_id,claimId:request.query_claim.claim_id}:null,candidateRefs:(request.candidate_refs??[]).map((ref)=>({snapshotId:ref.snapshot_id,claimId:ref.claim_id})),intent:request.intent,corpus:request.corpus,limit:request.limit};}
export async function resolveEvidenceRetrievalCorpus(_db,projectId,request){const fixture=globalThis.__evidenceSynthesisRouteTest;fixture.calls.resolutions.push({projectId,request});if(fixture.scenario.storeError)throw new EvidenceRetrievalStoreError(409,"RETRIEVAL_STORE_INVALID","stored retrieval corpus invalid");return {request,queryText:request.query??fixture.scenario.queryDocument?.canonicalClaim??"",queryDocument:fixture.scenario.queryDocument,manifests:structuredClone(fixture.scenario.manifests)};}
export async function loadEvidenceRetrievalDocumentsByRefs(_db,_projectId,refs){const fixture=globalThis.__evidenceSynthesisRouteTest;fixture.calls.documentRefs.push(structuredClone(refs));return refs.map((ref)=>fixture.scenario.documents.get(ref.snapshotId+"\\0"+ref.claimId)).filter(Boolean).map((document)=>structuredClone(document));}
export async function loadLatestEvidenceRetrievalAssessmentsByRefs(_db,_projectId,refs){const fixture=globalThis.__evidenceSynthesisRouteTest;fixture.calls.assessmentRefs.push(structuredClone(refs));return refs.map((ref)=>fixture.scenario.assessments.get(ref.snapshotId+"\\0"+ref.claimId)).filter(Boolean).map((assessment)=>structuredClone(assessment));}
export function toEvidenceRetrievalSearchRequestDto(projectId,resolved){return {schema_version:"evidence-retrieval/2.0",project_id:projectId,stage:resolved.request.stage,query:resolved.queryText,filters:{limit:resolved.request.limit},query_claim:resolved.request.queryClaim,candidate_refs:resolved.request.candidateRefs.map((ref)=>({snapshot_id:ref.snapshotId,claim_id:ref.claimId}))};}
`;
const compilerStub = `
export class CompilerClient{
 constructor(configuration){this.configuration=configuration;globalThis.__evidenceSynthesisRouteTest.calls.compilerConfigurations.push(structuredClone(configuration));}
 async normalizeClaimComparison(request,signal){const fixture=globalThis.__evidenceSynthesisRouteTest;fixture.calls.normalizations.push({request:structuredClone(request),signal});if(fixture.scenario.normalizeError)throw fixture.scenario.normalizeError;return structuredClone(fixture.scenario.normalization);}
 async analyzeClaimComparisons(request,signal){const fixture=globalThis.__evidenceSynthesisRouteTest;fixture.calls.analyses.push({request:structuredClone(request),signal});if(fixture.scenario.analysisError)throw fixture.scenario.analysisError;return structuredClone(fixture.scenario.envelopeFactory(request));}
}
`;
const clientStub = `
export class RetrievalClient{
 constructor(configuration){globalThis.__evidenceSynthesisRouteTest.calls.retrievalConfigurations.push(structuredClone(configuration));}
 async search(request,snapshotIds,signal){
  const fixture=globalThis.__evidenceSynthesisRouteTest;
  fixture.calls.searches.push({request:structuredClone(request),snapshotIds:[...snapshotIds],signal});
  if(fixture.scenario.searchError)throw fixture.scenario.searchError;
  if(request.stage==="preview")return {query_plan_handoff:"v1.fixture.handoff.signature",...structuredClone(fixture.scenario.searchResponse)};
  if(fixture.scenario.rerankResponse)return structuredClone(fixture.scenario.rerankResponse);
  const previewByRef=new Map(fixture.scenario.searchResponse.preview.map((item)=>[item.snapshot_id+"\\0"+item.claim_id,item]));
  const clusters=request.candidate_refs.map((ref,index)=>{
   const preview=previewByRef.get(ref.snapshot_id+"\\0"+ref.claim_id);
   const member={...preview,...ref,relation:"related",relation_direction:"result_to_query",relation_confidence:0.75,scope_relation:"overlap",scores:preview?.scores??{lexical:null,dense:null,rrf:0.5,rerank:null}};
   return {cluster_id:"cluster:related:"+index,relation:"related",representative:member,member_refs:[member],distinct_paper_families:1};
  });
  return {stage:"ai_rerank",preview:[],clusters,channels:{...fixture.scenario.searchResponse.channels,relation:{attempted:true,status:"ok",provider:"fixture-relation",returned:clusters.length}},warnings:[]};
 }
}
`;
const syncStub = `
export async function ensureRetrievalCoverage(_db,_retrieval,manifests,signal){const fixture=globalThis.__evidenceSynthesisRouteTest;fixture.calls.synchronizations.push({manifests:structuredClone(manifests),signal});if(fixture.scenario.syncError)throw fixture.scenario.syncError;return structuredClone(fixture.scenario.sync);}
`;
const loader = `
let rootUrl,storeUrl,graphUrl,comparisonUrl,retrievalUrl,compilerUrl,clientUrl,syncUrl;
export function initialize(data){({rootUrl,storeUrl,graphUrl,comparisonUrl,retrievalUrl,compilerUrl,clientUrl,syncUrl}=data);}
export async function resolve(specifier,context,nextResolve){
 if(specifier==="@/lib/server/evidence-store")return {url:storeUrl,shortCircuit:true};
 if(specifier==="@/lib/evidence-graph")return {url:graphUrl,shortCircuit:true};
 if(specifier==="@/lib/claim-comparison")return {url:comparisonUrl,shortCircuit:true};
 if(specifier==="@/lib/evidence-retrieval")return {url:retrievalUrl,shortCircuit:true};
 if(specifier==="@/lib/server/compiler-client")return {url:compilerUrl,shortCircuit:true};
 if(specifier==="@/lib/server/retrieval-client")return {url:clientUrl,shortCircuit:true};
 if(specifier==="@/lib/server/retrieval-sync")return {url:syncUrl,shortCircuit:true};
 if(specifier.startsWith("@/")){let path=specifier.slice(2);if(!/\\.[cm]?[jt]sx?$/.test(path))path+=".ts";return {url:new URL(path,rootUrl).href,shortCircuit:true};}
 return nextResolve(specifier,context);
}`;
const asDataUrl = (source) => `data:text/javascript,${encodeURIComponent(source)}`;
register(asDataUrl(loader), {
  parentURL: import.meta.url,
  data: {
    rootUrl: new URL("../", import.meta.url).href,
    storeUrl: asDataUrl(storeStub),
    graphUrl: asDataUrl(graphStub),
    comparisonUrl: asDataUrl(comparisonStub),
    retrievalUrl: asDataUrl(retrievalStub),
    compilerUrl: asDataUrl(compilerStub),
    clientUrl: asDataUrl(clientStub),
    syncUrl: asDataUrl(syncStub),
  },
});

const { POST } = await import("../app/api/evidence-synthesis/route.ts");

function scope(overrides = {}) {
  return {
    location: ["Pacific Ocean"], time: null, formation: [], sample_type: ["deep-sea mud"], method: [],
    condition: [], measurement: ["rare earth elements"], uncertainty: null, entities: ["REE"], ...overrides,
  };
}

function manifest(index, familyIndex = index) {
  const digest = index.toString(16).padStart(64, "0");
  return {
    snapshotId: `snapshot:sha256:${digest}`, paperId: `paper:${index}`,
    paperFamilyId: `artifact:${familyIndex}`, documentCount: 1,
    rootArtifactVersionId: `artifact-version:sha256:${digest}`, rootArtifactSha256: digest,
  };
}

function documentFor(item, index = 1) {
  const claimId = `claim:${item.paperId.replace("paper:", "")}:${index}`;
  const anchorId = `anchor:${item.paperId.replace("paper:", "")}:${index}`;
  return {
    snapshotId: item.snapshotId, paperId: item.paperId, paperFamilyId: item.paperFamilyId,
    rootArtifactVersionId: item.rootArtifactVersionId, claimId,
    canonicalClaim: `Fish debris can host REE enrichment in paper ${item.paperId}.`,
    claimType: "mechanistic", polarity: "affirmed", modality: "asserted", scope: scope(),
    evidenceAnchors: [{ anchorId, page: 4, section: "Results", quote: `Fish debris evidence for ${item.paperId}.`,
      figure: null, table: null, regionLabel: null }],
    shortestEvidencePath: { rootClaimId: claimId,
      nodes: [{ id: anchorId, kind: "source_anchor", label: "Results, page 4" },
        { id: claimId, kind: "atomic_claim", label: "REE host interpretation" }],
      links: [{ fromId: anchorId, toId: claimId }], depth: 1, meanEdgeConfidence: 0.9 },
  };
}

function evidenceFor(document) {
  return {
    id: `observation:${document.claimId}`, kind: "observation",
    text: `Measured REE concentrations in fish debris for ${document.paperId}.`,
    anchor_ids: [document.evidenceAnchors[0].anchorId], root_claim_ids: [document.claimId],
  };
}

function previewFor(document) {
  return {
    snapshot_id: document.snapshotId, claim_id: document.claimId, paper_id: document.paperId,
    paper_family_id: document.paperFamilyId,
    scores: { lexical: 0.8, dense: 0.7, rrf: 0.6, rerank: null },
  };
}

function clusterFor(document, relation, index) {
  const member = {
    ...previewFor(document), relation, relation_direction: "result_to_query",
    relation_confidence: 0.86, scope_relation: "overlap",
  };
  return {
    cluster_id: `cluster:${relation}:${index}`, relation, representative: member,
    member_refs: [member], distinct_paper_families: 1,
  };
}

function channels(status = "ok") {
  const item = { attempted: true, status, provider: "fixture", returned: 1 };
  return { lexical: item, dense: item, rerank: item, relation: item };
}

function rerankResult(clusters, status = "ok", warnings = []) {
  return {
    stage: "ai_rerank", preview: [], clusters,
    channels: {
      ...channels(),
      relation: {
        attempted: true, status, provider: "fixture-relation",
        returned: clusters.reduce((total, cluster) => total + cluster.member_refs.length, 0),
      },
    },
    warnings,
  };
}

class ScenarioDb {
  prepare(sql) {
    state.calls.sql.push(sql);
    return {
      parameters: [],
      bind(...parameters) { this.parameters = parameters; return this; },
      async all() {
        const snapshotIds = JSON.parse(this.parameters[0]);
        if (sql.includes("s.graph_json AS graphJson")) {
          return { results: snapshotIds.flatMap((snapshotId) => {
            const graph = state.scenario.graphs.get(snapshotId);
            return graph ? [{ snapshotId, graphJson: JSON.stringify(graph) }] : [];
          }) };
        }
        return { results: snapshotIds.flatMap((snapshotId) => {
          const source = state.scenario.sources.get(snapshotId);
          return source ? [structuredClone(source)] : [];
        }) };
      },
    };
  }
}

function verification(pair, overrides = {}) {
  return {
    pairId: pair.id, comparisonId: `comparison:${pair.candidate.claim_id}`, verdict: "maintained",
    relation: "supports", direction: "candidate_to_query", stance: "supportive",
    scopeAlignment: "equivalent", directness: "direct", confidence: 0.92,
    rationale: "The candidate independently supports the same scoped proposition.", limitations: [],
    queryEvidenceIds: pair.query.evidence_entities.map((entity) => entity.id),
    candidateEvidenceIds: pair.candidate.evidence_entities.map((entity) => entity.id), ...overrides,
  };
}

function resetScenario(overrides = {}) {
  state.calls = {
    configurations: 0, compilerConfigurations: [], retrievalConfigurations: [], parsedSearches: [],
    resolutions: [], normalizations: [], analyses: [], searches: [], synchronizations: [],
    documentRefs: [], assessmentRefs: [], entityLoads: [], sql: [],
  };
  const manifests = overrides.manifests ?? [manifest(1), manifest(2)];
  const documents = new Map();
  const graphs = new Map();
  const sources = new Map();
  for (const item of manifests) {
    const document = documentFor(item);
    documents.set(`${document.snapshotId}\0${document.claimId}`, document);
    graphs.set(item.snapshotId, { entities: { [document.claimId]: [evidenceFor(document)] } });
    sources.set(item.snapshotId, {
      snapshotId: item.snapshotId, artifactId: item.paperFamilyId, artifactName: `${item.paperId}.pdf`,
      artifactVersionId: item.rootArtifactVersionId, versionNumber: 1, mediaType: "application/pdf",
      sha256: item.rootArtifactSha256,
    });
  }
  state.scenario = {
    db: new ScenarioDb(), manifests, documents, graphs, sources, assessments: new Map(),
    queryDocument: null, configuration: { baseUrl: "http://compiler.test", sharedSecret: "s".repeat(40) },
    normalization: { schemaVersion: "claim-comparison/1.0", status: "needs_user_definition",
      clarification: "Please identify the location and measured outcome.", claims: [] },
    searchResponse: { preview: [], channels: channels(), warnings: [] },
    sync: { checkedSnapshots: manifests.length, pendingSnapshots: 0,
      durationMs: 4, warnings: [] },
    envelopeFactory: (request) => ({ verification: { assessments: request.pairs.map((pair) => verification(pair)) } }),
    ...overrides,
  };
  return state.scenario;
}

function normalizedSource(overrides = {}) {
  return {
    kind: "normalized_claim", claim: {
      id: "draft:ree", canonicalClaim: "Fish debris can host REE enrichment in Pacific deep-sea mud.",
      claimType: "mechanistic", polarity: "affirmed", modality: "asserted", scope: scope(), ...overrides,
    },
  };
}

function request(body, options = {}) {
  return new Request("https://evidence.example.test/api/evidence-synthesis", {
    method: "POST", headers: { "content-type": "application/json", ...options.headers },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}

test("normalize preserves unresolved research questions without manufacturing an affirmative answer", async () => {
  resetScenario();
  const response = await POST(request({ action: "normalize", source: { kind: "viewpoint", text: "  Why is REE-rich mud enriched?  " } }));
  const body = await response.json();
  assert.equal(response.status, 200);
  assert.equal(body.action, "normalize");
  assert.equal(body.normalization.status, "needs_user_definition");
  assert.deepEqual(body.normalization.claims, []);
  assert.deepEqual(state.calls.normalizations[0].request, {
    schema_version: "claim-comparison/1.0", project_id: PROJECT_ID,
    user_text: "Why is REE-rich mud enriched?",
  });
  assert.ok(state.calls.normalizations[0].signal instanceof AbortSignal);
  assert.equal(state.calls.searches.length, 0);
});

test("async normalization failures are returned as their precise public API error", async () => {
  resetScenario({ configuration: { baseUrl: "http://compiler.test", sharedSecret: null } });
  const response = await POST(request({ action: "normalize", source: { kind: "viewpoint", text: "REE enrichment may involve fish debris." } }));
  const body = await response.json();
  assert.equal(response.status, 503);
  assert.equal(body.error.code, "CLAIM_COMPARISON_NOT_CONFIGURED");
  assert.equal(state.calls.normalizations.length, 0);
});

test("async comparison corpus failures retain the specific stored-corpus error", async () => {
  resetScenario({ storeError: true });
  const response = await POST(request({ action: "compare", source: normalizedSource() }));
  const body = await response.json();
  assert.equal(response.status, 409);
  assert.equal(body.error.code, "RETRIEVAL_STORE_INVALID");
});

test("async retrieval-contract failures preserve their path and public 400 classification", async () => {
  resetScenario({ contractError: true });
  const response = await POST(request({ action: "compare", source: normalizedSource() }));
  const body = await response.json();
  assert.equal(response.status, 400);
  assert.equal(body.error.code, "INVALID_EVIDENCE_SYNTHESIS");
  assert.deepEqual(body.error.details, { path: "$.query" });
});

test("normalization rejects cross-origin writes before reading or calling Compiler", async () => {
  resetScenario();
  const response = await POST(request({ action: "normalize", source: { kind: "viewpoint", text: "REE" } }, {
    headers: { origin: "https://malicious.example.test" },
  }));
  assert.equal(response.status, 403);
  assert.equal((await response.json()).error.code, "CROSS_ORIGIN_WRITE_REJECTED");
  assert.equal(state.calls.configurations, 0);
});

test("normalization rejects oversized body, excessive viewpoint characters, and open request shapes", async () => {
  for (const scenario of [
    { body: { action: "normalize", source: { kind: "viewpoint", text: "a".repeat(140_000) } }, status: 413, code: "REQUEST_TOO_LARGE" },
    { body: { action: "normalize", source: { kind: "viewpoint", text: "a".repeat(32_001) } }, status: 400, code: "INVALID_EVIDENCE_SYNTHESIS" },
    { body: { action: "normalize", source: { kind: "viewpoint", text: "REE", untrusted: true } }, status: 400, code: "INVALID_EVIDENCE_SYNTHESIS" },
    { body: { action: "normalize", source: { kind: "paper_claim", text: "REE" } }, status: 400, code: "INVALID_EVIDENCE_SYNTHESIS" },
    { body: { action: "unknown", source: {} }, status: 400, code: "INVALID_EVIDENCE_SYNTHESIS" },
  ]) {
    resetScenario();
    const response = await POST(request(scenario.body));
    assert.equal(response.status, scenario.status);
    assert.equal((await response.json()).error.code, scenario.code);
    assert.equal(state.calls.normalizations.length, 0);
  }
});

test("comparison rejects malformed scientific scope, inverted time, duplicate entities, and invalid snapshot IDs", async () => {
  const invalidSources = [
    normalizedSource({ scope: scope({ entities: ["REE", "ree"] }) }),
    normalizedSource({ scope: scope({ time: { label: null, start: 5, end: 2, unit: "Ma" } }) }),
    normalizedSource({ scope: scope({ time: { label: null, start: 1, end: 2, unit: null } }) }),
    normalizedSource({ polarity: "proved" }),
    { kind: "paper_claim", snapshotId: "snapshot:bad", claimId: "claim:1" },
    { kind: "normalized_claim", claim: { ...normalizedSource().claim, trusted: true } },
  ];
  for (const source of invalidSources) {
    resetScenario();
    const response = await POST(request({ action: "compare", source }));
    assert.equal(response.status, 400);
    assert.equal((await response.json()).error.code, "INVALID_EVIDENCE_SYNTHESIS");
    assert.equal(state.calls.searches.length, 0);
  }
});

test("an empty corpus reports honest incomplete coverage without invoking retrieval or model", async () => {
  resetScenario({ manifests: [] });
  const response = await POST(request({ action: "compare", source: normalizedSource() }));
  const body = await response.json();
  assert.equal(response.status, 200);
  assert.equal(body.coverage.complete, false);
  assert.equal(body.coverage.activeClaims, 0);
  assert.equal(body.coverage.retrievalLimit, 12);
  assert.deepEqual(body.groups, { support: [], contradict: [], qualify: [], context: [] });
  assert.equal(state.calls.searches.length, 0);
  assert.equal(state.calls.analyses.length, 0);
});

test("an unavailable selected paper Claim returns a stable 404 without model calls", async () => {
  const sourceManifest = manifest(1);
  resetScenario({ manifests: [sourceManifest], queryDocument: null });
  const response = await POST(request({ action: "compare", source: {
    kind: "paper_claim", snapshotId: sourceManifest.snapshotId, claimId: "claim:missing",
  } }));
  assert.equal(response.status, 404);
  assert.equal((await response.json()).error.code, "QUERY_CLAIM_NOT_FOUND");
  assert.equal(state.calls.analyses.length, 0);
});

test("same-paper-family claims are excluded without inventing independent corroboration", async () => {
  const first = manifest(1, 7);
  const sameFamily = manifest(2, 7);
  const scenario = resetScenario({ manifests: [first, sameFamily] });
  scenario.queryDocument = documentFor(first);
  const response = await POST(request({ action: "compare", source: {
    kind: "paper_claim", snapshotId: first.snapshotId, claimId: scenario.queryDocument.claimId,
  } }));
  const body = await response.json();
  assert.equal(response.status, 200);
  assert.equal(body.coverage.excludedSourceFamily, 2);
  assert.equal(body.coverage.acceptedRelations, 0);
  assert.equal(body.coverage.complete, false);
  assert.equal(state.calls.searches.length, 0);
  assert.equal(state.calls.analyses.length, 0);
});

test("retrieval degradation is honestly surfaced rather than returning fabricated evidence", async () => {
  resetScenario({ searchResponse: { preview: [], channels: channels("failed"), warnings: ["Semantic index temporarily unavailable."] } });
  const response = await POST(request({ action: "compare", source: normalizedSource() }));
  const body = await response.json();
  assert.equal(response.status, 200);
  assert.equal(body.coverage.acceptedRelations, 0);
  assert.equal(body.coverage.complete, false);
  assert.ok(body.warnings.some((warning) => warning.includes("暂时受限")));
  assert.ok(body.warnings.includes("Semantic index temporarily unavailable."));
  assert.equal(state.calls.analyses.length, 0);
});

test("relation rerank keeps related and strong candidates while excluding unrelated previews", async () => {
  const manifests = Array.from({ length: 3 }, (_, index) => manifest(index + 100));
  const scenario = resetScenario({ manifests });
  const documents = manifests.map((item) => documentFor(item));
  scenario.searchResponse.preview = documents.map(previewFor);
  scenario.rerankResponse = rerankResult([
    clusterFor(documents[0], "related", 0),
    clusterFor(documents[1], "supports", 1),
    clusterFor(documents[2], "unrelated", 2),
  ]);

  const response = await POST(request({ action: "compare", source: normalizedSource() }));
  const body = await response.json();

  assert.equal(response.status, 200);
  assert.deepEqual(state.calls.searches.map((call) => call.request.stage), ["preview", "ai_rerank"]);
  assert.deepEqual(state.calls.searches[1].request.candidate_refs, documents.map((document) => ({
    snapshot_id: document.snapshotId,
    claim_id: document.claimId,
  })));
  assert.equal(state.calls.analyses.length, 1);
  assert.deepEqual(
    state.calls.analyses[0].request.pairs.map((pair) => pair.candidate.claim_id),
    [documents[0].claimId, documents[1].claimId],
  );
  assert.equal(body.coverage.retrievedCandidates, 3);
  assert.equal(body.coverage.comparedCandidates, 2);
});

test("an empty successful relation rerank returns zero comparisons without analysis", async () => {
  const manifests = Array.from({ length: 2 }, (_, index) => manifest(index + 110));
  const scenario = resetScenario({ manifests });
  scenario.searchResponse.preview = manifests.map((item) => previewFor(documentFor(item)));
  scenario.rerankResponse = rerankResult([]);

  const response = await POST(request({ action: "compare", source: normalizedSource() }));
  const body = await response.json();

  assert.equal(response.status, 200);
  assert.deepEqual(state.calls.searches.map((call) => call.request.stage), ["preview", "ai_rerank"]);
  assert.equal(body.coverage.retrievedCandidates, 2);
  assert.equal(body.coverage.comparedCandidates, 0);
  assert.equal(body.coverage.acceptedRelations, 0);
  assert.deepEqual(body.groups, { support: [], contradict: [], qualify: [], context: [] });
  assert.equal(state.calls.analyses.length, 0);
});

test("a degraded relation rerank fails without returning candidate answers or claim analysis", async () => {
  const candidateManifest = manifest(120);
  const scenario = resetScenario({ manifests: [candidateManifest] });
  const candidate = documentFor(candidateManifest);
  scenario.searchResponse.preview = [previewFor(candidate)];
  scenario.rerankResponse = rerankResult([], "degraded", ["Relation model unavailable"]);

  const response = await POST(request({ action: "compare", source: normalizedSource() }));
  const body = await response.json();

  assert.equal(response.status, 503);
  assert.equal(body.error.code, "CLAIM_RELATION_CLASSIFIER_UNAVAILABLE");
  assert.equal(body.groups, undefined);
  assert.equal(body.previewResults, undefined);
  assert.deepEqual(state.calls.searches.map((call) => call.request.stage), ["preview", "ai_rerank"]);
  assert.equal(state.calls.analyses.length, 0);
});

test("comparison uses only independently verified assessments and exact immutable evidence", async () => {
  const candidateManifest = manifest(2);
  const scenario = resetScenario({ manifests: [candidateManifest] });
  const candidate = documentFor(candidateManifest);
  scenario.searchResponse.preview = [previewFor(candidate)];
  const response = await POST(request({ action: "compare", source: normalizedSource() }));
  const body = await response.json();
  assert.equal(response.status, 200);
  assert.equal(body.groups.support.length, 1);
  const result = body.groups.support[0];
  assert.equal(result.candidate.paperId, candidate.paperId);
  assert.equal(result.candidate.claim.id, candidate.claimId);
  assert.equal(result.candidate.evidenceAnchors[0].page, 4);
  assert.equal(result.candidate.evidenceAnchors[0].quote, "Fish debris evidence for paper:2.");
  assert.equal(result.selectedEvidence.candidate[0].id, `observation:${candidate.claimId}`);
  assert.equal(result.candidate.source.downloadUrl, `/api/artifacts/${candidateManifest.rootArtifactSha256}`);
  assert.equal(body.coverage.comparedCandidates, 1);
  assert.equal(body.coverage.acceptedRelations, 1);
  assert.equal(body.coverage.complete, false);
  assert.equal(state.calls.analyses.length, 1);
  assert.ok(state.calls.analyses[0].signal instanceof AbortSignal);
});

test("narrower support is qualified and disjoint, unknown, or abstained conclusions are never published", async () => {
  const manifests = Array.from({ length: 5 }, (_, index) => manifest(index + 20));
  const scenario = resetScenario({ manifests });
  scenario.searchResponse.preview = manifests.map((item) => previewFor(documentFor(item)));
  scenario.envelopeFactory = (request) => ({ verification: { assessments: request.pairs.map((pair, index) => verification(pair,
    index === 0 ? { scopeAlignment: "candidate_within_query" }
      : index === 1 ? { scopeAlignment: "disjoint" }
        : index === 2 ? { scopeAlignment: "unknown" }
          : index === 3 ? { verdict: "abstained" }
            : { relation: "contradicts", stance: "contradictory", scopeAlignment: "overlap" },
  )) } });
  const response = await POST(request({ action: "compare", source: normalizedSource() }));
  const body = await response.json();
  assert.equal(response.status, 200);
  assert.equal(body.groups.support.length, 0);
  assert.equal(body.groups.contradict.length, 0);
  assert.equal(body.groups.qualify.length, 2);
  assert.equal(body.coverage.comparedCandidates, 5);
  assert.equal(body.coverage.acceptedRelations, 2);
});

test("query-to-candidate support remains context, and missing independent verification publishes nothing", async () => {
  for (const scenarioType of ["wrong_direction", "no_verifier"]) {
    const candidateManifest = manifest(41);
    const scenario = resetScenario({ manifests: [candidateManifest] });
    scenario.searchResponse.preview = [previewFor(documentFor(candidateManifest))];
    scenario.envelopeFactory = scenarioType === "no_verifier"
      ? () => ({ verification: null })
      : (request) => ({ verification: { assessments: [verification(request.pairs[0], { direction: "query_to_candidate" })] } });
    const response = await POST(request({ action: "compare", source: normalizedSource() }));
    const body = await response.json();
    assert.equal(response.status, 200);
    assert.equal(body.groups.support.length, 0);
    assert.equal(body.groups.context.length, scenarioType === "wrong_direction" ? 1 : 0);
  }
});

test("retrieval candidates are deduplicated and strictly capped at twelve before model analysis", async () => {
  const manifests = Array.from({ length: 15 }, (_, index) => manifest(index + 50));
  const scenario = resetScenario({ manifests });
  const preview = manifests.map((item) => previewFor(documentFor(item)));
  scenario.searchResponse.preview = [preview[0], preview[0], ...preview.slice(1)];
  const response = await POST(request({ action: "compare", source: normalizedSource() }));
  const body = await response.json();
  assert.equal(response.status, 200);
  assert.equal(body.coverage.retrievedCandidates, 16);
  assert.equal(body.coverage.comparedCandidates, 12);
  assert.equal(body.groups.support.length, 12);
  assert.equal(state.calls.analyses[0].request.pairs.length, 12);
  assert.equal(new Set(state.calls.analyses[0].request.pairs.map((pair) => pair.id)).size, 12);
});

test("retrieved same-family preview entries are removed before comparison", async () => {
  const first = manifest(70, 7);
  const sibling = manifest(71, 7);
  const independent = manifest(72, 9);
  const scenario = resetScenario({ manifests: [first, sibling, independent] });
  scenario.queryDocument = documentFor(first);
  scenario.searchResponse.preview = [previewFor(documentFor(sibling)), previewFor(documentFor(independent))];
  const response = await POST(request({ action: "compare", source: {
    kind: "paper_claim", snapshotId: first.snapshotId, claimId: scenario.queryDocument.claimId,
  } }));
  const body = await response.json();
  assert.equal(response.status, 200);
  assert.equal(body.groups.support.length, 1);
  assert.equal(body.groups.support[0].candidate.paperFamilyId, "artifact:9");
  assert.equal(body.coverage.excludedSourceFamily, 3);
  assert.equal(state.calls.analyses[0].request.pairs.length, 1);
});

test("mismatched immutable sources and unknown verifier pair IDs fail closed", async () => {
  for (const invalid of ["source", "pair", "document", "graph"]) {
    const candidateManifest = manifest(80);
    const scenario = resetScenario({ manifests: [candidateManifest] });
    const document = documentFor(candidateManifest);
    scenario.searchResponse.preview = [previewFor(document)];
    if (invalid === "source") scenario.sources.get(candidateManifest.snapshotId).artifactId = "artifact:attacker";
    if (invalid === "document") scenario.documents.clear();
    if (invalid === "graph") scenario.graphError = new Error("corrupt graph");
    if (invalid === "pair") scenario.envelopeFactory = (request) => ({ verification: {
      assessments: [verification(request.pairs[0], { pairId: "pair:unknown" })],
    } });
    const response = await POST(request({ action: "compare", source: normalizedSource() }));
    const body = await response.json();
    assert.equal(response.status, invalid === "graph" ? 409 : 502);
    assert.equal(body.error.code,
      invalid === "source" ? "RETRIEVAL_REFERENCE_MISMATCH"
        : invalid === "document" ? "RETRIEVAL_REFERENCE_NOT_FOUND"
          : invalid === "graph" ? "STORED_SNAPSHOT_INCOMPATIBLE"
            : "CLAIM_COMPARISON_PAIR_MISMATCH");
  }
});

test("public evidence responses remain bounded and never expose unselected evidence", async () => {
  const candidateManifest = manifest(90);
  const scenario = resetScenario({ manifests: [candidateManifest] });
  const document = scenario.documents.get(`${candidateManifest.snapshotId}\0${documentFor(candidateManifest).claimId}`);
  document.evidenceAnchors[0].quote = "证".repeat(3_000);
  document.shortestEvidencePath.nodes = Array.from({ length: 30 }, (_, index) => ({
    id: `node:${index}`, kind: "observation", label: "路".repeat(900),
  }));
  document.shortestEvidencePath.links = Array.from({ length: 29 }, (_, index) => ({
    fromId: `node:${index}`, toId: `node:${index + 1}`,
  }));
  const entities = scenario.graphs.get(candidateManifest.snapshotId).entities[document.claimId];
  entities[0].text = "证".repeat(3_000);
  entities.push({ ...entities[0], id: "observation:must-not-leak", text: "unselected private context" });
  scenario.searchResponse.preview = [previewFor(document)];
  scenario.envelopeFactory = (analysis) => ({ verification: { assessments: [verification(analysis.pairs[0], {
    candidateEvidenceIds: [entities[0].id],
  })] } });
  const response = await POST(request({ action: "compare", source: normalizedSource() }));
  const result = (await response.json()).groups.support[0];
  assert.equal(Array.from(result.candidate.evidenceAnchors[0].quote).length, 2_000);
  assert.equal(Array.from(result.selectedEvidence.candidate[0].text).length, 2_000);
  assert.equal(result.selectedEvidence.candidate.length, 1);
  assert.equal(result.candidate.shortestEvidencePath.nodes.length, 24);
  assert.equal(Array.from(result.candidate.shortestEvidencePath.nodes[0].label).length, 800);
  assert.equal(result.candidate.shortestEvidencePath.truncated, true);
  assert.ok(result.candidate.shortestEvidencePath.links.every((link) =>
    result.candidate.shortestEvidencePath.nodes.some((node) => node.id === link.fromId)
    && result.candidate.shortestEvidencePath.nodes.some((node) => node.id === link.toId)));
});

test("the comparison final ranks the sealed preview under its own model plan", async () => {
  const manifests = [manifest(90), manifest(91)];
  const scenario = resetScenario({ manifests });
  scenario.searchResponse.preview = manifests.map((item) => previewFor(documentFor(item)));
  const response = await POST(request({ action: "compare", source: normalizedSource() }));
  assert.equal(response.status, 200);
  const [preview, final] = state.calls.parsedSearches;
  assert.equal(preview.stage, "preview");
  assert.equal(final.stage, "ai_rerank");
  // The handoff never leaves the server, and the final carries exactly the
  // preview's ordered candidates so the Compiler can verify the seal.
  assert.equal(final.query_plan_handoff, "v1.fixture.handoff.signature");
  assert.deepEqual(final.candidate_refs, scenario.searchResponse.preview.map((item) => ({
    snapshot_id: item.snapshot_id, claim_id: item.claim_id,
  })));
});
