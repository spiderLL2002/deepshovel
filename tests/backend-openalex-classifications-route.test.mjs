import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

const PROJECT_ID = "DEEPSHOVEL-PROJECT";
const state = { scenario: null, calls: null };
globalThis.__openAlexClassificationsRouteTest = state;

const storeStub = `
export const PROJECT_ID=${JSON.stringify(PROJECT_ID)};
export class ApiError extends Error{constructor(status,code,message){super(message);this.status=status;this.code=code;}}
export function assertSameOrigin(request){const origin=request.headers.get("origin");if(origin&&origin!==new URL(request.url).origin)throw new ApiError(403,"CROSS_ORIGIN_WRITE_REJECTED","Cross-origin write rejected");}
export async function getBindings(){return {DB:globalThis.__openAlexClassificationsRouteTest.scenario.db};}
export async function getCompilerConfiguration(){const fixture=globalThis.__openAlexClassificationsRouteTest;fixture.calls.configurations+=1;return structuredClone(fixture.scenario.configuration);}
export async function readJsonObject(request,maximum){const text=await request.text();if(new TextEncoder().encode(text).byteLength>maximum)throw new ApiError(413,"REQUEST_TOO_LARGE","request too large");let parsed;try{parsed=JSON.parse(text);}catch{throw new ApiError(400,"INVALID_JSON","invalid JSON");}if(!parsed||typeof parsed!=="object"||Array.isArray(parsed))throw new ApiError(400,"INVALID_JSON","body must be an object");return parsed;}
export function safeError(error){return Response.json({ok:false,error:{code:error?.code??"INTERNAL_ERROR",message:error instanceof Error?error.message:String(error)}},{status:error?.status??500});}
`;
const retrievalStub = `
export async function loadActiveEvidenceRetrievalManifests(_db,projectId){const fixture=globalThis.__openAlexClassificationsRouteTest;fixture.calls.activeProjects.push(projectId);return structuredClone(fixture.scenario.active);}
`;
const clientStub = `
export class OpenAlexClassifierClient{
 constructor(configuration){globalThis.__openAlexClassificationsRouteTest.calls.clientConfigurations.push(structuredClone(configuration));}
 async classify(request,signal){const fixture=globalThis.__openAlexClassificationsRouteTest;fixture.calls.classifications.push({request:structuredClone(request),signal});fixture.calls.inFlight+=1;fixture.calls.peakConcurrency=Math.max(fixture.calls.peakConcurrency,fixture.calls.inFlight);await Promise.resolve();fixture.calls.inFlight-=1;if(fixture.scenario.classifierError)throw fixture.scenario.classifierError;if(fixture.scenario.nullClassification)return null;return {title:request.title,source:"openalex_work"};}
}
`;
const contractStub = `
export function parseOpenAlexClassification(value,path){globalThis.__openAlexClassificationsRouteTest.calls.parsedClassifications.push({value:structuredClone(value),path});return value;}
`;
const persistStub = `
export async function persistOpenAlexClassification(_db,snapshotId,classification){const fixture=globalThis.__openAlexClassificationsRouteTest;if(fixture.scenario.persistError)throw fixture.scenario.persistError;fixture.calls.persisted.push({snapshotId,classification:structuredClone(classification)});fixture.scenario.persisted.add(snapshotId);}
`;
const loader = `
let rootUrl,storeUrl,retrievalUrl,clientUrl,contractUrl,persistUrl;
export function initialize(data){({rootUrl,storeUrl,retrievalUrl,clientUrl,contractUrl,persistUrl}=data);}
export async function resolve(specifier,context,nextResolve){
 if(specifier==="@/lib/server/evidence-store")return {url:storeUrl,shortCircuit:true};
 if(specifier==="@/lib/evidence-retrieval")return {url:retrievalUrl,shortCircuit:true};
 if(specifier==="@/lib/server/openalex-classifier-client")return {url:clientUrl,shortCircuit:true};
 if(specifier.endsWith("/evidence-graphs/import-contract.ts"))return {url:contractUrl,shortCircuit:true};
 if(specifier.endsWith("/evidence-graphs/import-store.ts"))return {url:persistUrl,shortCircuit:true};
 if(specifier.startsWith("@/")){let path=specifier.slice(2);if(!/\\.[cm]?[jt]sx?$/.test(path))path+=".ts";return {url:new URL(path,rootUrl).href,shortCircuit:true};}
 return nextResolve(specifier,context);
}`;
const asDataUrl = (source) => `data:text/javascript,${encodeURIComponent(source)}`;
register(asDataUrl(loader), {
  parentURL: import.meta.url,
  data: {
    rootUrl: new URL("../", import.meta.url).href,
    storeUrl: asDataUrl(storeStub),
    retrievalUrl: asDataUrl(retrievalStub),
    clientUrl: asDataUrl(clientStub),
    contractUrl: asDataUrl(contractStub),
    persistUrl: asDataUrl(persistStub),
  },
});

