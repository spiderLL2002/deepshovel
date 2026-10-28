import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import ts from "typescript";

const require = createRequire(import.meta.url);

// Evaluate the actual components without mounting child panels or fetching data.
function componentModule(file, extraExports = "") {
  const states = [];
  let cursor = 0;
  const react = {
    ...require("react"),
    useState(initial) {
      const index = cursor++;
      if (!(index in states)) states[index] = typeof initial === "function" ? initial() : initial;
      return [states[index], (value) => { states[index] = value; }];
    },
  };
  const source = readFileSync(new URL(file, import.meta.url), "utf8") + extraExports;
  const compiled = ts.transpileModule(source, {
    compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS },
  }).outputText;
  const compiledModule = { exports: {} };
  const dependencies = new Proxy({ asRecord: (value) => value ?? {}, formatBytes: (value) => `${value} B`, shortHash: (value) => value }, {
    get: (target, name) => target[name] ?? String(name),
  });
  new Function("require", "module", "exports", compiled)((name) => {
    if (name === "react") return react;
    if (name === "react/jsx-runtime") return require(name);
    return dependencies;
  }, compiledModule, compiledModule.exports);
  return {
    render(name, props) { cursor = 0; return compiledModule.exports[name](props); },
  };
}

function elements(tree) {
  if (!tree || typeof tree !== "object") return [];
  if (Array.isArray(tree)) return tree.flatMap(elements);
  return [tree, ...elements(tree.props?.children)];
}

const claims = Array.from({ length: 4 }, (_, index) => ({
  id: `snapshot:one:claim:${index + 1}`, kind: "atomic_claim", proposition: `Proposition ${index + 1}`,
}));
const detail = {
  snapshot: { id: "snapshot:one" },
  graph: { claims, observations: [], anchors: [], artifact_versions: [] },
  paths: claims.map((claim) => ({ root_claim_id: claim.id, entities: [claim] })),
  verifications: [],
};

function selectedClaim(tree) {
  return elements(tree).find((node) => node.props?.className === "paper-claim-index-row" && node.props["aria-current"] === "true");
}

test("opening a Topic Claim passes its exact identity and selects its evidence, not Claim 01", () => {
  const graph = componentModule("../app/components/evidence-knowledge-graph.tsx", "\nexport { TaxonomyLedger };\n");
  let target;
  const tree = graph.render("TaxonomyLedger", {
    taxonomy: { nodes: [] },
    path: ["domain", "field", "subfield", "topic"].map((level) => ({ id: level, level })),
    claims: [{ snapshotId: detail.snapshot.id, claimId: claims[3].id, canonicalClaim: claims[3].proposition }],
    onOpenSnapshot: (snapshotId, claimId) => { target = { snapshotId, claimId }; },
  });
  elements(tree).find((node) => node.type === "button" && node.props.children === "查看证据").props.onClick();
  assert.deepEqual(target, { snapshotId: detail.snapshot.id, claimId: claims[3].id });

  const paper = componentModule("../app/components/paper-detail.tsx");
  const props = { detail, initialClaimId: target.claimId, loading: false };
  paper.render("PaperDetail", { ...props, detail: null, loading: true });
  const opened = paper.render("PaperDetail", props);
  assert.equal(selectedClaim(opened).key, claims[3].id);
  const panel = elements(opened).find((node) => node.props?.path);
  assert.equal(panel.props.path.root_claim_id, claims[3].id);
  // The initial target must not prevent subsequent manual Claim selection.
  elements(opened).find((node) => node.props?.className === "paper-claim-index-row").props.onClick();
  assert.equal(selectedClaim(paper.render("PaperDetail", props)).key, claims[0].id);
});

test("paper-level opens and stale Claim targets retain first available Claim fallback", () => {
  for (const initialClaimId of [undefined, "missing-claim"]) {
    const paper = componentModule("../app/components/paper-detail.tsx");
    assert.equal(selectedClaim(paper.render("PaperDetail", { detail, initialClaimId })).key, claims[0].id);
  }
});

