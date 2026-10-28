import assert from "node:assert/strict";
import { register } from "node:module";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { parseEvidenceSearchRequest, toEvidenceRetrievalSearchRequestDto } from "../lib/evidence-retrieval/index.ts";
import { RetrievalClient, parseRetrievalSearchResponse } from "../lib/server/retrieval-client.ts";

const version = "evidence-retrieval/2.3";
const snapshot = `snapshot:sha256:${"a".repeat(64)}`;
const token = (prefix) => `${prefix}.derived-auth-v1.e30.${"s".repeat(43)}`;
const history = [{ role: "user", content: "  第一条\n😀  " }, { role: "assistant", content: " This is supplied context.\n" }];
const input = (extra = {}) => ({ search_protocol: version, stage: "preview", query: "Does this affect it?", conversation_history: history, ...extra });
function dto(value = input()) {
  const request = parseEvidenceSearchRequest(value);
  return toEvidenceRetrievalSearchRequestDto("PROJECT", { request, queryText: request.query, queryDocument: null, manifests: [{ snapshotId: snapshot }] });
}
function pending() {
  return { schema_version: version, stage: "preview", result_status: "needs_context", query_plan: null,
    needs_context: { question: "Which system and intervention?", context_token: token("n1") },
    channels: Object.fromEntries(["lexical", "dense", "rerank", "relation"].map((name) => [name,
      { attempted: false, status: "skipped", provider: name, returned: 0 }])),
    preview: [], clusters: [], warnings: [], retrieval_confidence: null };
}
function resolved(stage = "preview") {
  return { ...pending(), needs_context: undefined, result_status: "no_direct_answer", stage,
    query_plan: { query_type: "claim_search", original_query: input().query, resolved_query: "Does intervention A affect system B?",
      canonical_claim: "Intervention A affects system B.", must_terms: [], optional_terms: [], exclude_terms: [], entities: [],
      polarity: null, claim_types: [], scope_constraints: null, planner: "gpt" },
    ...(stage === "preview" ? { query_plan_handoff: token("v1") } : {}) };
}
function resolvedWire(stage) { const response = resolved(stage); delete response.needs_context; return response; }

test("2.3 preserves role, order, whitespace and astral Unicode through preview and final DTOs", () => {
  const submission = { context_token: token("n1"), content: "  System B\nIntervention A 😀  " };
  for (const continuation of [{ context_submission: submission }, { clarification_selection: { clarification_token: token("c2"), option_id: "one" } }]) {
    const preview = dto(input(continuation));
    assert.equal(preview.schema_version, version);
    assert.deepEqual(preview.conversation_history, history);
    for (const [key, value] of Object.entries(continuation)) assert.deepEqual(preview[key], value);
    const final = dto(input({ ...continuation, stage: "ai_rerank", query_plan_handoff: token("v1") }));
    assert.deepEqual(final.conversation_history, preview.conversation_history);
    for (const key of Object.keys(continuation)) assert.deepEqual(final[key], preview[key]);
  }
  assert.equal(dto(input({ query: "  Does this affect it?  " })).query, input().query);
});

test("history and supplements reject malformed Unicode, normalization, roles, counts and UTF-8 overflow", () => {
  const invalidTexts = ["", " \n ", "\u0085", "\u001c", "e\u0301", "\ud800", "\udc00", "x".repeat(8001), "😀".repeat(8001)];
  for (const content of invalidTexts) {
    assert.throws(() => dto(input({ conversation_history: [{ role: "user", content }] })));
    assert.throws(() => dto(input({ context_submission: { context_token: token("n1"), content } })));
  }
  assert.equal(dto(input({ conversation_history: [{ role: "user", content: "😀".repeat(8000) }] })).conversation_history[0].content.length, 16000);
  assert.equal(dto(input({ conversation_history: [{ role: "user", content: "\ufeff" }] })).conversation_history[0].content, "\ufeff");
  const full = Array.from({ length: 16 }, () => ({ role: "user", content: "x".repeat(2048) }));
  assert.equal(dto(input({ conversation_history: full })).conversation_history.length, 16);
  const overflow = structuredClone(full); overflow[15].content += "x";
  for (const conversation_history of [overflow, [...full, full[0]], null, {}, [{ role: "system", content: "x" }],
    [{ role: "user", content: "x", extra: true }], [{ content: "x" }]]) {
    assert.throws(() => dto(input({ conversation_history })));
  }
});