const { POST } = await import("../app/api/evidence-knowledge-graph/classifications/route.ts");
const { ApiError } = await import("@/lib/server/evidence-store");

class ScenarioDb {
  prepare(sql) {
    state.calls.sql.push(sql);
    return {
      parameters: [],
      bind(...parameters) { this.parameters = parameters; return this; },
      async all() {
        assert.equal(this.parameters[0], PROJECT_ID);
        const active = new Set(state.scenario.active.map((item) => item.snapshotId));
        const limit = this.parameters[1];
        const results = state.scenario.rows
          .filter((row) => active.has(row.snapshotId) && !state.scenario.persisted.has(row.snapshotId))
          .slice(0, limit);
        return { results: structuredClone(results) };
      },
      async first() {
        assert.equal(this.parameters[0], PROJECT_ID);
        const active = state.scenario.active.map((item) => item.snapshotId);
        return { total: active.filter((snapshotId) => !state.scenario.persisted.has(snapshotId)).length };
      },
    };
  }
}

function snapshot(index) {
  return `snapshot:sha256:${index.toString(16).padStart(64, "0")}`;
}

function resetScenario(count = 3, overrides = {}) {
  state.calls = {
    configurations: 0, clientConfigurations: [], activeProjects: [], classifications: [],
    parsedClassifications: [], persisted: [], sql: [], inFlight: 0, peakConcurrency: 0,
  };
  const active = Array.from({ length: count }, (_, index) => ({ snapshotId: snapshot(index + 1) }));
  state.scenario = {
    db: new ScenarioDb(), active,
    rows: active.map((item, index) => ({ snapshotId: item.snapshotId, title: `REE paper ${index + 1}` })),
    persisted: new Set(), configuration: { baseUrl: "http://compiler.test", sharedSecret: "s".repeat(40) },
    ...overrides,
  };
  return state.scenario;
}

function request(body, options = {}) {
  return new Request("https://evidence.example.test/api/evidence-knowledge-graph/classifications", {
    method: "POST", headers: { "content-type": "application/json", ...options.headers },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}

test("no active corpus returns a truthful zero count without contacting OpenAlex", async () => {
  resetScenario(0);
  const response = await POST(request({}));
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { ok: true, classified: 0, remaining: 0 });
  assert.equal(state.calls.configurations, 0);
  assert.equal(state.calls.classifications.length, 0);
  assert.deepEqual(state.calls.activeProjects, []);
  assert.equal(state.calls.sql.length, 1);
  assert.ok(state.calls.sql[0].includes("graph_read_counts"));
});

test("already classified snapshots are not classified or charged a second time", async () => {
  const scenario = resetScenario(3);
  for (const item of scenario.active) scenario.persisted.add(item.snapshotId);
  const response = await POST(request({}));
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { ok: true, classified: 0, remaining: 0 });
  assert.equal(state.calls.classifications.length, 0);
});

test("default backfill classifies at most sixteen, uses exact titles, and reports remaining work", async () => {
  resetScenario(19);
  const response = await POST(request({}));
  const body = await response.json();
  assert.equal(response.status, 200);
  assert.deepEqual(body, { ok: true, classified: 16, remaining: 3 });
  assert.equal(state.calls.classifications.length, 16);
  assert.equal(state.calls.persisted.length, 16);
  assert.deepEqual(state.calls.classifications[0].request, { title: "REE paper 1" });
  assert.ok(state.calls.classifications.every((call) => call.signal instanceof AbortSignal));
  assert.ok(state.calls.sql.some((sql) => sql.includes("FROM graph_unclassified_snapshots")));
  assert.ok(state.calls.sql.some((sql) => sql.includes("ORDER BY document.claim_id LIMIT 1")));
  assert.ok(state.calls.sql.every((sql) => !/manifest_json|ROW_NUMBER/u.test(sql)));
  assert.ok(state.calls.sql.every((sql) => !/DELETE|UPDATE/u.test(sql)));
});

