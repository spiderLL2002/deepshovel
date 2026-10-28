"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
import type { Scope } from "@/lib/evidence-graph";
import type {
  EvidenceSearchCluster,
  EvidenceSearchClarificationResponse,
  EvidenceSearchNeedsContextResponse,
  EvidenceSearchIntent,
  EvidenceSearchMember,
  EvidenceSearchPreviewMember,
  EvidenceSearchRelation,
  EvidenceSearchRelationGroup,
  EvidenceSearchResponse,
} from "../workspace-types";
import { evidenceSearchRelationGroupOrder, readJson, responseError } from "../workspace-types";
import { parseEvidenceSearchResponse } from "../evidence-search-response";
import { fetchStreamed } from "../streamed-fetch";
import { contextHistoryError, contextTextError, contextTextIsBlank, continueContextRequest, type SearchHistoryTurn } from "../search-context";
import { ScientificContent } from "./scientific-content-lazy";
import { ClaimListDisclosure } from "./claim-disclosure";
import type { OpenAlexSearchScope } from "./evidence-knowledge-graph";
import { UiIcon } from "./ui-icon";

export type EvidenceSearchClaimRef = {
  snapshotId: string;
  claimId: string;
  proposition: string;
};

type Props = {
  active?: boolean;
  initialClaim?: EvidenceSearchClaimRef;
  initialOpenAlexScope?: OpenAlexSearchScope;
  onClearInitialClaim?: () => void;
  onClearOpenAlexScope?: () => void;
  onOpenSnapshot: (snapshotId: string, claimId: string) => void;
};

const EXAMPLE_QUERY = "检索支持或反对以下观点的证据：南鸟岛地区的深海富稀土泥中，稀土元素和钇主要赋存于生物成因磷酸钙。";

const RELATION_GROUPS: Array<{ id: EvidenceSearchRelationGroup; title: string }> = [
  { id: "related", title: "主题相关" },
  { id: "equivalent", title: "等价观点" },
  { id: "support", title: "支持证据" },
  { id: "contradict", title: "相反证据" },
  { id: "qualify", title: "限定与细化" },
  { id: "supplement", title: "补充证据" },
];

function relationGroup(relation: EvidenceSearchRelation): EvidenceSearchRelationGroup {
  if (relation === "related") return "related";
  if (relation === "equivalent") return "equivalent";
  if (relation === "supports") return "support";
  if (relation === "contradicts") return "contradict";
  if (relation === "qualifies" || relation === "refines") return "qualify";
  return "supplement";
}

function relationText(member: EvidenceSearchMember) {
  const { relation, direction } = member.queryRelation;
  if (relation === "related") return null;
  if (relation === "equivalent") return "与检索观点等价";
  if (relation === "contradicts") return "与检索观点给出相反结论";
  if (relation === "supports") {
    return direction === "result_to_query" ? "这条观点支持检索观点" : "检索起点支持这条观点";
  }
  if (relation === "qualifies") return "为检索观点补充成立条件";
  if (relation === "refines") return "进一步细化检索观点";
  return "提供补充材料";
}

function scopeRelationText(value: EvidenceSearchMember["queryRelation"]["scopeRelation"]) {
  const labels = {
    equivalent: "适用范围一致",
    overlap: "适用范围重叠",
    query_within_candidate: "检索范围更窄",
    candidate_within_query: "候选范围更窄",
    disjoint: "适用范围不同",
    unknown: "适用范围待核对",
  } as const;
  return labels[value];
}

function scopeSummary(scope: Scope) {
  const parts = [
    ...scope.entities,
    ...scope.location,
    scope.time?.label,
    ...scope.formation,
    ...scope.sample_type,
    ...scope.method,
    ...scope.condition,
    ...scope.measurement,
  ].filter((value): value is string => Boolean(value));
  return parts.slice(0, 8).join(" · ") || "未限定研究范围";
}

function assessmentText(verdict: string) {
  const labels: Record<string, string> = {
    supported: "本篇证据支持这条观点",
    contradicted: "本篇证据与这条观点不一致",
    qualified: "这条观点只在有限条件下成立",
    insufficient_evidence: "本篇证据不足以判断这条观点",
    uncertain: "本篇证据暂无法确定这条观点",
  };
  return labels[verdict.toLowerCase()] ?? "本篇核验结果待人工复核";
}

