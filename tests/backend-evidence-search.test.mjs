import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { register } from "node:module";
import test from "node:test";
import ts from "typescript";
import { parseEvidenceSearchResponse } from "../app/evidence-search-response.ts";
import { contextHistoryError, contextTextError, continueContextRequest } from "../app/search-context.ts";

const INDEX_VERSION = "evidence-retrieval/2.0";
const SEARCH_VERSION = "evidence-retrieval/2.1";
const QUERY_PLAN_HANDOFF = `v1.derived-auth-v1.e30.${"a".repeat(43)}`;
const CONTEXT_CLARIFICATION_TOKEN = `c2.derived-auth-v1.e30.${"b".repeat(43)}`;
const claimId = (character) => `claim:sha256:${character.repeat(64)}`;
const state = { scenario: null, calls: null, sessions: new Map() };
globalThis.__evidenceSearchRouteTest = state;

const rootUrl = new URL("../", import.meta.url);
const actualRetrievalUrl = new URL("lib/evidence-retrieval/index.ts", rootUrl).href;
const retrievalStub = `
export * from ${JSON.stringify(actualRetrievalUrl)};
import { EvidenceRetrievalStoreError } from ${JSON.stringify(actualRetrievalUrl)};
export async function resolveEvidenceRetrievalCorpus(_db, _projectId, request) {
  const scenario = globalThis.__evidenceSearchRouteTest.scenario;
  if (scenario.resolveError) {
    throw new EvidenceRetrievalStoreError(
      scenario.resolveError.status,
      scenario.resolveError.code,
      scenario.resolveError.message,
    );
  }
  return {request, queryText: request.query ?? scenario.queryText, queryDocument: scenario.queryDocument??null, manifests: scenario.manifests};
}
export async function loadEvidenceRetrievalIndexBundles(_db, _projectId, snapshotIds) {
  const fixture = globalThis.__evidenceSearchRouteTest;
  fixture.calls.bundleLoads.push([...snapshotIds]);
  return snapshotIds.map((id) => fixture.scenario.indexBundles.get(id)).filter(Boolean);
}
export async function loadEvidenceRetrievalDocumentsByRefs(_db, _projectId, refs) {
  const fixture = globalThis.__evidenceSearchRouteTest;
  fixture.calls.order.push("hydrate-documents");
  fixture.calls.documentRefs.push(refs.map((ref) => ({...ref})));
  return refs.map((ref) => fixture.scenario.documents.get(ref.snapshotId + "\\0" + ref.claimId)).filter(Boolean);
}
export async function loadLatestEvidenceRetrievalAssessmentsByRefs(_db, _projectId, refs) {
  const fixture = globalThis.__evidenceSearchRouteTest;
  fixture.calls.order.push("hydrate-assessments");
  fixture.calls.assessmentRefs.push(refs.map((ref) => ({...ref})));
  return refs.map((ref) => fixture.scenario.assessments.get(ref.snapshotId + "\\0" + ref.claimId)).filter(Boolean);
}
export function toEvidenceRetrievalManifestDto(manifest) {
  return {index_version:manifest.indexVersion,snapshot_id:manifest.snapshotId,paper_id:manifest.paperId,
    paper_family_id:manifest.paperFamilyId,root_artifact_version_id:manifest.rootArtifactVersionId,
    root_artifact_sha256:manifest.rootArtifactSha256,graph_sha256:manifest.graphSha256,
    graph_schema_version:manifest.graphSchemaVersion,extractor_prompt_version:manifest.extractorPromptVersion,
    document_count:manifest.documentCount,created_at:manifest.createdAt};
}
export function toEvidenceRetrievalIndexRequestDto(projectId, bundle) {
  return {schema_version:${JSON.stringify(INDEX_VERSION)},project_id:projectId,
    manifest:toEvidenceRetrievalManifestDto(bundle.manifest),documents:bundle.documents.map((document) => ({
      index_version:${JSON.stringify(INDEX_VERSION)},snapshot_id:document.snapshotId,claim_id:document.claimId,
      canonical_claim:document.canonicalClaim}))};
}
`;
const clientStub = `
import { ApiError } from "@/lib/server/evidence-store";
export const RETRIEVAL_MAX_INDEX_DOCUMENTS = 2048;
export const RETRIEVAL_MAX_MANIFESTS = 4096;
export class RetrievalClient {
  constructor(configuration) { globalThis.__evidenceSearchRouteTest.calls.configuration = configuration; }
  async checkManifests(projectId, manifests, signal) {
    const fixture=globalThis.__evidenceSearchRouteTest; fixture.calls.order.push("check");
    fixture.calls.checks.push({projectId,manifests,signal});
    if (fixture.scenario.checkHook) await fixture.scenario.checkHook(signal);
    return {schema_version:${JSON.stringify(INDEX_VERSION)},manifests:manifests.map((item) => fixture.scenario.manifestState(item))};
  }
  async indexSnapshot(projectId, manifest, documents, signal) {
    const fixture=globalThis.__evidenceSearchRouteTest; fixture.calls.order.push("index");
    fixture.calls.indexes.push({projectId,manifest,documents,signal});
    if (fixture.scenario.indexHook) await fixture.scenario.indexHook(signal);
    const indexed=fixture.scenario.indexedCounts.get(manifest.snapshot_id)??0;
    const next=Math.min(manifest.document_count,indexed+documents.length); fixture.scenario.indexedCounts.set(manifest.snapshot_id,next);
    return {schema_version:${JSON.stringify(INDEX_VERSION)},manifests:[{snapshot_id:manifest.snapshot_id,
      status:next===manifest.document_count?"indexed":"degraded",indexed_document_count:next,
      document_count:manifest.document_count,manifest_sha256:"d".repeat(64),dense_complete:next===manifest.document_count}],
      indexed_documents:documents.length,warnings:[]};
  }
  async search(request, allowedSnapshotIds, signal) {
    const fixture=globalThis.__evidenceSearchRouteTest; fixture.calls.order.push("search");
    const searchIndex=fixture.calls.searches.length;
    fixture.calls.searches.push({request,allowedSnapshotIds,signal});
    const configuredError=fixture.scenario.searchErrors?.[searchIndex]??fixture.scenario.searchError;
    if(configuredError?.apiError){
      throw new ApiError(configuredError.status,configuredError.code,configuredError.message,configuredError.details);
    }
    if(configuredError)throw configuredError;
    const response=fixture.scenario.searchResponses?.[searchIndex]??fixture.scenario.searchResponse;
    return structuredClone(response);
  }
}
`;
const storeStub = `
export const PROJECT_ID="DEEPSHOVEL-PROJECT";
export class ApiError extends Error {constructor(status,code,message,details){super(message);this.status=status;this.code=code;this.details=details;}}
export async function getBindings(){const fixture=globalThis.__evidenceSearchRouteTest;fixture.calls.bindingReads+=1;return {DB:fixture.scenario.db};}
export async function getCompilerConfiguration(){return {baseUrl:"https://retrieval.example.test",sharedSecret:"s".repeat(40)};}
export async function readJsonObject(request,maximum){const text=await request.text();if(new TextEncoder().encode(text).byteLength>maximum)throw new ApiError(413,"REQUEST_TOO_LARGE","request too large");return JSON.parse(text);}
export function safeError(error){if(error&&typeof error.status==="number")return Response.json({ok:false,error:{code:error.code,message:error.message,details:error.details}},{status:error.status});return Response.json({ok:false,error:{code:"INTERNAL_ERROR",message:String(error)}},{status:500});}
`;
// Server-side continuation sessions: the browser only ever sees opaque rs1. IDs.
const sessionsStub = `
export async function createRetrievalSession(_db, kind, token) {
  const sessions=globalThis.__evidenceSearchRouteTest.sessions;
  const id="rs1."+String(sessions.size+1).padStart(43,"0");
  sessions.set(id,{kind,token});
  return id;
}
export async function resolveRetrievalSession(_db, kind, id) {
  const entry=globalThis.__evidenceSearchRouteTest.sessions.get(id);
  return entry&&entry.kind===kind?entry.token:null;
}
`;
const workersStub = `
export const env=new Proxy({}, {get(_target,key){const fixture=globalThis.__evidenceSearchRouteTest;fixture.calls.featureReads+=1;return fixture.scenario?.bindings?.[key];}});
`;
const loader = `
let rootUrl,retrievalUrl,clientUrl,storeUrl,workersUrl,sessionsUrl;
export function initialize(data){({rootUrl,retrievalUrl,clientUrl,storeUrl,workersUrl,sessionsUrl}=data);}
export async function resolve(specifier,context,nextResolve){
  if(specifier==="@/lib/evidence-retrieval")return {url:retrievalUrl,shortCircuit:true};
  if(specifier==="@/lib/server/retrieval-client")return {url:clientUrl,shortCircuit:true};
  if(specifier==="@/lib/server/evidence-store")return {url:storeUrl,shortCircuit:true};
  if(specifier==="@/lib/server/retrieval-sessions")return {url:sessionsUrl,shortCircuit:true};
  if(specifier==="cloudflare:workers")return {url:workersUrl,shortCircuit:true};
  if(specifier.startsWith("@/")){let path=specifier.slice(2);if(!/\\.[cm]?[jt]sx?$/.test(path))path+=".ts";return {url:new URL(path,rootUrl).href,shortCircuit:true};}
  return nextResolve(specifier,context);
}`;
register(`data:text/javascript,${encodeURIComponent(loader)}`, {parentURL:import.meta.url,data:{rootUrl:rootUrl.href,
  retrievalUrl:`data:text/javascript,${encodeURIComponent(retrievalStub)}`,
  clientUrl:`data:text/javascript,${encodeURIComponent(clientStub)}`,
  storeUrl:`data:text/javascript,${encodeURIComponent(storeStub)}`,
  workersUrl:`data:text/javascript,${encodeURIComponent(workersStub)}`,
  sessionsUrl:`data:text/javascript,${encodeURIComponent(sessionsStub)}`}});