test("an explicit batch limit is respected and successful reruns process only the remainder", async () => {
  resetScenario(5);
  const first = await POST(request({ limit: 2 }));
  assert.deepEqual(await first.json(), { ok: true, classified: 2, remaining: 3 });
  const second = await POST(request({ limit: 2 }));
  assert.deepEqual(await second.json(), { ok: true, classified: 2, remaining: 1 });
  const third = await POST(request({ limit: 2 }));
  assert.deepEqual(await third.json(), { ok: true, classified: 1, remaining: 0 });
  assert.equal(new Set(state.calls.persisted.map((item) => item.snapshotId)).size, 5);
});

test("classifications use exactly the documented maximum of eight concurrent provider calls", async () => {
  resetScenario(16);
  const response = await POST(request({ limit: 16 }));
  assert.equal(response.status, 200);
  assert.equal(state.calls.peakConcurrency, 8);
  assert.ok(state.calls.clientConfigurations[0].sharedSecret);
});

test("missing Compiler credential is explicit and never starts provider requests", async () => {
  resetScenario(2, { configuration: { baseUrl: "http://compiler.test", sharedSecret: null } });
  const response = await POST(request({}));
  assert.equal(response.status, 503);
  assert.equal((await response.json()).error.code, "COMPILER_NOT_CONFIGURED");
  assert.equal(state.calls.classifications.length, 0);
  assert.equal(state.calls.persisted.length, 0);
});

test("provider failure preserves its classified status and does not fabricate classifications", async () => {
  resetScenario(1, { classifierError: new ApiError(503, "OPENALEX_CLASSIFIER_UNAVAILABLE", "OpenAlex temporarily unavailable") });
  const response = await POST(request({}));
  assert.equal(response.status, 503);
  assert.equal((await response.json()).error.code, "OPENALEX_CLASSIFIER_UNAVAILABLE");
  assert.equal(state.calls.persisted.length, 0);
});

test("null provider classifications fail closed and are never persisted", async () => {
  resetScenario(1, { nullClassification: true });
  const response = await POST(request({}));
  assert.equal(response.status, 502);
  assert.equal((await response.json()).error.code, "OPENALEX_CLASSIFICATION_MISSING");
  assert.equal(state.calls.persisted.length, 0);
});

test("backfill rejects cross-origin writes before corpus access", async () => {
  resetScenario(2);
  const response = await POST(request({}, { headers: { origin: "https://untrusted.example.test" } }));
  assert.equal(response.status, 403);
  assert.equal((await response.json()).error.code, "CROSS_ORIGIN_WRITE_REJECTED");
  assert.equal(state.calls.activeProjects.length, 0);
});

test("backfill rejects oversized bodies, non-integers, out-of-range limits, and unknown fields", async () => {
  for (const input of [
    { body: { limit: 0 }, status: 400, code: "INVALID_OPENALEX_BACKFILL_REQUEST" },
    { body: { limit: 17 }, status: 400, code: "INVALID_OPENALEX_BACKFILL_REQUEST" },
    { body: { limit: 1.5 }, status: 400, code: "INVALID_OPENALEX_BACKFILL_REQUEST" },
    { body: { limit: "2" }, status: 400, code: "INVALID_OPENALEX_BACKFILL_REQUEST" },
    { body: { limit: 2, bypass: true }, status: 400, code: "INVALID_OPENALEX_BACKFILL_REQUEST" },
    { body: { padding: "x".repeat(300) }, status: 413, code: "REQUEST_TOO_LARGE" },
    { body: "{", status: 400, code: "INVALID_JSON" },
  ]) {
    resetScenario(3);
    const response = await POST(request(input.body));
    assert.equal(response.status, input.status);
    assert.equal((await response.json()).error.code, input.code);
    assert.equal(state.calls.activeProjects.length, 0);
  }
});