test("2.3 preserves taxonomy filter identity even when resolved snapshots are identical", async () => {
  const first = dto(input({ openalex_scope: { level: "domain", id: "https://openalex.org/domains/1" } }));
  const second = dto(input({ openalex_scope: { level: "field", id: "https://openalex.org/fields/2" } }));
  assert.deepEqual(first.filters.active_snapshot_ids, second.filters.active_snapshot_ids);
  assert.notDeepEqual(first.filters, second.filters);
  assert.deepEqual(first.filters.openalex_scope, { level: "domain", id: "https://openalex.org/domains/1" });
  assert.equal(Object.hasOwn(dto().filters, "openalex_scope"), false);
  // Internal callers without a protocol keep the legacy wire, which never carries taxonomy metadata.
  const legacy = dto({ query: "x", stage: "preview", openalex_scope: first.filters.openalex_scope });
  assert.equal(Object.hasOwn(legacy.filters, "openalex_scope"), false);
  assert.throws(() => dto(input({ openalex_scope: { level: "domain", id: "https://openalex.org/fields/2" } })));
  // The removed clarification-only protocol 2.2 is rejected outright.
  assert.throws(() => dto({ query: "x", stage: "preview", search_protocol: "evidence-retrieval/2.2" }));
  let calls = 0;
  const client = new RetrievalClient({ baseUrl: "https://compiler.example", sharedSecret: "retrieval-test-secret-0123456789012345", fetch: async (_url, init) => {
    calls++; assert.deepEqual(JSON.parse(init.body), first); return Response.json(pending());
  } });
  await client.search(first, [snapshot]);
  await assert.rejects(client.search({ ...first, schema_version: "evidence-retrieval/2.2" }, [snapshot]), /unsupported version/u);
  await assert.rejects(client.search({ ...first, filters: { ...first.filters, openalex_scope: null } }, [snapshot]), /context is invalid/u);
  assert.equal(calls, 1);
});

test("2.3 continuations remain mutually exclusive and protocol/token purposes are closed", () => {
  for (const extra of [
    { context_submission: null }, { context_submission: { context_token: token("n1"), content: "x", extra: true } },
    { context_submission: { context_token: token("c2"), content: "x" } },
    { clarification_selection: { clarification_token: token("n1"), option_id: "one" } },
    { clarification_selection: { clarification_token: token("c1"), option_id: "one" } },
    { context_submission: { context_token: token("n1"), content: "x" }, clarification_selection: { clarification_token: token("c2"), option_id: "one" } },
    { context_submission: { context_token: `${token("n1")}.extra`, content: "x" } },
    { context_submission: { context_token: token("n1").replace("derived-auth-v1", "other"), content: "x" } },
    { stage: "ai_rerank" },
  ]) assert.throws(() => dto(input(extra)));
  for (const search_protocol of [undefined, "evidence-retrieval/2.2"]) {
    for (const extra of [{ conversation_history: history }, { context_submission: { context_token: token("n1"), content: "x" } }]) {
      const request = { stage: "preview", query: "x", ...extra };
      if (search_protocol) request.search_protocol = search_protocol;
      assert.throws(() => parseEvidenceSearchRequest(request));
    }
  }
});

test("needs_context is strictly pre-retrieval and resolved 2.3 has no inactive payload", () => {
  assert.deepEqual(parseRetrievalSearchResponse(pending(), [snapshot]), pending());
  const spaced = pending(); spaced.needs_context.question = "  Which system?\n";
  assert.deepEqual(parseRetrievalSearchResponse(spaced, [snapshot]), spaced);
  const choice = pending(); delete choice.needs_context; choice.result_status = "clarification_required";
  choice.clarification = { question: "Which task?", clarification_token: token("c2"), options: [
    { option_id: "one", label: "First task", resolved_query: "Does A affect B?" },
    { option_id: "two", label: "Second task", resolved_query: "Does C affect D?" },
  ] };
  assert.deepEqual(parseRetrievalSearchResponse(choice, [snapshot]), choice);
  choice.clarification.clarification_token = token("c1");
  assert.throws(() => parseRetrievalSearchResponse(choice, [snapshot]));
  for (const mutate of [
    (r) => { r.stage = "ai_rerank"; }, (r) => { r.schema_version = "evidence-retrieval/2.2"; },
    (r) => { r.query_plan_handoff = token("v1"); }, (r) => { r.clarification = null; },
    (r) => { r.preview = [{}]; }, (r) => { r.clusters = [{}]; },
    (r) => { r.query_plan = {}; }, (r) => { r.retrieval_confidence = {}; },
    (r) => { r.channels.lexical.attempted = true; }, (r) => { r.channels.rerank.returned = 1; },
    (r) => { r.needs_context.context_token = token("c2"); }, (r) => { r.needs_context.extra = true; },
    (r) => { r.needs_context.question = "x".repeat(1001); },
  ]) { const response = pending(); mutate(response); assert.throws(() => parseRetrievalSearchResponse(response, [snapshot])); }
  for (const stage of ["preview", "ai_rerank"]) assert.deepEqual(parseRetrievalSearchResponse(resolvedWire(stage), [snapshot]), resolvedWire(stage));
  for (const key of ["needs_context", "clarification"]) assert.throws(() => parseRetrievalSearchResponse({ ...resolvedWire(), [key]: null }, [snapshot]));
});