function evidenceLocator(anchor: EvidenceSearchMember["evidenceAnchors"][number]) {
  return [
    anchor.page ? `第 ${anchor.page} 页` : null,
    anchor.section,
    anchor.figure,
    anchor.table,
    anchor.regionLabel,
  ].filter(Boolean).join(" · ") || "原文位置";
}

function SearchScopeDetails({ scope }: { scope: Scope }) {
  return (
    <details className="search-scope-details">
      <summary>范围与关键词</summary>
      <ScientificContent value={scopeSummary(scope)} inline />
    </details>
  );
}

function AssessmentNote({ member }: { member: EvidenceSearchMember }) {
  if (!member.assessment) return null;
  return (
    <p className="cluster-assessment">
      <strong>{assessmentText(member.assessment.verdict)}</strong>
      <span>可信度 {Math.round(member.assessment.confidence * 100)}%</span>
    </p>
  );
}

function EvidenceDetails({ member }: { member: EvidenceSearchMember }) {
  const pathPreview = member.shortestEvidencePath.nodes
    .filter((node) => ["source_anchor", "structured_visual_evidence", "observation"].includes(node.kind))
    .slice(0, 8);
  return (
    <details className="member-evidence">
      <summary>展开原文与证据链</summary>
      {member.claim.originalClaim && member.claim.originalClaim !== member.claim.canonicalClaim ? (
        <div className="original-claim">
          <strong>论文原始表达</strong>
          <ScientificContent value={member.claim.originalClaim} />
          {member.claim.originalClaimTruncated ? <p className="evidence-preview-note">完整引文请查看论文详情。</p> : null}
        </div>
      ) : null}
      {member.evidenceAnchors.length ? (
        <ol className="anchor-preview-list">
          {member.evidenceAnchors.map((anchor) => (
            <li key={anchor.id}>
              <span>{evidenceLocator(anchor)}</span>
              {anchor.quote ? <ScientificContent value={anchor.quote} /> : <p className="muted">该原文位置没有可安全展示的精确引文。</p>}
            </li>
          ))}
        </ol>
      ) : null}
      {!member.evidenceAnchors.length && pathPreview.length ? (
        <ol className="path-preview-list">
          {pathPreview.map((node) => (
            <li key={node.id}>
              <span>{node.kind === "observation" ? "观察结果" : "证据位置"}</span>
              <ScientificContent value={node.label} />
            </li>
          ))}
        </ol>
      ) : null}
      {member.evidenceAnchorsTruncated || member.shortestEvidencePath.truncated ? (
        <p className="evidence-preview-note">完整证据链请查看论文详情。</p>
      ) : null}
      {!member.evidenceAnchors.length && !pathPreview.length ? <p className="muted">暂无可预览的原文位置。</p> : null}
    </details>
  );
}

function MemberRow({ member, onOpenSnapshot }: { member: EvidenceSearchMember; onOpenSnapshot: (snapshotId: string, claimId: string) => void }) {
  return (
    <article className="cluster-member">
      <header>
        <div>
          <span className="member-source" title={member.source.artifactName}>{member.source.artifactName}</span>
          <div className="search-claim-title"><ScientificContent value={member.claim.canonicalClaim} inline /></div>
          <SearchScopeDetails scope={member.claim.scope} />
        </div>
        <span className="scope-relation">{scopeRelationText(member.queryRelation.scopeRelation)}</span>
      </header>
      {relationText(member) ? <p className="member-relation">{relationText(member)}</p> : null}
      <AssessmentNote member={member} />
      <EvidenceDetails member={member} />
      <button className="text-button" type="button" onClick={() => onOpenSnapshot(member.snapshotId, member.claim.id)}>
        查看论文完整证据
      </button>
    </article>
  );
}

