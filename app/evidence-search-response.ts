import type { EvidenceSearchResponse } from "./workspace-types";

const record = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const text = (value: unknown): value is string => typeof value === "string";
const optionalText = (value: unknown) => value == null || text(value);
const list = (value: unknown, valid: (item: unknown) => boolean): boolean => Array.isArray(value) && value.every(valid);
const relations = ["equivalent", "supports", "contradicts", "qualifies", "refines", "supplements", "related"];

/** Validate fields consumed by this UI before storing an untrusted HTTP payload. */
function validMember(value: unknown, withRelation: boolean): boolean {
  const member = record(value);
  const claim = record(member.claim);
  const scope = record(claim.scope);
  const path = record(member.shortestEvidencePath);
  const assessment = record(member.assessment);
  const relation = record(member.queryRelation);
  return text(member.snapshotId) && Boolean(member.snapshotId) && text(record(member.source).artifactName)
    && text(claim.id) && Boolean(claim.id) && text(claim.canonicalClaim) && optionalText(claim.originalClaim)
    && ["entities", "location", "formation", "sample_type", "method", "condition", "measurement"].every(key => list(scope[key], text))
    && (scope.time == null || optionalText(record(scope.time).label))
    && list(member.evidenceAnchors, value => {
      const anchor = record(value);
      return text(anchor.id) && (anchor.page == null || typeof anchor.page === "number")
        && ["section", "quote", "figure", "table", "regionLabel"].every(key => optionalText(anchor[key]));
    })
    && list(path.nodes, value => {
      const node = record(value);
      return text(node.id) && text(node.kind) && text(node.label);
    })
    && (member.assessment == null || (text(assessment.verdict) && typeof assessment.confidence === "number"))
    && (!withRelation || (relations.includes(String(relation.relation))
      && ["query_to_result", "result_to_query"].includes(String(relation.direction))
      && ["equivalent", "overlap", "query_within_candidate", "candidate_within_query", "disjoint", "unknown"].includes(String(relation.scopeRelation))));
}

export function parseEvidenceSearchResponse(value: unknown, stage: "preview" | "ai_rerank", quickPreview = false): EvidenceSearchResponse {
  const response = record(value);
  const query = record(response.query);
  const plan = record(response.queryPlan);
  const channels = record(response.channels);
  if (record(channels.relation).status === "degraded") {
    throw new Error("模型分析未完成，本次未能生成有效答案，请稍后重试。");
  }
  let valid = response.ok === true && response.stage === stage
    && ["claim_search", "supporting_evidence", "contradiction_search"].includes(String(query.intent))
    && (response.queryPlan === null || (text(plan.queryType) && optionalText(plan.resolvedQuery)))
    && list(response.previewResults, member => validMember(member, false))
    && (stage === "preview" || (Array.isArray(response.previewResults) && response.previewResults.length === 0))
    && list(response.clusters, value => {
      const cluster = record(value);
      return text(cluster.id) && relations.includes(String(cluster.relation))
        && typeof cluster.distinctPaperFamilies === "number" && validMember(cluster.representative, true)
        && list(cluster.members, member => validMember(member, true));
    })
    && ((quickPreview && stage === "preview") || ["lexical", "dense", "rerank", "relation"].every(key => ["ok", "degraded", "skipped"].includes(String(record(channels[key]).status))));
  if (response.resultStatus === "clarification_required") {
    const clarification = record(response.clarification);
    valid &&= stage === "preview" && text(clarification.question) && text(clarification.clarificationToken)
      && list(clarification.options, value => {
        const option = record(value);
        return text(option.optionId) && text(option.label) && text(option.resolvedQuery);
      });
  } else if (response.resultStatus === "needs_context") {
    const context = record(response.needsContext);
    valid &&= stage === "preview" && text(context.question) && text(context.contextToken);
  } else {
    valid &&= ["matches", "no_direct_answer", "degraded"].includes(String(response.resultStatus))
      && ((quickPreview && stage === "preview" && response.queryPlan === null)
        || (["paper_lookup", "topic_search", "claim_search", "supporting_evidence", "contradiction_search"].includes(String(plan.queryType)) && optionalText(plan.resolvedQuery)))
      && optionalText(response.queryPlanHandoff);
  }
  if (!valid) throw new Error("服务返回的结果格式异常，本次分析未完成。");
  return value as EvidenceSearchResponse;
}