test("client preserves complete context, requires resolved task, sanitizes conflicts and never replays POST", async () => {
  const request = dto(input({ context_submission: { context_token: token("n1"), content: "  System B  " } }));
  let calls = 0;
  let response = pending();
  let status = 200;
  const client = new RetrievalClient({ baseUrl: "https://compiler.example", sharedSecret: "retrieval-test-secret-0123456789012345", fetch: async (_url, init) => {
    calls++; assert.deepEqual(JSON.parse(init.body), request); return Response.json(response, { status });
  } });
  assert.equal((await client.search(request, [snapshot])).result_status, "needs_context");
  response = resolvedWire(); delete response.query_plan.resolved_query;
  await assert.rejects(client.search(request, [snapshot]), /invalid shape/u);
  status = 409; response = { error: { code: "CONTEXT_REPREVIEW_REQUIRED", message: "PRIVATE TOKEN", retryable: true } };
  await assert.rejects(client.search(request, [snapshot]), (error) => error.status === 409 && error.code === "CONTEXT_REPREVIEW_REQUIRED"
    && error.details.retryable === false && !error.message.includes("PRIVATE"));
  assert.equal(calls, 3);
});

// Exercise the real route with bounded storage/sync stubs; response validation above uses the actual client.
const routeState = { searches: [], bindingReads: 0, response: pending(), empty: false, sessions: new Map() };
globalThis.__retrievalContextRoute = routeState;
const rootUrl = new URL("../", import.meta.url).href;
const retrievalUrl = new URL("../lib/evidence-retrieval/index.ts", import.meta.url).href;
const routeRetrieval = `export * from ${JSON.stringify(retrievalUrl)};
export async function resolveEvidenceRetrievalCorpus(_db,_project,request) {
 const state=globalThis.__retrievalContextRoute;
 return {request,queryText:request.query,queryDocument:null,manifests:state.empty?[]:[{snapshotId:${JSON.stringify(snapshot)}}]};
}
export async function loadEvidenceRetrievalDocumentsByRefs(){throw new Error('pending must not hydrate');}
export async function loadLatestEvidenceRetrievalAssessmentsByRefs(){throw new Error('pending must not hydrate');}`;
const routeStore = `export const PROJECT_ID='PROJECT';
export class ApiError extends Error {constructor(status,code,message,details){super(message);Object.assign(this,{status,code,details});}}
export async function getBindings(){globalThis.__retrievalContextRoute.bindingReads++;return {DB:{}};}
export async function getCompilerConfiguration(){return {baseUrl:'https://compiler.example',sharedSecret:'s'.repeat(40)};}
export async function readJsonObject(request){return request.json();}
export function safeError(e){return Response.json({ok:false,error:{code:e.code,message:e.message,details:e.details}},{status:e.status??500});}`;
const routeClient = `export class RetrievalClient {async search(request){const s=globalThis.__retrievalContextRoute;s.searches.push(request);return structuredClone(s.response);}}`;
const routeSync = `export async function ensureRetrievalCoverage(){return {checkedSnapshots:1,pendingSnapshots:0,warnings:[]};}`;
// Server-side continuation sessions: the browser holds only rs1. IDs.
const routeSessions = `export async function createRetrievalSession(_db,kind,token){const sessions=globalThis.__retrievalContextRoute.sessions;
 const id='rs1.'+String(sessions.size+1).padStart(43,'0');sessions.set(id,{kind,token});return id;}
export async function resolveRetrievalSession(_db,kind,id){const entry=globalThis.__retrievalContextRoute.sessions.get(id);return entry&&entry.kind===kind?entry.token:null;}`;
const session = (kind, value) => {
  const id = `rs1.${`t${routeState.sessions.size + 1}`.padStart(43, "0")}`;
  routeState.sessions.set(id, { kind, token: value });
  return id;
};
const data = (source) => `data:text/javascript,${encodeURIComponent(source)}`;
const aliases = { "@/lib/evidence-retrieval": data(routeRetrieval), "@/lib/server/evidence-store": data(routeStore),
  "@/lib/server/retrieval-client": data(routeClient), "@/lib/server/retrieval-sync": data(routeSync),
  "@/lib/server/retrieval-sessions": data(routeSessions) };