const { POST } = await import("../app/api/evidence-search/route.ts");

function manifest(index, documentCount=1) {
  const sha=index.toString(16).padStart(64,"0");
  return {indexVersion:INDEX_VERSION,snapshotId:`snapshot:sha256:${sha}`,paperId:`paper:${index}`,
    paperFamilyId:`artifact:${index}`,rootArtifactVersionId:`artifact-version:sha256:${sha}`,
    rootArtifactSha256:sha,graphSha256:sha,graphSchemaVersion:"scientific-evidence-graph/1.0",
    extractorPromptVersion:"compiler-extractor/1.0.0",documentCount,createdAt:`2026-08-18T00:${String(index%60).padStart(2,"0")}:00.000Z`};
}
function documentFor(item, claim=claimId("a"), originalClaim="The source reports a mercury anomaly.") {
  return {indexVersion:INDEX_VERSION,snapshotId:item.snapshotId,paperId:item.paperId,paperFamilyId:item.paperFamilyId,
    rootArtifactVersionId:item.rootArtifactVersionId,claimId:claim,sourceClaimId:"claim:source:fixture",originalClaim,
    canonicalClaim:"Mercury anomalies accompany volcanism.",propositionKey:`proposition:sha256:${"e".repeat(64)}`,
    scopedClaimId:claim,paperTitle:`Paper ${item.paperId}`,evidenceContext:"[source] Mercury anomaly.",entities:["Hg"],
    keyTerms:["mercury"],abbreviations:[],qualifiers:[],claimType:"correlational",polarity:"affirmed",modality:"asserted",
    attribution:"authors_conclusion",rhetoricalRole:"terminal_conclusion",scope:{location:["Basin X"],time:null,formation:[],
      sample_type:[],method:["ICP-MS"],condition:[],measurement:["Hg"],uncertainty:null,entities:["Hg"]},
    evidenceAnchors:[{anchorId:"anchor:fixture",artifactVersionId:item.rootArtifactVersionId,anchorType:"text",page:4,
      section:"Results",paragraph:2,quote:"The source reports a mercury anomaly.",figure:null,table:null,rowStart:null,
      rowEnd:null,rowRangeLabel:null,columnNames:[],series:null,charStart:10,charEnd:48,region:null,regionLabel:null,datasetPath:null}],
    explicitRelations:[],shortestEvidencePath:{rootClaimId:claim,nodes:[{id:"anchor:fixture",kind:"source_anchor",label:"Results, page 4"}],links:[],depth:1,meanEdgeConfidence:1},
    pathQuality:1,extractorPromptVersion:item.extractorPromptVersion};
}
function memberRef(item, claim=claimId("a"), relation="supports") {
  return {claim_id:claim,snapshot_id:item.snapshotId,paper_id:item.paperId,paper_family_id:item.paperFamilyId,relation,
    relation_direction:"result_to_query",relation_confidence:0.88,scope_relation:"overlap",
    scores:{lexical:4.2,dense:0.83,rrf:0.03,rerank:0.91}};
}
function previewRef(item, claim=claimId("a")) {
  return {claim_id:claim,snapshot_id:item.snapshotId,paper_id:item.paperId,paper_family_id:item.paperFamilyId,
    scores:{lexical:4.2,dense:0.83,rrf:0.03,rerank:0.91}};
}
function retrievalConfidence() {
  return {mode:"observe",decision:"return",would_abstain:null,applicability:"applicable",
    policy_inapplicable_reasons:[],query_profile:"claim",long_query_truncated:false,
    direct_lexical_query_tokens:4,direct_lexical_overlap:2,direct_lexical_coverage:0.5,
    exact_anchor_hits:0,concept_anchor_hits:1,scientific_anchor_hits:0,strong_signal_types:["concept_anchor"],
    dense_observed_hits:2,dense_top1:0.83,dense_top2:0.79,dense_margin:0.04,dense_top3_mean:0.81};
}
function responseWithClusters(clusters=[],schemaVersion=INDEX_VERSION) {
  const returned=new Set(clusters.flatMap((cluster)=>cluster.member_refs.map((member)=>(
    member.snapshot_id+"\0"+member.claim_id
  )))).size;
  const stage={attempted:true,status:"ok",provider:"fixture",returned};
  return {schema_version:schemaVersion,stage:"ai_rerank",result_status:clusters.length?"matches":"no_direct_answer",query_plan:{query_type:"supporting_evidence",original_query:"mercury volcanism",
    canonical_claim:"Mercury anomalies accompany volcanism.",must_terms:["mercury"],optional_terms:["Hg"],
    exclude_terms:["laboratory blank"],entities:["Hg"],polarity:"affirmed",claim_types:["correlational"],
    scope_constraints:null,planner:"deterministic"},channels:{lexical:stage,dense:stage,rerank:stage,relation:stage},preview:[],clusters,warnings:[],retrieval_confidence:null};
}
function responseWithPreview(preview=[], queryPlanHandoff=QUERY_PLAN_HANDOFF) {
  const recalled={attempted:true,status:"ok",provider:"fixture",returned:preview.length};
  const skipped={attempted:false,status:"skipped",provider:"gpt",returned:0};
  return {schema_version:SEARCH_VERSION,stage:"preview",result_status:preview.length?"matches":"no_direct_answer",query_plan:{query_type:"claim_search",original_query:"topoinf 能提高很多gnn方法的效果",
    canonical_claim:"topoinf 能提高很多gnn方法的效果",must_terms:[],optional_terms:["topoinf","gnn"],exclude_terms:[],
    entities:[],polarity:null,claim_types:[],scope_constraints:null,planner:"deterministic"},
    query_plan_handoff:queryPlanHandoff,
    channels:{lexical:recalled,dense:recalled,rerank:recalled,relation:skipped},preview,clusters:[],warnings:[],retrieval_confidence:retrievalConfidence()};
}
class SourceDb {
  sql=[];
  prepare(sql){this.sql.push(sql);return {args:[],bind(...args){this.args=args;return this;},async run(){
    if(sql.includes("INSERT INTO work_items"))state.calls.workRequests.push({kind:this.args[0],subjects:JSON.parse(this.args[8])});
    return {meta:{changes:1}};},async all(){
    const ids=JSON.parse(this.args[0]);return {results:ids.map((id)=>{const item=state.scenario.manifests.find((candidate)=>candidate.snapshotId===id);
      return {snapshotId:id,artifactId:item.paperFamilyId,artifactName:`Paper ${item.paperId}.pdf`,artifactVersionId:item.rootArtifactVersionId,
        versionNumber:1,mediaType:"application/pdf",sha256:item.rootArtifactSha256};})};}};}
}
function resetScenario(overrides={}) {
  const active=overrides.manifests??[manifest(1)],documents=new Map(),indexBundles=new Map();
  for(const item of active){const docs=item.documentCount?[documentFor(item)]:[];indexBundles.set(item.snapshotId,{manifest:item,documents:docs});
    for(const document of docs)documents.set(`${document.snapshotId}\0${document.claimId}`,document);}
  state.calls={configuration:null,bindingReads:0,featureReads:0,checks:[],bundleLoads:[],indexes:[],searches:[],documentRefs:[],assessmentRefs:[],publicResponseBindings:[],order:[],workRequests:[]};
  state.scenario={db:new SourceDb(),queryText:"Mercury anomalies accompany volcanism.",manifests:active,documents,
    assessments:new Map(),indexBundles,indexedCounts:new Map(),manifestState:(dto)=>({snapshot_id:dto.snapshot_id,status:"indexed",
      indexed_document_count:dto.document_count,document_count:dto.document_count,manifest_sha256:"d".repeat(64),dense_complete:true}),
    queryDocument:null,searchResponse:responseWithClusters(),searchResponses:null,searchErrors:null,searchError:null,resolveError:null,
    bindings:{},...overrides};return state.scenario;
}
function session(kind, token){
  const id="rs1."+("s"+String(state.sessions.size+1)).padStart(43,"0");
  state.sessions.set(id,{kind,token});
  return id;
}
function request(body){return new Request("http://localhost/api/evidence-search",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(body)});}

