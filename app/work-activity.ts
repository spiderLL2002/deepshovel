import type { Activity } from "./workspace-types.ts";

/** Server-owned background work as returned by GET /api/work. */
export type WorkItemDto = {
  id: string;
  kind: "retrieval_index" | "claim_relations";
  subjectId: string;
  state: "pending" | "running" | "succeeded" | "failed" | "cancelled";
  attempts: number;
  maxAttempts: number;
  nextAttemptAt: string | null;
  progress: { total: number | null; persisted: number | null };
  error: { code: string; message: string } | null;
  fileName: string | null;
  artifactId: string | null;
  artifactVersionId: string | null;
  updatedAt: string;
  completedAt: string | null;
};

const WORK_ERROR_LABELS: Record<string, string> = {
  CLAIM_RELATION_PROCESSING_INCOMPLETE: "部分 Claim 未能完成关系分析，将自动重试",
  RETRIEVAL_INDEX_UNAVAILABLE: "论文的检索索引尚未建立，关系分析稍后自动继续",
  RETRIEVAL_UNAVAILABLE: "检索服务暂时不可用，将自动重试",
  RETRIEVAL_NOT_CONFIGURED: "检索服务未配置",
};

export function isOpenWork(item: Pick<WorkItemDto, "state">) {
  return item.state === "pending" || item.state === "running";
}

/** Present one Claim-relation work item as a read-only task row. */
export function claimAnalysisActivity(item: WorkItemDto): Activity {
  const total = item.progress.total;
  const persisted = item.progress.persisted ?? 0;
  const base = {
    id: `claim-analysis:${item.subjectId}`,
    label: `${item.fileName ?? "论文"} · 关系分析`,
    kind: "claim-analysis" as const,
    ...(item.artifactId ? { artifactId: item.artifactId } : {}),
    ...(item.artifactVersionId ? { artifactVersionId: item.artifactVersionId } : {}),
    sourceUpdatedAt: item.updatedAt,
    cancellable: false,
  };
  if (isOpenWork(item)) {
    const retrying = item.attempts > 0 && item.error;
    return {
      ...base,
      state: "working",
      detail: retrying
        ? `${WORK_ERROR_LABELS[item.error!.code] ?? "Claim 关系分析将自动重试"}（第 ${item.attempts + 1}/${item.maxAttempts} 次）`
        : total
          ? `Claim 关系分析 ${Math.min(persisted, total)}/${total}`
          : "等待分析本篇论文的 Claim 关系",
      progress: total ? Math.round((Math.min(persisted, total) / total) * 100) : 0,
    };
  }
  if (item.state === "succeeded") {
    return {
      ...base,
      state: "done",
      detail: persisted ? `本篇 Claim 关系分析完成 · ${persisted} 条` : "本篇论文的 Claim 关系已全部分析",
      progress: 100,
    };
  }
  if (item.state === "cancelled") {
    return { ...base, state: "cancelled", detail: "关系分析已停止", progress: 100 };
  }
  return {
    ...base,
    state: "failed",
    detail: (item.error && WORK_ERROR_LABELS[item.error.code]) ?? "Claim 关系分析失败，可在证据图谱中重新处理",
    progress: 100,
  };
}