register(data(`let root,aliases;export function initialize(d){root=d.root;aliases=d.aliases;}
export async function resolve(s,c,next){if(aliases[s])return {url:aliases[s],shortCircuit:true};
if(s.startsWith('@/')){let p=s.slice(2);if(!/\\.[cm]?[jt]sx?$/.test(p))p+='.ts';return {url:new URL(p,root).href,shortCircuit:true};}return next(s,c);}`),
{ parentURL: import.meta.url, data: { root: rootUrl, aliases } });
const { POST } = await import("../app/api/evidence-search/route.ts");
const post = (body, mode = "") => POST(new Request(`https://example.test/api/evidence-search${mode}`, {
  method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) }));

test("route returns pending context before hydration and forwards exact history/submission", async () => {
  routeState.empty = false; routeState.response = pending(); routeState.searches = [];
  const request = input({ context_submission: { context_token: session("context", token("n1")), content: "  One more detail\n" } });
  const response = await post(request); const body = await response.json();
  assert.equal(response.status, 200); assert.equal(response.headers.get("cache-control"), "private, no-store");
  assert.equal(body.resultStatus, "needs_context"); assert.equal(body.queryPlan, null); assert.equal(body.queryPlanHandoff, null);
  assert.equal(body.needsContext.question, pending().needs_context.question);
  assert.deepEqual(routeState.sessions.get(body.needsContext.contextToken), { kind: "context", token: token("n1") });
  assert.deepEqual(body.previewResults, []); assert.deepEqual(body.clusters, []); assert.equal(body.retrievalConfidence, null);
  assert.deepEqual(routeState.searches[0].conversation_history, history);
  // The Compiler receives the exact submission with the token behind the session ID.
  assert.deepEqual(routeState.searches[0].context_submission, { context_token: token("n1"), content: "  One more detail\n" });
});

test("route maps stale pre-Compiler context continuations to a safe 409", async () => {
  const continuation = { context_submission: { context_token: session("context", token("n1")), content: "x" } };
  for (const extra of [{ query: "" }, { corpus: "same_paper" }, { conversation_history: [{ role: "system", content: "PRIVATE" }] }]) {
    routeState.bindingReads = 0;
    const response = await post(input({ ...continuation, ...extra }));
    assert.equal(response.status, 409); assert.equal(routeState.bindingReads, 0);
    assert.equal((await response.json()).error.message, "The context is no longer valid; run preview again");
  }
  routeState.empty = true;
  const response = await post(input(continuation));
  assert.equal(response.status, 409); assert.equal((await response.json()).error.code, "CONTEXT_REPREVIEW_REQUIRED");
  routeState.empty = false;
  // Raw Compiler tokens and unknown sessions also re-preview without reading bindings.
  for (const context_token of [token("n1"), `rs1.${"q".repeat(43)}`]) {
    routeState.bindingReads = 0;
    const stale = await post(input({ context_submission: { context_token, content: "x" } }));
    assert.equal(stale.status, 409); assert.equal((await stale.json()).error.code, "CONTEXT_REPREVIEW_REQUIRED");
    assert.equal(routeState.bindingReads, context_token.startsWith("rs1.") ? 1 : 0);
  }
});