test("local quick preview bypasses Compiler configuration, sync and model search", async()=>{
  const active=manifest(1),scenario=resetScenario({manifests:[active]}),ref=previewRef(active);
  const sourcePrepare=scenario.db.prepare.bind(scenario.db);
  scenario.db.prepare=(sql)=>sql.includes("WITH active AS")?{
    bind(){return {async all(){return {results:[{snapshot_id:ref.snapshot_id,claim_id:ref.claim_id}]};}};},
  }:sourcePrepare(sql);
  const response=await POST(new Request("http://localhost/api/evidence-search?response=quick_preview",{
    method:"POST",headers:{"content-type":"application/json"},
    body:JSON.stringify({query:"mercury",stage:"preview",corpus:"cross_paper"}),
  }));
  const body=await response.json();
  assert.equal(response.status,200,JSON.stringify(body));
  assert.equal(body.previewResults.length,1);
  assert.equal(body.queryPlanHandoff,null);
  assert.equal(body.previewResults[0].queryRelation,undefined);
  assert.equal(state.calls.configuration,null);
  assert.deepEqual(state.calls.checks,[]);
  assert.deepEqual(state.calls.searches,[]);
});

test("gateway hydrates the visible Top 12 before any AI relation result", async()=>{
  const active=manifest(1),scenario=resetScenario({manifests:[active]}),ref=previewRef(active);
  scenario.searchResponse=responseWithPreview([ref]);
  const response=await POST(request({query:"topoinf 能提高很多gnn方法的效果",stage:"preview",corpus:"cross_paper",limit:12}));
  const body=await response.json();
  assert.equal(response.status,200,JSON.stringify(body));
  assert.equal(body.stage,"preview");
  assert.equal(body.resultStatus,"matches");
  assert.equal(body.previewResults.length,1);
  assert.equal(body.previewResults[0].claim.canonicalClaim,"Mercury anomalies accompany volcanism.");
  assert.equal(body.previewResults[0].queryRelation,undefined);
  assert.equal(body.clusters.length,0);
  assert.equal(body.retrievalConfidence.mode,"observe");
  assert.equal(body.retrievalConfidence.decision,"return");
  assert.equal(body.retrievalConfidence.wouldAbstain,null);
  assert.equal(body.retrievalConfidence.conceptAnchorHits,1);
  assert.equal(body.retrievalConfidence.denseTop1,0.83);
  // The signed handoff stays on the server behind an opaque session ID.
  assert.match(body.queryPlanHandoff,/^rs1\.[A-Za-z0-9_-]{43}$/u);
  assert.deepEqual(state.sessions.get(body.queryPlanHandoff),{kind:"query_plan_handoff",token:QUERY_PLAN_HANDOFF});
  assert.equal(JSON.stringify(body).includes(QUERY_PLAN_HANDOFF),false);
  assert.equal(state.calls.searches[0].request.stage,"preview");
  assert.deepEqual(state.calls.searches[0].request.candidate_refs,[]);
});

test("ordinary final search forwards the preview plan handoff behind its session unchanged", async()=>{
  resetScenario({searchResponse:responseWithClusters([],SEARCH_VERSION)});
  const response=await POST(request({
    query:"mercury",stage:"ai_rerank",corpus:"cross_paper",
    query_plan_handoff:session("query_plan_handoff",QUERY_PLAN_HANDOFF),
  }));
  const body=await response.json();
  assert.equal(response.status,200,JSON.stringify(body));
  assert.equal(body.queryPlanHandoff,null);
  assert.equal(state.calls.searches[0].request.query_plan_handoff,QUERY_PLAN_HANDOFF);
});

test("browser continuation state is accepted only as a live server-side session", async()=>{
  const cases=[
    // A raw Compiler token (a page loaded before sessions) re-previews.
    [{query_plan_handoff:QUERY_PLAN_HANDOFF},409,"QUERY_PLAN_HANDOFF_REPREVIEW_REQUIRED"],
    // Unknown, expired or wrong-kind sessions re-preview.
    [{query_plan_handoff:"rs1."+"z".repeat(43)},409,"QUERY_PLAN_HANDOFF_REPREVIEW_REQUIRED"],
    [{query_plan_handoff:session("context",QUERY_PLAN_HANDOFF)},409,"QUERY_PLAN_HANDOFF_REPREVIEW_REQUIRED"],
    [{query_plan_handoff:"not-a-session"},400,"INVALID_EVIDENCE_SEARCH"],
  ];
  for(const [fields,status,code] of cases){
    resetScenario({searchResponse:responseWithClusters([],SEARCH_VERSION)});
    const response=await POST(request({query:"mercury",stage:"ai_rerank",corpus:"cross_paper",...fields}));
    const body=await response.json();
    assert.equal(response.status,status,JSON.stringify(body));
    assert.equal(body.error.code,code);
    assert.deepEqual(state.calls.searches,[]);
  }
  for(const [fields,code] of [
    [{context_submission:{context_token:"rs1."+"y".repeat(43),content:"Basin X"}},"CONTEXT_REPREVIEW_REQUIRED"],
    [{clarification_selection:{clarification_token:CONTEXT_CLARIFICATION_TOKEN,option_id:"A"}},"CLARIFICATION_REPREVIEW_REQUIRED"],
  ]){
    resetScenario();
    const response=await POST(request({query:"mercury",stage:"preview",corpus:"cross_paper",
      search_protocol:"evidence-retrieval/2.3",...fields}));
    const body=await response.json();
    assert.equal(response.status,409,JSON.stringify(body));
    assert.equal(body.error.code,code);
    assert.deepEqual(state.calls.searches,[]);
  }
});

test("a plain 2.3 final whose corpus vanished re-previews the handoff, not the context", async()=>{
  resetScenario({manifests:[]});
  const response=await POST(request({query:"mercury",stage:"ai_rerank",corpus:"cross_paper",
    search_protocol:"evidence-retrieval/2.3",query_plan_handoff:session("query_plan_handoff",QUERY_PLAN_HANDOFF)}));
  const body=await response.json();
  assert.equal(response.status,409,JSON.stringify(body));
  assert.equal(body.error.code,"QUERY_PLAN_HANDOFF_REPREVIEW_REQUIRED");
  assert.deepEqual(state.calls.searches,[]);
});

test("an unsupported quick preview is refused before any binding or session read", async()=>{
  resetScenario();
  const response=await POST(new Request("http://localhost/api/evidence-search?response=quick_preview",{
    method:"POST",headers:{"content-type":"application/json"},
    body:JSON.stringify({query:"mercury",stage:"preview",must_terms:["Hg"]}),
  }));
  assert.equal(response.status,400);
  assert.equal((await response.json()).error.code,"QUICK_PREVIEW_UNSUPPORTED");
  assert.equal(state.calls.bindingReads,0);
});