function ClusterResult({ cluster, onOpenSnapshot }: { cluster: EvidenceSearchCluster; onOpenSnapshot: (snapshotId: string, claimId: string) => void }) {
  const representative = cluster.representative;
  return (
    <article className="proposition-cluster">
      <header>
        <div className="cluster-title-line">
          {relationText(representative) ? <span className="relation-badge">{relationText(representative)}</span> : null}
          <span>{cluster.distinctPaperFamilies} 篇独立论文</span>
        </div>
        <div className="search-claim-title"><ScientificContent value={representative.claim.canonicalClaim} inline /></div>
        <SearchScopeDetails scope={representative.claim.scope} />
      </header>
      <div className="cluster-representative">
        <span className="member-source" title={representative.source.artifactName}>代表证据 · {representative.source.artifactName}</span>
        <AssessmentNote member={representative} />
        <EvidenceDetails member={representative} />
        <button className="text-button" type="button" onClick={() => onOpenSnapshot(representative.snapshotId, representative.claim.id)}>
          查看代表论文
        </button>
      </div>
      {cluster.members.length ? (
        <details className="cluster-members">
          <summary>展开另外 {cluster.members.length} 条论文证据</summary>
          <div>
            {cluster.members.map((member) => (
              <MemberRow member={member} onOpenSnapshot={onOpenSnapshot} key={`${member.snapshotId}:${member.claim.id}`} />
            ))}
          </div>
        </details>
      ) : null}
    </article>
  );
}

function PreviewResult({ member, rank, onOpenSnapshot }: {
  member: EvidenceSearchPreviewMember;
  rank: number;
  onOpenSnapshot: (snapshotId: string, claimId: string) => void;
}) {
  return (
    <article className="preview-result">
      <span className="preview-rank" aria-label={`初排第 ${rank} 名`}>{rank}</span>
      <div>
        <span className="member-source" title={member.source.artifactName}>{member.source.artifactName}</span>
        <div className="search-claim-title"><ScientificContent value={member.claim.canonicalClaim} inline /></div>
        <SearchScopeDetails scope={member.claim.scope} />
        <button className="text-button" type="button" onClick={() => onOpenSnapshot(member.snapshotId, member.claim.id)}>查看论文证据</button>
      </div>
    </article>
  );
}