test("real Python RetrievalService 2.3 envelopes cross the actual TS parser and client", async () => {
  const compilerPath = fileURLToPath(new URL("../compiler/", import.meta.url));
  const script = String.raw`
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
sys.path.insert(0, str(Path.cwd() / "tests"))
from test_retrieval_context import ContextPlanner, ContextClassifier, final_request
from test_retrieval_service import _service, _index_request, _manifest, _document, _HANDOFF_SECRET_A
from compiler_service.retrieval_contract import HybridSearchRequest

def execute():
    wire = json.load(sys.stdin)
    base = HybridSearchRequest.model_validate(wire, strict=True)
    snapshot = base.filters.active_snapshot_ids[0]
    planner = ContextPlanner("needs_context", "clarify", "retrieve")
    with TemporaryDirectory(prefix="retrieval-wire-context-") as directory:
        service = _service(Path(directory), planner=planner, classifier=ContextClassifier(),
            query_plan_handoff_secret=_HANDOFF_SECRET_A)
        indexed = _index_request(_manifest(snapshot, document_count=2), [
            _document("pacific", "Pacific ecology 太平洋 😀", snapshot_id=snapshot),
            _document("ordos", "Ordos tectonics 鄂尔多斯", snapshot_id=snapshot),
        ]).model_copy(update={"project_id": base.project_id})
        service.index_snapshot(indexed)
        first = service.search(base)
        supplement = "  Pacific ecology 太平洋 😀\n"
        second_wire = base.model_dump(mode="json")
        second_wire["context_submission"] = {
            "context_token": first.needs_context.context_token, "content": supplement,
        }
        second_request = HybridSearchRequest.model_validate(second_wire, strict=True)
        second = service.search(second_request)
        selected_wire = base.model_dump(mode="json")
        selected_wire["clarification_selection"] = {
            "clarification_token": second.clarification.clarification_token, "option_id": "B",
        }
        selected = HybridSearchRequest.model_validate(selected_wire, strict=True)
        preview = service.search(selected)
        if not preview.preview:
            raise AssertionError("fixture requires actual retrieval candidates")
        final_input = final_request(selected, preview)
        def forbidden(*args, **kwargs):
            raise AssertionError("final cannot replan or recall")
        planner.plan_context = forbidden
        service.store.lexical_search = forbidden
        service.vector_index.search = forbidden
        service.dense_encoder.embed_query = forbidden
        final = service.search(final_input)
        assert final.query_plan == preview.query_plan
        assert all(item[0]["conversation_history"] == wire["conversation_history"] for item in planner.seen)
        assert planner.seen[-1][1]["supplements"] == [supplement]
        assert planner.seen[-1][1]["selected_query"] == "Pacific ecology"
        assert planner.seen[-1][0]["filters"]["openalex_scope"] == wire["filters"]["openalex_scope"]
        def request_wire(request):
            value = request.model_dump(mode="json")
            # Public TS DTOs omit absent optional capabilities; Python keeps internal None defaults.
            for field in ["query_plan_handoff", "context_submission", "clarification_selection"]:
                if value.get(field) is None:
                    value.pop(field, None)
            return value
        print(json.dumps({"pairs": [
            {"request": request_wire(request), "response": response.model_dump(mode="json")}
            for request, response in [(base, first), (second_request, second), (selected, preview), (final_input, final)]
        ]}, ensure_ascii=False))

try:
    execute()
except Exception as error:
    # Test bearer capabilities must never appear in failure diagnostics.
    print(type(error).__name__, file=sys.stderr)
    raise SystemExit(1)
`;
  const wire = dto(input({ openalex_scope: { level: "topic", id: "https://openalex.org/T123" } }));
  const output = execFileSync(`${compilerPath}.venv/bin/python`, ["-c", script], {
    cwd: compilerPath, input: JSON.stringify(wire), encoding: "utf8", maxBuffer: 4 * 1024 * 1024, timeout: 30_000,
  });
  const { pairs } = JSON.parse(output);
  const statuses = [];
  let calls = 0;
  for (const { request, response } of pairs) {
    const parsed = parseRetrievalSearchResponse(response, [snapshot]);
    statuses.push(parsed.result_status);
    const client = new RetrievalClient({ baseUrl: "https://compiler.example", sharedSecret: "wire-parity-test-secret-0123456789012345",
      fetch: async (_url, init) => {
        calls++;
        // Boolean comparison prevents signed tokens being logged on assertion failure.
        assert.equal(JSON.stringify(JSON.parse(init.body)) === JSON.stringify(request), true, "full request must survive client transport");
        return Response.json(response);
      } });
    const result = await client.search(request, [snapshot]);
    assert.equal(result.result_status, parsed.result_status);
    assert.deepEqual(request.conversation_history, history);
    assert.deepEqual(request.filters.openalex_scope, wire.filters.openalex_scope);
  }
  assert.deepEqual(statuses, ["needs_context", "clarification_required", "matches", "matches"]);
  assert.equal(calls, 4);
  const final = pairs[3].response;
  assert.equal(final.stage, "ai_rerank");
  assert.equal(final.channels.lexical.status, "skipped");
  assert.equal(final.channels.dense.status, "skipped");
  assert.equal(final.query_plan.resolved_query, "Operation: answer_questions\nTask instruction: Does this affect it?\nInput 1: Pacific ecology");
});