test("needs-context and clarification tokens reach the browser only as session IDs", async()=>{
  const contextToken=`n1.derived-auth-v1.e30.${"c".repeat(43)}`;
  const stage={attempted:false,status:"skipped",provider:"gpt",returned:0};
  const base={schema_version:SEARCH_VERSION,stage:"preview",query_plan:null,
    channels:{lexical:stage,dense:stage,rerank:stage,relation:stage},preview:[],clusters:[],warnings:[],retrieval_confidence:null};
  resetScenario({searchResponse:{...base,result_status:"needs_context",
    needs_context:{question:"Which basin?",context_token:contextToken}}});
  let response=await POST(request({query:"mercury",stage:"preview",corpus:"cross_paper",search_protocol:"evidence-retrieval/2.3"}));
  let body=await response.json();
  assert.equal(response.status,200,JSON.stringify(body));
  assert.deepEqual(state.sessions.get(body.needsContext.contextToken),{kind:"context",token:contextToken});
  assert.equal(JSON.stringify(body).includes(contextToken),false);
  const contextSession=body.needsContext.contextToken;
  resetScenario({searchResponse:responseWithPreview([])});
  response=await POST(request({query:"mercury",stage:"preview",corpus:"cross_paper",search_protocol:"evidence-retrieval/2.3",
    context_submission:{context_token:contextSession,content:"Basin X"}}));
  body=await response.json();
  assert.equal(response.status,200,JSON.stringify(body));
  assert.deepEqual(state.calls.searches[0].request.context_submission,{context_token:contextToken,content:"Basin X"});

  resetScenario({searchResponse:{...base,result_status:"clarification_required",
    clarification:{question:"Which mercury?",clarification_token:CONTEXT_CLARIFICATION_TOKEN,
      options:[{option_id:"A",label:"Element",resolved_query:"mercury element"},{option_id:"B",label:"Planet",resolved_query:"Mercury planet"}]}}});
  response=await POST(request({query:"mercury",stage:"preview",corpus:"cross_paper",search_protocol:"evidence-retrieval/2.3"}));
  body=await response.json();
  assert.equal(response.status,200,JSON.stringify(body));
  assert.deepEqual(state.sessions.get(body.clarification.clarificationToken),{kind:"clarification",token:CONTEXT_CLARIFICATION_TOKEN});
  assert.equal(JSON.stringify(body).includes(CONTEXT_CLARIFICATION_TOKEN),false);
});

test("gateway rejects a selected interpretation whose bound corpus is no longer available", async()=>{
  for(const overrides of [{manifests:[]},{resolveError:{status:404,code:"QUERY_CLAIM_NOT_FOUND",message:"private corpus detail"}}]){
    resetScenario(overrides);
    const response=await POST(request({query:"mercury",stage:"preview",corpus:"cross_paper",
      search_protocol:"evidence-retrieval/2.3",
      clarification_selection:{clarification_token:session("clarification",CONTEXT_CLARIFICATION_TOKEN),option_id:"A"}}));
    const body=await response.json();
    assert.equal(response.status,409,JSON.stringify(body));
    assert.equal(body.error.code,"CONTEXT_REPREVIEW_REQUIRED");
    assert.equal(JSON.stringify(body).includes("private corpus detail"),false);
    assert.deepEqual(state.calls.searches,[]);
    assert.deepEqual(state.calls.documentRefs,[]);
    assert.deepEqual(state.calls.assessmentRefs,[]);
  }
});

test("browser final pass conditionally forwards the preview handoff and keeps legacy previews compatible",()=>{
  const source=readFileSync(new URL("../app/components/evidence-search.tsx",import.meta.url),"utf8");
  assert.match(source,/preview\.queryPlanHandoff\s*\?\s*\{\s*query_plan_handoff:\s*preview\.queryPlanHandoff\s*\}\s*:\s*\{\}/);
});

// Expand concise browser fixtures into the HTTP DTO consumed by the real validator.
function browserResponse(value, stage){
  for(const [key, fallback] of Object.entries({ok:true,stage,resultStatus:"matches",query:{intent:"claim_search"},
    queryPlan:{queryType:"claim_search"},queryPlanHandoff:null,previewResults:[],clusters:[],
    channels:Object.fromEntries(["lexical","dense","rerank","relation"].map(key=>[key,{status:"ok"}]))})){
    if(!(key in value))value[key]=fallback;
  }
  for(const member of value.previewResults){
    member.source??={artifactName:"Fixture paper"};
    member.claim.canonicalClaim??="Grounded fixture claim";
    member.claim.scope??={entities:[],location:[],formation:[],sample_type:[],method:[],condition:[],measurement:[]};
    member.evidenceAnchors??=[];
    member.shortestEvidencePath??={nodes:[]};
  }
  if(value.clarification){
    value.clarification.question??="Which interpretation?";
    for(const option of value.clarification.options)option.label??=option.resolvedQuery;
  }
  return value;
}