test("search preview, representative, and member links retain the returned Claim identity", () => {
  const search = componentModule("../app/components/evidence-search.tsx", "\nexport { PreviewResult, ClusterResult, MemberRow };\n");
  const member = {
    snapshotId: detail.snapshot.id,
    claim: { id: claims[3].id, canonicalClaim: claims[3].proposition },
    source: { artifactName: "Paper" },
    queryRelation: { relation: "related", scopeRelation: "unknown" },
  };
  for (const name of ["PreviewResult", "ClusterResult", "MemberRow"]) {
    let target;
    const tree = search.render(name, {
      member, rank: 1, cluster: { representative: member, members: [] },
      onOpenSnapshot: (...args) => { target = args; },
    });
    elements(tree).find((node) => node.type === "button" && node.props.className === "text-button").props.onClick();
    assert.deepEqual(target, [detail.snapshot.id, claims[3].id], name);
  }
});

test("paper comparison result opens its candidate Claim instead of the first paper Claim", () => {
  const synthesis = componentModule("../app/components/evidence-synthesis.tsx", "\nexport { EvidenceRow };\n");
  let target;
  const tree = synthesis.render("EvidenceRow", {
    group: "context", index: 0,
    item: {
      paper: { title: "Hydrothermal fluids" },
      candidateClaim: { snapshotId: detail.snapshot.id, claimId: claims[1].id, canonicalClaim: claims[1].proposition },
      candidateSource: { mediaType: "application/pdf", downloadUrl: "/paper.pdf" },
      exactAnchors: [], limitations: [], scopeAlignment: { relation: "overlap" },
    },
    onOpenSnapshot: (...args) => { target = args; },
  });
  elements(tree).find((node) => node.type === "button" && node.props.children === "打开论文完整证据").props.onClick();
  assert.deepEqual(target, [detail.snapshot.id, claims[1].id]);
  const paper = componentModule("../app/components/paper-detail.tsx");
  assert.equal(selectedClaim(paper.render("PaperDetail", { detail, initialClaimId: target[1] })).key, claims[1].id);
});

test("leaving paper comparison cancels pending jobs and preserves completed results", () => {
  const synthesis = componentModule("../app/components/evidence-synthesis.tsx", "\nexport { cancelPendingComparisonJobs };\n");
  const completed = { status: "completed", result: { groups: { support: ["existing evidence"] } } };
  const failed = { status: "failed", error: "existing error" };
  const jobs = [completed, { status: "running", result: null }, { status: "queued", result: null }, failed];
  const cancelled = synthesis.render("cancelPendingComparisonJobs", jobs);
  assert.equal(cancelled[0], completed);
  assert.equal(cancelled[3], failed);
  assert.deepEqual(cancelled.map((job) => job.status), ["completed", "cancelled", "cancelled", "failed"]);
  assert.deepEqual(jobs.map((job) => job.status), ["completed", "running", "queued", "failed"]);
});

test("library rows retain original download and retry actions after local failed-task dismissal", () => {
  const library = componentModule("../app/components/library-view.tsx");
  for (const libraryStatus of ["pending", "failed", "processing", "completed"]) {
    const version = { id: "version:qa:2", artifactId: "artifact:qa", mediaType: "application/pdf", downloadUrl: "/api/artifacts/qa", byteSize: 100, sha256: "qa" };
    let retry;
    const tree = library.render("LibraryView", {
      artifacts: [{ artifactId: "artifact:qa", title: "QA paper", libraryStatus, latestVersion: version, machineVerification: { status: "unassessed" }, updatedAt: "2026-10-20T09:00:00Z" }],
      pageInfo: {hasMore:false}, query:"", onCompile: (target) => { retry = target; },
    });
    const nodes = elements(tree);
    assert.ok(nodes.some((node) => node.type === "a" && node.props.href === version.downloadUrl));
    const action = nodes.find((node) => node.type === "button" && ["Compile", "重试 Compile"].includes(node.props.children));
    if (["pending", "failed"].includes(libraryStatus)) {
      assert.ok(action);
      action.props.onClick();
      assert.equal(retry, version);
    } else assert.equal(action, undefined, "completed or running versions must not expose duplicate Compile actions");
  }
});