export function EvidenceSearch({ active = true, initialClaim, initialOpenAlexScope, onClearInitialClaim, onClearOpenAlexScope, onOpenSnapshot }: Props) {
  const hasPaperContext = Boolean(initialClaim);
  const [queryClaim, setQueryClaim] = useState<EvidenceSearchClaimRef | null>(() => initialClaim ?? null);
  const [query, setQuery] = useState("");
  const [intent, setIntent] = useState<EvidenceSearchIntent>("claim_search");
  const [corpus, setCorpus] = useState<"same_paper" | "cross_paper">("cross_paper");
  const [responseData, setResponseData] = useState<EvidenceSearchResponse | null>(null);
  const [previewData, setPreviewData] = useState<EvidenceSearchResponse | null>(null);
  const [quickData, setQuickData] = useState<EvidenceSearchResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [aiBusy, setAiBusy] = useState(false);
  const [aiError, setAiError] = useState("");
  const [error, setError] = useState("");
  const [previewExpanded, setPreviewExpanded] = useState(false);
  const [selectedInterpretation, setSelectedInterpretation] = useState<string | null>(null);
  const [conversationHistory, setConversationHistory] = useState<Array<SearchHistoryTurn & { id: number }>>([]);
  const historySequence = useRef(0);
  const historyEditor = useRef<HTMLDetailsElement | null>(null);
  const historyFocusTarget = useRef<number | null>(null);
  const addHistoryButton = useRef<HTMLButtonElement | null>(null);
  const [contextInput, setContextInput] = useState("");
  const [contextError, setContextError] = useState("");
  const [submittedSupplements, setSubmittedSupplements] = useState<string[]>([]);
  const [expandedRelationGroups, setExpandedRelationGroups] = useState<Set<EvidenceSearchRelationGroup>>(new Set());
  const requestGeneration = useRef(0);
  const requestController = useRef<AbortController | null>(null);
  const queryInput = useRef<HTMLTextAreaElement | null>(null);
  const previewSection = useRef<HTMLElement | null>(null);
  const clarificationHeading = useRef<HTMLHeadingElement | null>(null);
  const contextHeading = useRef<HTMLHeadingElement | null>(null);
  const contextTextarea = useRef<HTMLTextAreaElement | null>(null);
  const pendingClarification = useRef<{
    request: Record<string, unknown>;
    clarification: EvidenceSearchClarificationResponse["clarification"];
  } | null>(null);
  const pendingContext = useRef<{
    request: Record<string, unknown>;
    needsContext: EvidenceSearchNeedsContextResponse["needsContext"];
  } | null>(null);

  useEffect(() => {
    if (previewData?.resultStatus === "clarification_required") clarificationHeading.current?.focus();
    if (previewData?.resultStatus === "needs_context") contextHeading.current?.focus();
  }, [previewData]);

  useEffect(() => {
    if (historyFocusTarget.current === null) return;
    historyEditor.current?.querySelector<HTMLTextAreaElement>(`#history-content-${historyFocusTarget.current}`)?.focus();
    historyFocusTarget.current = null;
  }, [conversationHistory]);

  useEffect(() => {
    if (!active) return;
    return () => {
      requestGeneration.current += 1;
      requestController.current?.abort();
      requestController.current = null;
      setBusy(false);
      setAiBusy(false);
    };
  }, [active]);

  const clearSearchResult = () => {
    setQuickData(null);
    requestGeneration.current += 1;
    requestController.current?.abort();
    requestController.current = null;
    pendingClarification.current = null;
    pendingContext.current = null;
    setBusy(false);
    setAiBusy(false);
    setPreviewData(null);
    setResponseData(null);
    setError("");
    setAiError("");
    setPreviewExpanded(false);
    setSelectedInterpretation(null);
    setContextInput("");
    setContextError("");
    setSubmittedSupplements([]);
    setExpandedRelationGroups(new Set());
  };

  const trimmedQuery = query.trim() || (hasPaperContext ? "" : EXAMPLE_QUERY);
  const samePaperNeedsClaim = hasPaperContext && corpus === "same_paper" && !queryClaim;
  const missingSearchInput = hasPaperContext ? !queryClaim && !trimmedQuery : !trimmedQuery;
  const cannotSubmit = busy || missingSearchInput || samePaperNeedsClaim;

  const groupedClusters = useMemo(() => {
    const groups = new Map<EvidenceSearchRelationGroup, EvidenceSearchCluster[]>();
    for (const group of RELATION_GROUPS) groups.set(group.id, []);
    for (const cluster of responseData?.clusters ?? []) groups.get(relationGroup(cluster.relation))?.push(cluster);
    return groups;
  }, [responseData]);

  const orderedRelationGroups = useMemo(() => {
    const metadata = new Map(RELATION_GROUPS.map((group) => [group.id, group]));
    return evidenceSearchRelationGroupOrder(responseData?.query.intent ?? "claim_search")
      .map((id) => metadata.get(id))
      .filter((group): group is (typeof RELATION_GROUPS)[number] => Boolean(group));
  }, [responseData?.query.intent]);

  const startQuickPreview = (request: Record<string, unknown>, generation: number, signal: AbortSignal) => {
    setQuickData(null);
    if (request.query_claim || request.conversation_history || request.context_submission || request.clarification_selection) return;
    void fetch("/api/evidence-search?response=quick_preview", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ ...request, stage: "preview" }), signal,
    }).then(async response => {
      const result = await readJson(response);
      if (response.ok && !signal.aborted && generation === requestGeneration.current) {
        flushSync(() => setQuickData(parseEvidenceSearchResponse(result, "preview", true)));
      }
    }).catch(() => { /* The full retrieval continues independently. */ });
  };

  const submit = async (event?: React.FormEvent<HTMLFormElement>, optionId?: string, supplement?: string) => {
    event?.preventDefault();
    if (cannotSubmit || requestController.current) return;
    const historyError = contextHistoryError(conversationHistory);
    if (historyError) {
      setError(historyError);
      if (historyEditor.current) historyEditor.current.open = true;
      historyEditor.current?.querySelector("textarea")?.focus();
      return;
    }
    const pending = pendingClarification.current;
    const context = pendingContext.current;
    if (supplement !== undefined) {
      if (!context) return;
      const invalid = contextTextError(supplement);
      if (invalid) {
        setContextError(invalid);
        contextTextarea.current?.focus();
        return;
      }
    }
    if (optionId === undefined && supplement === undefined && (pending || context)) return;
    const selectedOption = optionId === undefined ? undefined
      : pending?.clarification.options.find((option) => option.optionId === optionId);
    if (optionId !== undefined && (!pending || !selectedOption)) return;
    const generation = requestGeneration.current + 1;
    requestGeneration.current = generation;
    const controller = new AbortController();
    requestController.current = controller;
    setBusy(true);
    setAiBusy(false);
    setError("");
    setAiError("");
    if (!pending && !context) setPreviewData(null);
    setResponseData(null);
    setPreviewExpanded(false);
    setExpandedRelationGroups(new Set());
    if (selectedOption) setSelectedInterpretation(selectedOption.resolvedQuery);
    if (!pending && !context) {
      setSelectedInterpretation(null);
      setSubmittedSupplements([]);
    }
    setContextError("");
    let previewCompleted = false;
    try {
      const baseRequest = context && supplement !== undefined ? continueContextRequest(context.request, {
        context_submission: { context_token: context.needsContext.contextToken, content: supplement },
      }) : pending && selectedOption ? continueContextRequest(pending.request, {
        clarification_selection: {
          clarification_token: pending.clarification.clarificationToken,
          option_id: selectedOption.optionId,
        },
      }) : {
        search_protocol: "evidence-retrieval/2.3",
        ...(trimmedQuery ? { query: trimmedQuery } : {}),
        ...(queryClaim ? { query_claim: { snapshot_id: queryClaim.snapshotId, claim_id: queryClaim.claimId } } : {}),
        ...(conversationHistory.length ? { conversation_history: conversationHistory.map(({ role, content }) => ({ role, content })) } : {}),
        intent,
        corpus: hasPaperContext ? corpus : "cross_paper",
        limit: 50,
        ...(initialOpenAlexScope ? { openalex_scope: { level: initialOpenAlexScope.level, id: initialOpenAlexScope.id } } : {}),
      };
      startQuickPreview(baseRequest, generation, controller.signal);
      const response = await fetchStreamed("/api/evidence-search", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          ...baseRequest,
          stage: "preview",
        }),
        signal: controller.signal,
      });
      const payload = await readJson(response);
      if (controller.signal.aborted || generation !== requestGeneration.current) return;
      if (!response.ok) throw new Error(responseError(payload, "相关证据检索失败，请调整条件后重试"));
      const preview = parseEvidenceSearchResponse(payload, "preview");
      previewCompleted = true;
      pendingClarification.current = null;
      pendingContext.current = null;
      if (supplement !== undefined) {
        setSubmittedSupplements((current) => [...current, supplement]);
        setContextInput("");
      }
      // Commit the retrieval stage independently before starting relation analysis.
      // The result remains visible while the second request is pending or fails.
      flushSync(() => setPreviewData(preview));
      if (preview.resultStatus === "clarification_required") {
        pendingClarification.current = { request: baseRequest, clarification: preview.clarification };
        return;
      }
      if (preview.resultStatus === "needs_context") {
        pendingContext.current = { request: baseRequest, needsContext: preview.needsContext };
        return;
      }
      if (!preview.queryPlan) throw new Error("模型分析未完成，请稍后重试。");
      // A Claim-seeded final pass also receives every other Claim from the
      // same paper directly, even when cross-paper retrieval found nothing.
      if (!preview.previewResults.length && !queryClaim) return;

      flushSync(() => setAiBusy(true));
      previewSection.current?.scrollIntoView({ block: "start", behavior: "instant" });
      const aiResponse = await fetchStreamed("/api/evidence-search", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          ...baseRequest,
          stage: "ai_rerank",
          ...(preview.queryPlanHandoff ? { query_plan_handoff: preview.queryPlanHandoff } : {}),
          ...(preview.previewResults.length ? {
            candidate_refs: preview.previewResults.map((member) => ({
              snapshot_id: member.snapshotId,
              claim_id: member.claim.id,
            })),
          } : {}),
        }),
        signal: controller.signal,
      });
      const aiPayload = await readJson(aiResponse);
      if (controller.signal.aborted || generation !== requestGeneration.current) return;
      const previewIsTopic = preview.queryPlan.queryType === "topic_search"
        || preview.queryPlan.queryType === "paper_lookup";
      if (!aiResponse.ok) {
        throw new Error(responseError(
          aiPayload,
          previewIsTopic ? "主题结果整理失败，请稍后重试" : "关系分析失败，请稍后重试",
        ));
      }
      setResponseData(parseEvidenceSearchResponse(aiPayload, "ai_rerank"));
    } catch (cause) {
      if (controller.signal.aborted || generation !== requestGeneration.current) return;
      // Stop an outstanding quick preview from resurfacing after terminal failure.
      controller.abort();
      const message = cause instanceof Error ? cause.message : "相关证据检索失败，请调整条件后重试";
      if (previewCompleted) setAiError(message);
      else setError(message);
    } finally {
      if (generation === requestGeneration.current) {
        requestController.current = null;
        setAiBusy(false);
        setBusy(false);
      }
    }
  };

  const activeQueryType = responseData?.queryPlan?.queryType ?? previewData?.queryPlan?.queryType;
  const clarification = previewData?.resultStatus === "clarification_required" ? previewData.clarification : null;
  const needsContext = previewData?.resultStatus === "needs_context" ? previewData.needsContext : null;
  const resolvedTask = responseData?.queryPlan?.resolvedQuery ?? previewData?.queryPlan?.resolvedQuery ?? selectedInterpretation;
  const isTopicSearch = activeQueryType === "topic_search" || activeQueryType === "paper_lookup";
  const resultCount = responseData?.clusters.length ?? 0;
  const previewResults = (previewData ?? quickData)?.previewResults ?? [];
  const searchFailed = Boolean(error || aiError);
  return (
    <section className="evidence-search" aria-labelledby="evidence-search-title">
      <div className="search-heading page-intro">
        <div>
          <h1 id="evidence-search-title">{hasPaperContext ? "围绕当前 Claim 继续检索" : "输入观点"}</h1>
        </div>
      </div>

      <form className="search-form" onSubmit={submit}>
        {queryClaim ? (
          <div className="query-claim-chip" aria-label="当前检索观点">
            <span>当前观点</span>
            <strong title={queryClaim.proposition}><ScientificContent value={queryClaim.proposition} inline /></strong>
            <button type="button" onClick={() => { clearSearchResult(); setQueryClaim(null); onClearInitialClaim?.(); }} aria-label="移除当前观点"><UiIcon name="close" size={15} /></button>
          </div>
        ) : null}
        {initialOpenAlexScope ? (
          <div className="query-claim-chip taxonomy-scope-chip" aria-label="OpenAlex 检索范围">
            <span>{initialOpenAlexScope.level}</span>
            <strong>{initialOpenAlexScope.label}</strong>
            <button type="button" onClick={() => { clearSearchResult(); onClearOpenAlexScope?.(); }} aria-label="移除 OpenAlex 检索范围"><UiIcon name="close" size={15} /></button>
          </div>
        ) : null}

        <div className="query-field">
          <textarea
            ref={queryInput}
            aria-label={hasPaperContext ? "补充问题（可选）" : "待核对观点"}
            value={query}
            onChange={(event) => { clearSearchResult(); setQuery(event.target.value); }}
            maxLength={8000}
            rows={3}
            placeholder={hasPaperContext ? "例如：是否有独立的地球化学证据？" : `例如：${EXAMPLE_QUERY}`}
          />
        </div>

        <details className="search-history" ref={historyEditor}>
          <summary>对话历史（可选）{conversationHistory.length ? ` · ${conversationHistory.length} 条` : ""}</summary>
          <ol className="search-history-turns">
            {conversationHistory.map((turn, index) => (
              <li key={turn.id}>
                <div className="search-history-turn-controls">
                  <label htmlFor={`history-role-${turn.id}`}>第 {index + 1} 条 · 角色</label>
                  <select id={`history-role-${turn.id}`} value={turn.role} onChange={(event) => {
                    clearSearchResult();
                    setConversationHistory((current) => current.map((item) => item.id === turn.id
                      ? { ...item, role: event.target.value as SearchHistoryTurn["role"] } : item));
                  }}>
                    <option value="user">用户</option>
                    <option value="assistant">助手</option>
                  </select>
                  <button className="text-button" type="button" aria-label={`移除第 ${index + 1} 条历史`} onClick={() => {
                    clearSearchResult();
                    setConversationHistory((current) => current.filter((item) => item.id !== turn.id));
                    addHistoryButton.current?.focus();
                  }}>移除</button>
                </div>
                <label className="search-history-text-label" htmlFor={`history-content-${turn.id}`}>原始文本</label>
                <textarea id={`history-content-${turn.id}`} rows={3} value={turn.content}
                  aria-label={`第 ${index + 1} 条${turn.role === "user" ? "用户" : "助手"}历史原文`}
                  onChange={(event) => {
                    clearSearchResult();
                    setConversationHistory((current) => current.map((item) => item.id === turn.id ? { ...item, content: event.target.value } : item));
                  }} />
              </li>
            ))}
          </ol>
          <div className="search-history-actions">
            <button className="button secondary" ref={addHistoryButton} type="button" disabled={conversationHistory.length >= 16} onClick={() => {
              clearSearchResult();
              const id = ++historySequence.current;
              historyFocusTarget.current = id;
              setConversationHistory((current) => [...current, { id, role: "user", content: "" }]);
            }}>添加一条历史</button>
          </div>
        </details>

        <div className="search-form-controls">
          <label className="intent-field">
            <span>检索意图</span>
            <select value={intent} onChange={(event) => { clearSearchResult(); setIntent(event.target.value as EvidenceSearchIntent); }}>
              <option value="claim_search">全部相关观点</option>
              <option value="supporting_evidence">优先支持证据</option>
              <option value="contradiction_search">优先相反证据</option>
            </select>
          </label>

          {hasPaperContext ? (
            <fieldset>
              <legend>检索范围</legend>
              <label><input type="radio" name="corpus" value="cross_paper" checked={corpus === "cross_paper"} onChange={() => { clearSearchResult(); setCorpus("cross_paper"); }} />全部论文</label>
              <label><input type="radio" name="corpus" value="same_paper" checked={corpus === "same_paper"} onChange={() => { clearSearchResult(); setCorpus("same_paper"); }} />仅当前论文</label>
            </fieldset>
          ) : null}

          <button className="button" type="submit" disabled={cannotSubmit || Boolean(clarification || needsContext)}><UiIcon name="search" />{aiBusy ? "分析中…" : busy ? "检索中…" : "检索证据"}</button>
        </div>

        {samePaperNeedsClaim ? <p className="search-form-help">请选择论文观点</p> : null}
      </form>

      {error ? <p className="search-error" role="alert">{error}</p> : null}

      {clarification ? (
        <section className="search-clarification" aria-labelledby="search-clarification-title">
          <h2 id="search-clarification-title" tabIndex={-1} ref={clarificationHeading}>请先确认检索含义</h2>
          <ScientificContent value={clarification.question} />
          <ol className="clarification-options">
            {clarification.options.map((option) => (
              <li key={option.optionId}>
                <strong><ScientificContent value={option.label} inline /></strong>
                <ScientificContent value={option.resolvedQuery} />
                <button className="button secondary" type="button" disabled={busy}
                  aria-label={`选择解释：${option.label}`}
                  onClick={() => { void submit(undefined, option.optionId); }}>
                  按此解释检索
                </button>
              </li>
            ))}
          </ol>
          <button className="text-button" type="button" onClick={() => { clearSearchResult(); queryInput.current?.focus(); }}>
            修改原问题
          </button>
        </section>
      ) : null}

      {needsContext ? (
        <section className="search-clarification search-context" aria-labelledby="search-context-title" aria-busy={busy}>
          <h2 id="search-context-title" tabIndex={-1} ref={contextHeading}>请补充检索所需信息</h2>
          <ScientificContent value={needsContext.question} />
          <form onSubmit={(event) => { void submit(event, undefined, contextInput); }}>
            <label htmlFor="search-context-input">你的补充</label>
            <textarea id="search-context-input" ref={contextTextarea} rows={3} required value={contextInput}
              disabled={busy} aria-invalid={Boolean(contextError)}
              aria-describedby={contextError ? "search-context-error" : undefined}
              onChange={(event) => { setContextInput(event.target.value); setContextError(""); }} />
            {contextError ? <p className="search-error" id="search-context-error" role="alert">{contextError}</p> : null}
            <div className="search-context-actions">
              <button className="button" type="submit" disabled={busy || contextTextIsBlank(contextInput)}>{busy ? "正在确认任务…" : "提交补充并继续"}</button>
              <button className="text-button" type="button" onClick={() => { clearSearchResult(); queryInput.current?.focus(); }}>修改原问题</button>
            </div>
          </form>
        </section>
      ) : null}

      {submittedSupplements.length ? (
        <details className="search-submitted-context">
          <summary>本次已提交的补充 · {submittedSupplements.length} 条</summary>
          <ol>{submittedSupplements.map((content, index) => <li key={index}><ScientificContent value={content} /></li>)}</ol>
        </details>
      ) : null}

      {resolvedTask ? (
        <details className="search-selected-interpretation">
          <summary>检索理解</summary>
          <ScientificContent value={resolvedTask} />
        </details>
      ) : null}

      {aiError ? <div className="ai-stage-error" role="alert">{aiError}</div> : null}
      {!searchFailed && (responseData || previewData || quickData) && !clarification && !needsContext ? (
        <div className="search-stage-layout">
          <section ref={previewSection} className="preview-results" aria-labelledby="preview-results-title">
            <header>
              <div><h3 id="preview-results-title">初步结果</h3></div>
              <span>{previewResults.length} 条</span>
            </header>
            {previewResults.length ? (
              <div>
                {(previewExpanded ? previewResults : previewResults.slice(0, 3)).map((member, index) => <PreviewResult member={member} rank={index + 1} onOpenSnapshot={onOpenSnapshot} key={`${member.snapshotId}:${member.claim.id}`} />)}
                <ClaimListDisclosure total={previewResults.length} visibleCount={3} expanded={previewExpanded} onToggle={() => setPreviewExpanded((current) => !current)} />
              </div>
            ) : !previewData ? <p className="stage-empty">暂无结果</p> : null}
          </section>

          <section className="ai-results" aria-labelledby="ai-results-title" aria-live="polite" aria-busy={aiBusy}>
            <header>
              <div>
                <h3 id="ai-results-title">{isTopicSearch ? "主题整理" : "关系分析"}</h3>
              </div>
              {responseData ? <span>{resultCount} 组</span> : null}
            </header>
            {aiBusy ? (
              <div className="ai-ranking-progress" role="status" aria-label="分析中">
                <div className="ai-progress-mark" aria-hidden="true"><span /><span /><span /></div>
              </div>
            ) : null}
            {!aiBusy && resultCount ? (
              <div className="cluster-results">
                {orderedRelationGroups.map((group) => {
                  const clusters = groupedClusters.get(group.id) ?? [];
                  if (!clusters.length) return null;
                  const expanded = expandedRelationGroups.has(group.id);
                  return (
                    <section className="relation-group" data-relation={group.id} key={group.id} aria-labelledby={`relation-${group.id}`}>
                      <header>
                        <div><h3 id={`relation-${group.id}`}>{group.title}</h3></div>
                        <span>{clusters.length} 组</span>
                      </header>
                      <div>
                        {(expanded ? clusters : clusters.slice(0, 3)).map((cluster) => <ClusterResult cluster={cluster} onOpenSnapshot={onOpenSnapshot} key={cluster.id} />)}
                        <ClaimListDisclosure total={clusters.length} visibleCount={3} expanded={expanded} onToggle={() => setExpandedRelationGroups((current) => {
                          const next = new Set(current);
                          if (next.has(group.id)) next.delete(group.id); else next.add(group.id);
                          return next;
                        })} />
                      </div>
                    </section>
                  );
                })}
              </div>
            ) : null}
            {!aiBusy && !resultCount && responseData?.resultStatus === "no_direct_answer" && !aiError ? <p className="stage-empty">暂无匹配结果</p> : null}
          </section>
        </div>
      ) : null}
      {previewData && !aiBusy && !resultCount && !aiError && !clarification && !needsContext && !previewResults.length && !searchFailed ? (
        <div className="search-empty">
          <strong>暂未检索到相关证据</strong>
        </div>
      ) : null}
    </section>
  );
}