function browserSubmitFixture(responses,overrides={}){
  const source=readFileSync(new URL("../app/components/evidence-search.tsx",import.meta.url),"utf8");
  const file=ts.createSourceFile("evidence-search.tsx",source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
  const handlers=[];
  function visit(node){
    if(ts.isVariableDeclaration(node)&&node.name.getText(file)==="submit")handlers.push(node.initializer);
    ts.forEachChild(node,visit);
  }
  visit(file);
  assert.equal(handlers.length,1);
  // Execute the component's handler, including both fetch bodies; do not copy
  // request construction into a test-only implementation.
  const {outputText}=ts.transpileModule(`return (${handlers[0].getText(file)});`,{
    compilerOptions:{target:ts.ScriptTarget.ES2022},
  });
  const calls=[],updates=[];
  const scope={
    cannotSubmit:false,requestGeneration:{current:0},requestController:{current:null},
    flushSync:(callback)=>callback(),previewSection:{current:null},
    startQuickPreview:()=>{},
    pendingClarification:{current:null},pendingContext:{current:null},trimmedQuery:"mercury",queryClaim:null,
    conversationHistory:[],historyEditor:{current:null},contextTextarea:{current:null},
    contextHistoryError,contextTextError,continueContextRequest,parseEvidenceSearchResponse,
    intent:"claim_search",hasPaperContext:false,corpus:"same_paper",
    initialOpenAlexScope:{level:"topic",id:"https://openalex.org/T123"},
    readJson:async(response)=>{const value=await response.json();return response.ok?browserResponse(value,response.stage):value;},responseError:()=>"unexpected response failure",
    fetch:async(url,options)=>{
      const response=responses[calls.length];
      calls.push({url,body:JSON.parse(options.body),signal:options.signal});
      return {ok:true,json:async()=>response};
    },
  };
  for(const name of ["setBusy","setAiBusy","setError","setAiError","setPreviewData","setResponseData",
    "setPreviewExpanded","setExpandedRelationGroups","setSelectedInterpretation",
    "setContextInput","setContextError","setSubmittedSupplements"]){
    scope[name]=(value)=>updates.push({name,value});
  }
  Object.assign(scope,overrides);
  const fetch=scope.fetch;
  scope.fetch=async(url,options)=>{const response=await fetch(url,options);response.stage=JSON.parse(options.body).stage;return response;};
  // Transport framing is covered by streamed-response.test; keep executing the
  // actual submit handler against the same overridable HTTP responses here.
  scope.fetchStreamed=(...args)=>scope.fetch(...args);
  const createSubmit=()=>new Function(...Object.keys(scope),outputText)(...Object.values(scope));
  return {calls,updates,scope,createSubmit,submit:createSubmit()};
}

function browserClarification(){
  return {resultStatus:"clarification_required",queryPlan:null,queryPlanHandoff:null,
    previewResults:[],clusters:[],clarification:{clarificationToken:"ct1.synthetic",options:[
      {optionId:"A",resolvedQuery:"mercury element"},{optionId:"B",resolvedQuery:"Mercury planet"},
    ]}};
}

test("browser submit preserves the same signed limit across its actual preview and final requests", async()=>{
  for(const handoff of [QUERY_PLAN_HANDOFF,null]){
    for(const seeded of [false,true]){
      const previewResults=seeded?[]:[{snapshotId:"snapshot:result",claim:{id:"claim:result"}}];
      const preview={queryPlanHandoff:handoff,previewResults,queryPlan:{queryType:"claim_search"}};
      const {submit,calls,updates}=browserSubmitFixture([preview,{clusters:[]}],{
        trimmedQuery:seeded?"":"mercury",queryClaim:seeded?{snapshotId:"snapshot:seed",claimId:"claim:seed"}:null,
        hasPaperContext:seeded,
      });
      let prevented=false;
      await submit({preventDefault:()=>{prevented=true;}});
      assert.equal(prevented,true);
      assert.deepEqual(updates.filter(({name,value})=>["setError","setAiError"].includes(name)&&value),[]);
      assert.equal(calls.length,2);
      assert.ok(calls.every((call)=>call.url==="/api/evidence-search"));
      const {stage:previewStage,...previewRequest}=calls[0].body;
      const {stage:finalStage,query_plan_handoff:finalHandoff,candidate_refs:candidates,...finalRequest}=calls[1].body;
      assert.equal(previewStage,"preview");
      assert.equal(finalStage,"ai_rerank");
      assert.equal(previewRequest.limit,50);
      assert.equal(previewRequest.search_protocol,"evidence-retrieval/2.3");
      assert.equal(finalRequest.limit,50);
      assert.deepEqual(finalRequest,previewRequest);
      assert.equal(finalHandoff,handoff??undefined);
      assert.deepEqual(candidates,seeded?undefined:[{snapshot_id:"snapshot:result",claim_id:"claim:result"}]);
    }
  }
});

test("browser publishes loading preview and signals final failure without replacing it with an answer",async()=>{
  for(const fail of [false,true]){
    const preview={queryPlan:{queryType:"claim_search"},previewResults:[{snapshotId:"snapshot:result",claim:{id:"claim:result"}}]};
    let finishFinal;
    const finalPending=new Promise((resolve,reject)=>{
      finishFinal=()=>fail?reject(new Error("analysis unavailable")):resolve({clusters:[]});
    });
    const {submit,updates}=browserSubmitFixture([preview,finalPending]);
    const running=submit({preventDefault(){}});
    await new Promise(resolve=>setImmediate(resolve));
    assert.ok(updates.some(({name,value})=>name==="setPreviewData"&&value===preview));
    assert.equal(updates.filter(({name})=>name==="setAiBusy").at(-1).value,true);
    assert.ok(!updates.some(({name,value})=>name==="setResponseData"&&value));
    finishFinal();
    await running;
    assert.equal(updates.filter(({name})=>name==="setPreviewData").at(-1).value,preview);
    assert.equal(updates.filter(({name})=>name==="setAiBusy").at(-1).value,false);
    if(fail)assert.equal(updates.filter(({name})=>name==="setAiError").at(-1).value,"analysis unavailable");
    else assert.deepEqual(updates.filter(({name})=>name==="setResponseData").at(-1).value.clusters,[]);
  }
});

test("browser search-empty condition excludes both pending states while preserving ordinary empty previews",()=>{
  const source=readFileSync(new URL("../app/components/evidence-search.tsx",import.meta.url),"utf8");
  const file=ts.createSourceFile("evidence-search.tsx",source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
  const elements=[],bindings=new Map();
  function visit(node){
    if(ts.isJsxElement(node)&&node.openingElement.attributes.properties.some((attribute)=>(
      ts.isJsxAttribute(attribute)&&attribute.name.getText(file)==="className"
      &&attribute.initializer&&ts.isStringLiteral(attribute.initializer)&&attribute.initializer.text==="search-empty"
    )))elements.push(node);
    if(ts.isVariableDeclaration(node)&&["clarification","needsContext","previewResults"].includes(node.name.getText(file))){
      const name=node.name.getText(file);
      assert.equal(bindings.has(name),false,`ambiguous component binding: ${name}`);
      bindings.set(name,node.initializer.getText(file));
    }
    ts.forEachChild(node,visit);
  }
  visit(file);
  assert.equal(elements.length,1);
  assert.equal(bindings.size,3);
  let branch=elements[0];
  while(ts.isParenthesizedExpression(branch.parent))branch=branch.parent;
  const conditional=branch.parent;
  assert.ok(ts.isConditionalExpression(conditional));
  assert.equal(conditional.whenTrue,branch);
  // Evaluate the real JSX condition and its actual component-derived state.
  const rendersEmpty=new Function("previewData","error","state",[
    "const quickData=null,responseData=null,searchFailed=Boolean(error);",
    "const {aiBusy=false,resultCount=0,aiError=''}=state;",
    ...[...bindings].map(([name,expression])=>`const ${name}=(${expression});`),
    `return Boolean(${conditional.condition.getText(file)});`,
  ].join("\n"));
  for(const {name,preview,error,expected,state={}} of [
    {name:"pending clarification",preview:browserClarification(),error:"",expected:false},
    {name:"pending context",preview:{resultStatus:"needs_context",needsContext:{question:"Which setting?",contextToken:"n1.synthetic"},previewResults:[]},error:"",expected:false},
    {name:"ordinary empty preview",preview:{resultStatus:"no_direct_answer",previewResults:[]},error:"",expected:true},
    {name:"preview with candidates",preview:{resultStatus:"matches",previewResults:[{}]},error:"",expected:false},
    {name:"preview error",preview:{resultStatus:"no_direct_answer",previewResults:[]},error:"failed",expected:false},
    {name:"before any preview",preview:null,error:"",expected:false},
    {name:"model still pending",preview:{previewResults:[]},error:"",state:{aiBusy:true},expected:false},
    {name:"final groups present",preview:{previewResults:[]},error:"",state:{resultCount:4},expected:false},
    {name:"model failed",preview:{previewResults:[]},error:"",state:{aiError:"failed"},expected:false},
  ])assert.equal(rendersEmpty(preview,error,state),expected,name);
});

test("browser clarification pauses before final and each choice preserves the original request", async()=>{
  for(const optionId of ["A","B"]){
    const pendingResponse=browserClarification();
    const selectedPreview={resultStatus:"matches",queryPlanHandoff:QUERY_PLAN_HANDOFF,
      queryPlan:{queryType:"claim_search"},previewResults:[{snapshotId:"snapshot:result",claim:{id:"claim:result"}}]};
    const fixture=browserSubmitFixture([pendingResponse,selectedPreview,{clusters:[]}]);
    await fixture.submit();
    assert.equal(fixture.calls.length,1);
    assert.equal(fixture.scope.pendingClarification.current.clarification,pendingResponse.clarification);
    assert.ok(fixture.updates.some(({name,value})=>name==="setPreviewData"&&value===pendingResponse));
    assert.equal(fixture.updates.some(({name,value})=>name==="setResponseData"&&value!==null),false);
    const {stage,...originalRequest}=fixture.calls[0].body;
    assert.equal(stage,"preview");
    // A rerender may change closure values; selection must use the sealed pending request.
    fixture.scope.trimmedQuery="unrelated edited query";
    fixture.scope.initialOpenAlexScope={level:"field",id:"https://openalex.org/F999"};
    await fixture.createSubmit()(undefined,optionId);
    assert.equal(fixture.calls.length,3);
    const {stage:selectedStage,...selectedRequest}=fixture.calls[1].body;
    const {stage:finalStage,query_plan_handoff:handoff,candidate_refs:refs,...finalRequest}=fixture.calls[2].body;
    assert.equal(selectedStage,"preview");
    assert.equal(finalStage,"ai_rerank");
    assert.deepEqual(selectedRequest,{...originalRequest,clarification_selection:{clarification_token:"ct1.synthetic",option_id:optionId}});
    assert.deepEqual(finalRequest,selectedRequest);
    assert.equal(handoff,QUERY_PLAN_HANDOFF);
    assert.deepEqual(refs,[{snapshot_id:"snapshot:result",claim_id:"claim:result"}]);
    assert.equal(fixture.scope.pendingClarification.current,null);
    assert.ok(fixture.updates.some(({name,value})=>name==="setSelectedInterpretation"
      &&value===pendingResponse.clarification.options.find((option)=>option.optionId===optionId).resolvedQuery));
    assert.deepEqual(fixture.updates.filter(({name,value})=>["setError","setAiError"].includes(name)&&value),[]);
  }
});

test("browser ignores unknown choices and choices whose pending clarification was cleared", async()=>{
  const fixture=browserSubmitFixture([browserClarification()]);
  await fixture.submit();
  const generation=fixture.scope.requestGeneration.current;
  const updates=fixture.updates.length;
  await fixture.submit(undefined,"unknown");
  fixture.scope.pendingClarification.current=null;
  await fixture.submit(undefined,"A");
  assert.equal(fixture.calls.length,1);
  assert.equal(fixture.scope.requestGeneration.current,generation);
  assert.equal(fixture.updates.length,updates);
});

test("browser carries real history through repeated context, choice, and exact final without mixing continuations", async()=>{
  const needs=(token)=>({resultStatus:"needs_context",queryPlan:null,queryPlanHandoff:null,
    previewResults:[],clusters:[],needsContext:{question:"Which study conditions?",contextToken:token}});
  const resolved={resultStatus:"matches",queryPlan:{queryType:"claim_search",resolvedQuery:"Mercury evidence under specified conditions"},
    queryPlanHandoff:QUERY_PLAN_HANDOFF,previewResults:[{snapshotId:"snapshot:result",claim:{id:"claim:result"}}]};
  const history=[{id:1,role:"user",content:"  Earlier question\n"},{id:2,role:"assistant",content:"Original response\n "}];
  const fixture=browserSubmitFixture([needs("n1.first"),needs("n1.second"),browserClarification(),needs("n1.after-choice"),resolved,{clusters:[]}],{
    conversationHistory:history,
  });
  await fixture.submit();
  assert.equal(fixture.calls.length,1);
  const {stage,...base}=fixture.calls[0].body;
  assert.equal(stage,"preview");
  assert.deepEqual(base.conversation_history,history.map(({role,content})=>({role,content})));
  await fixture.submit(undefined,undefined,"  First supplement\n");
  assert.equal(fixture.calls.length,2);
  assert.deepEqual(fixture.calls[1].body,{...base,stage:"preview",context_submission:{context_token:"n1.first",content:"  First supplement\n"}});
  await fixture.submit(undefined,undefined,"Second supplement");
  assert.equal(fixture.calls.length,3);
  assert.equal(fixture.scope.pendingContext.current,null);
  assert.equal(fixture.scope.pendingClarification.current.clarification.clarificationToken,"ct1.synthetic");
  await fixture.submit(undefined,"A");
  assert.equal(fixture.calls.length,4);
  assert.deepEqual(fixture.calls[3].body,{...base,stage:"preview",clarification_selection:{clarification_token:"ct1.synthetic",option_id:"A"}});
  await fixture.submit(undefined,undefined,"Third supplement");
  assert.equal(fixture.calls.length,6);
  const {stage:previewStage,...continued}=fixture.calls[4].body;
  const {stage:finalStage,query_plan_handoff,candidate_refs,...final}=fixture.calls[5].body;
  assert.equal(previewStage,"preview");
  assert.equal(finalStage,"ai_rerank");
  assert.deepEqual(continued,{...base,context_submission:{context_token:"n1.after-choice",content:"Third supplement"}});
  assert.deepEqual(final,continued);
  assert.equal(query_plan_handoff,QUERY_PLAN_HANDOFF);
  assert.deepEqual(candidate_refs,[{snapshot_id:"snapshot:result",claim_id:"claim:result"}]);
  let submitted=[];
  for(const {name,value} of fixture.updates)if(name==="setSubmittedSupplements")submitted=typeof value==="function"?value(submitted):value;
  assert.deepEqual(submitted,["  First supplement\n","Second supplement","Third supplement"]);
});

test("browser retains pending state and supplemental input when a continuation expires or exhausts its budget", async()=>{
  for(const code of ["CONTEXT_REPREVIEW_REQUIRED","RETRIEVAL_PLANNING_INPUT_TOO_LARGE"]){
    const pending={resultStatus:"needs_context",queryPlan:null,queryPlanHandoff:null,previewResults:[],clusters:[],
      needsContext:{question:"One more condition?",contextToken:"n1.synthetic"}};
    const fixture=browserSubmitFixture([pending]);
    await fixture.submit();
    const saved=fixture.scope.pendingContext.current;
    fixture.updates.length=0;
    fixture.scope.fetch=async()=>({ok:false,json:async()=>({error:{code}})});
    await fixture.createSubmit()(undefined,undefined,"Keep this new text");
    assert.equal(fixture.scope.pendingContext.current,saved);
    assert.equal(fixture.updates.some(({name})=>name==="setSubmittedSupplements"||name==="setContextInput"),false);
    assert.equal(fixture.updates.some(({name,value})=>name==="setPreviewData"&&value===null),false);
    assert.ok(fixture.updates.some(({name,value})=>name==="setError"&&value));
    assert.equal(fixture.scope.requestController.current,null);
  }
});

test("browser synchronously blocks duplicate requests before the disabled button rerenders", async()=>{
  let release;
  const held=new Promise((resolve)=>{release=resolve;});
  let calls=0;
  const fixture=browserSubmitFixture([],{
    fetch:async()=>{calls+=1;await held;return {ok:true,json:async()=>browserClarification()};},
  });
  const first=fixture.submit();
  await fixture.submit();
  assert.equal(calls,1);
  release();
  await first;
  assert.equal(fixture.scope.pendingClarification.current.clarification.clarificationToken,"ct1.synthetic");
});

test("browser ignores delayed preview and final responses after the request generation changes", async()=>{
  for(const delayedStage of ["preview","ai_rerank"]){
    let resolveResponse;
    const delayed=new Promise((resolve)=>{resolveResponse=resolve;});
    let markStarted;
    const started=new Promise((resolve)=>{markStarted=resolve;});
    const preview={queryPlan:{queryType:"claim_search"},queryPlanHandoff:QUERY_PLAN_HANDOFF,
      previewResults:[{snapshotId:"snapshot:result",claim:{id:"claim:result"}}]};
    const requests=[];
    const fixture=browserSubmitFixture([],{
      fetch:async(_url,options)=>{
        const body=JSON.parse(options.body);
        requests.push(body);
        if(body.stage===delayedStage){markStarted();return {ok:true,json:async()=>delayed};}
        return {ok:true,json:async()=>preview};
      },
    });
    const running=fixture.submit();
    await started;
    fixture.scope.requestGeneration.current+=1;
    const before=[...fixture.updates];
    resolveResponse(delayedStage==="preview"?browserClarification():{clusters:[]});
    await running;
    assert.deepEqual(fixture.updates,before);
    assert.equal(requests.length,delayedStage==="preview"?1:2);
    assert.equal(fixture.scope.pendingClarification.current,null);
  }
});

test("browser rejects degraded preview without final request or retained candidate answer",async()=>{
  const preview=browserResponse({resultStatus:"degraded",queryPlan:null},"preview");
  const fixture=browserSubmitFixture([preview]);
  await fixture.submit();
  assert.equal(fixture.calls.length,1);
  assert.equal(fixture.updates.some(({name,value})=>name==="setPreviewData"&&value),false);
  assert.ok(fixture.updates.filter(({name})=>name==="setError").at(-1).value);
  assert.equal(fixture.updates.some(({name,value})=>name==="setAiBusy"&&value),false);
  assert.equal(fixture.calls[0].signal.aborted,true);
});

test("browser malformed final signals error and aborts late quick preview",async()=>{
  const preview=browserResponse({previewResults:[{snapshotId:"snapshot:result",claim:{id:"claim:result"}}]},"preview");
  const fixture=browserSubmitFixture([preview,{}],{readJson:async(response)=>response.json()});
  await fixture.submit();
  assert.equal(fixture.calls.length,2);
  assert.equal(fixture.updates.filter(({name})=>name==="setPreviewData").at(-1).value,preview);
  assert.match(fixture.updates.filter(({name})=>name==="setAiError").at(-1).value,/格式异常/);
  assert.equal(fixture.calls[0].signal.aborted,true);
  assert.equal(fixture.updates.some(({name,value})=>name==="setResponseData"&&value),false);
});

test("empty corpus publishes no confidence observation and skips retrieval", async()=>{
  resetScenario({manifests:[]});
  const response=await POST(request({query:"mercury",stage:"preview",corpus:"cross_paper"})),body=await response.json();
  assert.equal(response.status,200);
  assert.equal(body.retrievalConfidence,null);
  assert.equal(body.resultStatus,"no_direct_answer");
  assert.equal(body.queryPlanHandoff,null);
  assert.deepEqual(body.previewResults,[]);
  assert.equal(state.calls.searches.length,0);
});

test("a signed final fails closed when its preview corpus has disappeared", async()=>{
  resetScenario({manifests:[]});
  const response=await POST(request({
    query:"mercury",stage:"ai_rerank",corpus:"cross_paper",
    query_plan_handoff:session("query_plan_handoff",QUERY_PLAN_HANDOFF),
  })),body=await response.json();
  assert.equal(response.status,409);
  assert.equal(body.error.code,"QUERY_PLAN_HANDOFF_REPREVIEW_REQUIRED");
  assert.equal(state.calls.searches.length,0);
});

test("a signed final maps bound query-claim corpus drift to one re-preview conflict", async()=>{
  const active=manifest(706);
  resetScenario({
    manifests:[active],
    resolveError:{
      status:404,
      code:"QUERY_CLAIM_NOT_FOUND",
      message:"query_claim is not present in the immutable retrieval projection",
    },
  });
  const response=await POST(request({
    query:"mercury",stage:"ai_rerank",corpus:"cross_paper",
    query_claim:{snapshot_id:active.snapshotId,claim_id:claimId("a")},
    candidate_refs:[{snapshot_id:active.snapshotId,claim_id:claimId("a")}],
    query_plan_handoff:session("query_plan_handoff",QUERY_PLAN_HANDOFF),
  }));
  const body=await response.json();
  assert.equal(response.status,409);
  assert.equal(body.error.code,"QUERY_PLAN_HANDOFF_REPREVIEW_REQUIRED");
  assert.equal(body.error.message,"The retrieval preview is no longer valid; run preview again");
  assert.equal(state.calls.searches.length,0);
});

test("gateway keeps query-relative relation separate from latest paper assessment", async()=>{
  const active=manifest(1),scenario=resetScenario({manifests:[active]}),ref=memberRef(active);
  scenario.searchResponse=responseWithClusters([
    {cluster_id:"cluster:fixture",relation:"supports",representative:ref,member_refs:[ref],distinct_paper_families:1},
    ...["related","unrelated"].map((relation)=>{
      const irrelevant={...ref,relation};
      return {cluster_id:`cluster:${relation}`,relation,representative:irrelevant,member_refs:[irrelevant],distinct_paper_families:1};
    }),
  ]);
  scenario.assessments.set(`${active.snapshotId}\0${ref.claim_id}`,{snapshotId:active.snapshotId,claimId:ref.claim_id,
    verdict:"contradicted",confidence:0.72,evidenceIds:["evidence:one"],limitations:["limited sample"]});
  const response=await POST(request({query:"mercury volcanism",intent:"supporting_evidence",corpus:"cross_paper",
    must_terms:["mercury"],exclude_terms:["laboratory blank"],claim_types:["correlational"],polarity:"affirmed",limit:10}));
  const body=await response.json();
  assert.equal(response.status,200);assert.equal(body.query.intent,"supporting_evidence");assert.equal(body.clusters.length,1);assert.equal(body.clusters[0].relation,"supports");
  assert.equal(body.clusters[0].representative.queryRelation.relation,"supports");
  assert.equal(body.clusters[0].representative.assessment.verdict,"contradicted");
  assert.equal(body.clusters[0].representative.source.artifactId,active.paperFamilyId);
  assert.equal(body.retrievalConfidence,null);
  assert.equal(state.calls.indexes.length,0);assert.deepEqual(state.calls.searches[0].request.filters.active_snapshot_ids,[active.snapshotId]);
  assert.deepEqual(state.calls.searches[0].request.must_terms,["mercury"]);assert.deepEqual(state.calls.searches[0].allowedSnapshotIds,[active.snapshotId]);
  assert.ok(state.calls.order.indexOf("search")<state.calls.order.indexOf("hydrate-assessments"));
  assert.ok(state.scenario.db.sql.every((sql)=>!sql.includes("graph_json")));
});

test("claim discovery keeps scientifically related results while excluding unrelated recalls", async()=>{
  const active=manifest(1),scenario=resetScenario({manifests:[active]}),ref=memberRef(active);
  scenario.searchResponse=responseWithClusters(["related","unrelated"].map((relation)=>{
    const candidate={...ref,relation};
    return {cluster_id:`cluster:${relation}`,relation,representative:candidate,member_refs:[candidate],distinct_paper_families:1};
  }));
  const response=await POST(request({query:"Ordos Basin",intent:"claim_search",corpus:"cross_paper",limit:10}));
  const body=await response.json();
  assert.equal(response.status,200);
  assert.deepEqual(body.clusters.map((cluster)=>cluster.relation),["related"]);
});

test("snapshots that are not searchable yet are queued for background indexing, never indexed by a search", async()=>{
  const manifests=Array.from({length:17},(_,index)=>manifest(index+10,0)),scenario=resetScenario({manifests});
  scenario.manifestState=(dto)=>({snapshot_id:dto.snapshot_id,status:"missing",indexed_document_count:0,
    document_count:dto.document_count,manifest_sha256:null,dense_complete:false});
  const response=await POST(request({query:"mercury",corpus:"cross_paper"})),body=await response.json();
  assert.equal(response.status,200);
  assert.equal(body.indexSync.checkedSnapshots,17);
  assert.equal(body.indexSync.pendingSnapshots,17);
  assert.equal(state.calls.bundleLoads.length,0,"a search never loads index bundles");
  assert.equal(state.calls.indexes.length,0,"a search never embeds or indexes documents");
  assert.deepEqual(state.calls.workRequests,[{kind:"retrieval_index",subjects:manifests.map((item)=>item.snapshotId)}]);
  assert.ok(body.warnings.some((warning)=>warning.includes("17 篇论文的检索索引仍在后台建立")));
  assert.equal(state.calls.searches.length,1);
});

test("one snapshot the service cannot index never fails a search", async()=>{
  const broken=manifest(30,2),healthy=manifest(31),scenario=resetScenario({manifests:[broken,healthy]});
  scenario.manifestState=(dto)=>dto.snapshot_id===broken.snapshotId
    ?{snapshot_id:dto.snapshot_id,status:"stale",indexed_document_count:0,document_count:dto.document_count,manifest_sha256:null,dense_complete:false}
    :{snapshot_id:dto.snapshot_id,status:"indexed",indexed_document_count:dto.document_count,document_count:dto.document_count,manifest_sha256:"d".repeat(64),dense_complete:true};
  const response=await POST(request({query:"mercury",corpus:"cross_paper"})),body=await response.json();
  assert.equal(response.status,200);
  assert.equal(body.indexSync.pendingSnapshots,1);
  assert.deepEqual(state.calls.workRequests,[{kind:"retrieval_index",subjects:[broken.snapshotId]}]);
  assert.equal(state.calls.indexes.length,0);
});

test("complete degraded snapshots are searched without repeated embedding", async()=>{
  const active=manifest(2,0),scenario=resetScenario({manifests:[active]});
  scenario.manifestState=(dto)=>({snapshot_id:dto.snapshot_id,status:"degraded",indexed_document_count:dto.document_count,
    document_count:dto.document_count,manifest_sha256:"d".repeat(64),dense_complete:false});
  const response=await POST(request({query:"mercury",corpus:"cross_paper"})),body=await response.json();
  assert.equal(response.status,200);assert.equal(state.calls.indexes.length,0);assert.equal(state.calls.bundleLoads.length,0);
  assert.ok(body.warnings.some((warning)=>warning.includes("语义向量通道当前降级")));
  assert.deepEqual(state.calls.workRequests,[],"a complete lexical index is not re-queued");
});

test("fifty long representative records stay within the public response budget", async()=>{
  const manifests=Array.from({length:50},(_,index)=>manifest(index+100));
  const scenario=resetScenario({manifests}),clusters=[];
  for(let index=0;index<manifests.length;index+=1){
    const item=manifests[index],ref=memberRef(item),document=scenario.documents.get(`${item.snapshotId}\0${ref.claim_id}`);
    document.originalClaim="原".repeat(128_000);
    document.evidenceAnchors[0].quote="证".repeat(128_000);
    document.shortestEvidencePath.nodes=Array.from({length:64},(_,nodeIndex)=>({
      id:`path:${index}:${nodeIndex}`,kind:nodeIndex===63?"atomic_claim":"source_anchor",label:"路径标签".repeat(500),
    }));
    document.shortestEvidencePath.links=Array.from({length:63},(_,linkIndex)=>({
      fromId:`path:${index}:${linkIndex}`,toId:`path:${index}:${linkIndex+1}`,relation:"grounds",linkType:"edge",confidence:0.9,
    }));
    clusters.push({cluster_id:`cluster:${index}`,relation:"supports",representative:ref,member_refs:[ref],distinct_paper_families:1});
  }
  scenario.searchResponse=responseWithClusters(clusters);

  const response=await POST(request({query:"mercury",corpus:"cross_paper",limit:50})),body=await response.json();
  assert.equal(response.status,200);
  assert.ok(new TextEncoder().encode(JSON.stringify(body)).byteLength<=7*1024*1024);
  assert.equal(body.clusters.length,50);
  for(const cluster of body.clusters){
    const representative=cluster.representative;
    assert.equal(representative.claim.originalClaimTruncated,true);
    assert.ok(Array.from(representative.claim.originalClaim).length<=16_001);
    assert.equal(representative.evidenceAnchorsTruncated,true);
    assert.equal(representative.shortestEvidencePath.truncated,true);
    assert.equal(representative.shortestEvidencePath.nodes.length,24);
    assert.ok(Array.from(representative.shortestEvidencePath.nodes[0].label).length<=801);
  }
});

test("gateway rejects unknown public request fields before service work", async()=>{
  resetScenario();const response=await POST(request({query:"mercury",unexpected:true})),body=await response.json();
  assert.equal(response.status,400);assert.equal(body.error.code,"INVALID_EVIDENCE_SEARCH");
  assert.equal(state.calls.checks.length,0);assert.equal(state.calls.searches.length,0);
});

test("gateway body budget admits the full public must/exclude term contract", async()=>{
  resetScenario();
  const mustTerms=Array.from({length:64},(_,index)=>`must-${index}-${"a".repeat(985)}`);
  const excludeTerms=Array.from({length:64},(_,index)=>`exclude-${index}-${"b".repeat(982)}`);
  const payload={query:"mercury",must_terms:mustTerms,exclude_terms:excludeTerms,corpus:"cross_paper"};
  assert.ok(new TextEncoder().encode(JSON.stringify(payload)).byteLength>64*1024);
  const response=await POST(request(payload));
  assert.equal(response.status,200);
  assert.equal(state.calls.searches[0].request.must_terms.length,64);
  assert.equal(state.calls.searches[0].request.exclude_terms.length,64);
});

test("ordinary runtime failures return errors without local recall or candidate answers", async()=>{
  for (const stage of ["preview","ai_rerank"]) for(const code of ["RETRIEVAL_UNAVAILABLE","RETRIEVAL_PLANNING_OUTPUT_INVALID"]){
    const scenario=resetScenario({searchError:{apiError:true,status:503,code,message:"model unavailable"}});
    const prepare=scenario.db.prepare.bind(scenario.db);let recalls=0;
    scenario.db.prepare=sql=>{if(sql.includes("WITH active AS"))recalls++;return prepare(sql);};
    const result=await POST(request({query:"mercury",stage,corpus:"cross_paper"}));
    const body=await result.json();assert.equal(result.status,503,JSON.stringify(body));
    assert.equal(body.error.code,code);assert.equal(recalls,0);assert.equal(body.previewResults,undefined);
    assert.equal(state.calls.searches.length,1);assert.deepEqual(state.calls.documentRefs,[]);
  }
});

test("ordinary relation degradation is an error before candidate hydration",async()=>{
  const response=responseWithClusters();response.result_status="degraded";
  response.channels.relation={attempted:true,status:"degraded",provider:"gpt",returned:0};
  resetScenario({searchResponse:response});
  const result=await POST(request({query:"mercury",stage:"ai_rerank"}));
  const body=await result.json();assert.equal(result.status,503);
  assert.equal(body.error.code,"RETRIEVAL_RELATION_UNAVAILABLE");assert.equal(body.previewResults,undefined);
  assert.deepEqual(state.calls.documentRefs,[]);
});

test("ordinary gateway never masks integrity, access, bad request or cancellation errors",async()=>{
  for(const [status,code] of [[400,"INVALID_EVIDENCE_SEARCH"],[401,"UPSTREAM_RETRIEVAL_UNAUTHORIZED"],[403,"UPSTREAM_RETRIEVAL_FORBIDDEN"],[409,"QUERY_PLAN_HANDOFF_REPREVIEW_REQUIRED"],[408,"RETRIEVAL_REQUEST_CANCELLED"],[502,"INVALID_RETRIEVAL_RESPONSE"]]){
    resetScenario({searchError:{apiError:true,status,code,message:"rejected"}});
    const result=await POST(request({query:"mercury",stage:"preview"}));assert.equal(result.status,status);assert.deepEqual(state.calls.documentRefs,[]);
  }
});

test("cancellation during loading-only quick preview remains cancellation",async()=>{
  const controller=new AbortController(),scenario=resetScenario({searchError:{apiError:true,status:503,code:"RETRIEVAL_UNAVAILABLE",message:"offline"}});
  const prepare=scenario.db.prepare.bind(scenario.db);
  scenario.db.prepare=(sql)=>sql.includes("WITH active AS")?{bind(){return {async all(){controller.abort();return {results:[]};}};}}:prepare(sql);
  const result=await POST(new Request("http://localhost/api/evidence-search?response=quick_preview",{method:"POST",signal:controller.signal,
    headers:{"content-type":"application/json"},body:JSON.stringify({query:"mercury",stage:"preview"})}));
  assert.equal(result.status,408);assert.equal((await result.json()).error.code,"RETRIEVAL_REQUEST_CANCELLED");
});


test("realtime coverage check waits without an active timeout and respects user cancellation",async()=>{
  const originalTimeout=AbortSignal.timeout;
  try {
    for (const cancelUser of [false,true]) {
      const user=new AbortController(),budgets=[];
      AbortSignal.timeout=ms=>{budgets.push(ms);return new AbortController().signal;};
      let entered,release;
      const stalled=new Promise(resolve=>{entered=resolve;});
      resetScenario({checkHook:async signal=>new Promise((resolve,reject)=>{
        release=resolve;
        signal.addEventListener("abort",()=>reject(new DOMException("aborted","AbortError")),{once:true});
        entered();
      })});
      let settled=false;
      const running=POST(new Request("http://localhost/api/evidence-search",{method:"POST",signal:user.signal,
        headers:{"content-type":"application/json"},body:JSON.stringify({query:"mercury",stage:"preview"})})).then(value=>{settled=true;return value;});
      await stalled;
      await new Promise(resolve=>setImmediate(resolve));
      assert.equal(settled,false);assert.deepEqual(budgets,[]);
      assert.equal(state.calls.checks[0].signal.aborted,false);
      if(cancelUser)user.abort();else release();
      const result=await running,body=await result.json();
      assert.equal(result.status,cancelUser?408:200,JSON.stringify(body));
      if(cancelUser)assert.equal(body.error.code,"RETRIEVAL_REQUEST_CANCELLED");
      assert.equal(state.calls.searches.length,cancelUser?0:1);
      assert.equal(state.calls.indexes.length,0);
    }
  } finally {AbortSignal.timeout=originalTimeout;}
});

test("browser hides all candidate and model result sections after either terminal error",()=>{
  const source=readFileSync(new URL("../app/components/evidence-search.tsx",import.meta.url),"utf8");
  const file=ts.createSourceFile("evidence-search.tsx",source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
  let layout,failed;
  function visit(node){
    if(ts.isVariableDeclaration(node)&&node.name.getText(file)==="searchFailed")failed=node.initializer.getText(file);
    if(ts.isJsxElement(node)&&node.openingElement.attributes.properties.some(attribute=>ts.isJsxAttribute(attribute)
      &&attribute.name.getText(file)==="className"&&attribute.initializer&&ts.isStringLiteral(attribute.initializer)
      &&attribute.initializer.text==="search-stage-layout"))layout=node;
    ts.forEachChild(node,visit);
  }
  visit(file);assert.ok(layout);assert.ok(failed);
  let branch=layout;while(ts.isParenthesizedExpression(branch.parent))branch=branch.parent;
  const conditional=branch.parent;assert.ok(ts.isConditionalExpression(conditional));
  const visible=new Function("error","aiError",`const responseData={},previewData={},quickData={},clarification=null,needsContext=null;const searchFailed=${failed};return Boolean(${conditional.condition.getText(file)});`);
  assert.equal(visible("",""),true);
  for(const errors of [["failed",""],["","failed"],["failed","failed"]])assert.equal(visible(...errors),false);
});
