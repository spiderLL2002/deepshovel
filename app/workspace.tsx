"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent as ReactDragEvent } from "react";
import { ARTIFACT_ACCEPT } from "@/lib/artifacts/media-types";
import type { AtomicClaim } from "@/lib/evidence-graph";
import {
  activeActivityCount,
  activityTaskButtonLabel,
  patchActivity as patchActivityState,
  reconcileCompileActivities,
  upsertActivity,
} from "./activity-state.ts";
import { compileActivityStatus, CompileSubmissionQueue, MAX_COMPILE_RUN_ATTEMPTS, nextCompileRunAttempt, submitCompileJob } from "./compile-polling.ts";
import { claimAnalysisActivity, isOpenWork, type WorkItemDto } from "./work-activity.ts";
import { streamPendingCompile } from "./pending-compile-stream.ts";
import { fetchStreamed } from "./streamed-fetch.ts";
import { ActivityView, type ActivityFilter } from "./components/activity-view";
import { EvidenceKnowledgeGraph, type OpenAlexSearchScope } from "./components/evidence-knowledge-graph";
import { EvidenceSearch, type EvidenceSearchClaimRef } from "./components/evidence-search";
import { EvidenceSynthesis } from "./components/evidence-synthesis";
import { GeochemicalAtlas } from "./components/geochemical-atlas";
import { LibraryView, hasCompilableLibraryVersion, type LibraryStatus } from "./components/library-view";
import { PaperVisualLibrary } from "./components/paper-visual-library";
import { PaperDetail } from "./components/paper-detail";
import { ReviewWorkspace, type ReviewFilter } from "./components/review-workspace";
import { EvidenceMark, UiIcon, type UiIconName } from "./components/ui-icon";
import { consumeUploadSelection, uploadArtifact, UploadScheduler } from "./upload-scheduler.ts";
import type {
  Activity,
  ArtifactVersionDto,
  CitationReceptionDto,
  CompileJobDto,
  LibraryArtifactDto,
  SnapshotDetailDto,
  SnapshotSummaryDto,
} from "./workspace-types";
import { compileErrorMessage, isUnfilteredCompletedLibrary, readJson, responseError, shortHash } from "./workspace-types";

type WorkspaceView = "search" | "verification" | "library" | "knowledgeGraph" | "visualLibrary" | "geochemicalAtlas" | "paper" | "review" | "activity";
type PaperReturnView = "search" | "verification" | "library" | "knowledgeGraph" | "visualLibrary" | "review";
type ReviewDecision = "accepted" | "rejected" | "needs_revision" | "withdrawn";
type NavigationView = Exclude<WorkspaceView, "paper">;
type NavigationItem = {
  view: NavigationView;
  description: string;
  icon: UiIconName;
  label: string;
};
type CompileTarget = Pick<ArtifactVersionDto, "id" | "fileName"> & { artifactId?: string };

function rememberArtifactIdentity(cache: Map<string, string>, versionId: string, artifactId: string) {
  cache.delete(versionId);
  cache.set(versionId, artifactId);
  while (cache.size > 2000) cache.delete(cache.keys().next().value!);
}

const PRIMARY_NAVIGATION = [
  { view: "search", description: "输入观点并核对论文证据", icon: "search", label: "观点校验" },
  { view: "verification", description: "核对整篇论文的推理与数据", icon: "document", label: "论文校验" },
  { view: "library", description: "上传与版本管理", icon: "library", label: "文献库" },
  { view: "knowledgeGraph", description: "浏览主题与 Claim 关系", icon: "graph", label: "证据图谱" },
  { view: "visualLibrary", description: "查看论文的元数据、正文与图表", icon: "visuals", label: "数据图表库" },
  { view: "geochemicalAtlas", description: "浏览全球地球化学元素分布", icon: "atlas", label: "元素图谱" },
] as const satisfies readonly NavigationItem[];

const QUALITY_NAVIGATION = [
  { view: "review", description: "复核证据并记录结论", icon: "review", label: "人工复核" },
  { view: "activity", description: "查看上传与 Compile 进度", icon: "activity", label: "当前任务" },
] as const satisfies readonly NavigationItem[];

const PAPER_RETURN_LABELS = {
  knowledgeGraph: "证据图谱",
  library: "文献库",
  search: "观点校验",
  verification: "论文校验",
  visualLibrary: "数据图表库",
  review: "人工复核",
} as const satisfies Record<PaperReturnView, string>;

const MAX_PARALLEL_UPLOADS = 2;
const COMPILE_ATTEMPT_STORAGE_KEY = "deepshovel.compile-attempts.v1";

function compileDetail(job: CompileJobDto) {
  if (job.error?.code === "COMPILE_CANCELLED" || job.error?.code === "JOB_CANCELLED") {
    return "任务已终止，原始文件仍保存在文献库";
  }
  const labels: Record<CompileJobDto["stage"], string> = {
    queued: "已进入 Compile 队列",
    parsing: "正在解析文档版面与表格",
    extracting: "正在提取论文观点与证据",
    validating: "正在核验提取结果",
    importing: "正在写入证据库",
    completed: "Compile 已完成，证据现已可供检索",
    failed: compileErrorMessage(job.error?.code, job.error?.message ?? "Compile 失败", job.timings),
  };
  return `${labels[job.stage]} · ${Math.round(job.progress)}%`;
}

function WorkspaceNavButton({
  active,
  ariaLabel,
  description,
  icon,
  label,
  onClick,
}: {
  active: boolean;
  ariaLabel?: string;
  description: string;
  icon: UiIconName;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      className="workspace-nav-item"
      aria-current={active ? "page" : undefined}
      aria-label={ariaLabel ?? `${label}：${description}`}
      title={label}
      onClick={(event) => {
        onClick();
        if (event.detail > 0) event.currentTarget.blur();
      }}
      type="button"
    >
      <UiIcon name={icon} />
      <span className="workspace-nav-copy"><strong>{label}</strong></span>
    </button>
  );
}

export function EvidenceWorkspace() {
  const [activeView, setActiveView] = useState<WorkspaceView>("search");
  const [atlasStarted, setAtlasStarted] = useState(false);
  const [knowledgeGraphStarted, setKnowledgeGraphStarted] = useState(false);
  const [verificationStarted, setVerificationStarted] = useState(false);
  const activeViewRef = useRef<WorkspaceView>("search");
  const [paperReturnView, setPaperReturnView] = useState<PaperReturnView>("library");
  const [libraryArtifacts, setLibraryArtifacts] = useState<LibraryArtifactDto[]>([]);
  const [completedLibraryArtifacts, setCompletedLibraryArtifacts] = useState<LibraryArtifactDto[]>([]);
  const [pendingTaskArtifacts, setPendingTaskArtifacts] = useState<LibraryArtifactDto[]>([]);
  const [libraryQuery, setLibraryQuery] = useState("");
  const [libraryLoading, setLibraryLoading] = useState(true);
  const [pendingTaskLoading, setPendingTaskLoading] = useState(true);
  const [libraryPage, setLibraryPage] = useState({ page: 1, pageSize: 20, total: null as number | null, totalPages: null as number | null, hasMore: false, nextCursor: null as string | null });
  const [snapshots, setSnapshots] = useState<SnapshotSummaryDto[]>([]);
  const [reviewQuery, setReviewQuery] = useState("");
  const [reviewFilter, setReviewFilter] = useState<ReviewFilter>("all");
  const [reviewPage, setReviewPage] = useState({ page: 1, pageSize: 20, total: null as number | null, totalPages: null as number | null, hasMore: false });
  const [reviewListLoading, setReviewListLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [paperClaimId, setPaperClaimId] = useState<string | null>(null);
  const [detail, setDetail] = useState<SnapshotDetailDto | null>(null);
  const [citationReception, setCitationReception] = useState<CitationReceptionDto | null>(null);
  const [citationReceptionLoading, setCitationReceptionLoading] = useState(false);
  const [citationReceptionError, setCitationReceptionError] = useState("");
  const [citationReceptionAnalyzing, setCitationReceptionAnalyzing] = useState(false);
  const [citationReceptionActionError, setCitationReceptionActionError] = useState("");
  const [searchClaim, setSearchClaim] = useState<EvidenceSearchClaimRef | null>(null);
  const [openAlexSearchScope, setOpenAlexSearchScope] = useState<OpenAlexSearchScope | null>(null);
  const [activity, setActivity] = useState<Activity[]>([]);
  const [compileAllProgress, setCompileAllProgress] = useState<number | null>(null);
  const compileAllController = useRef<AbortController | null>(null);
  const [activityFilter, setActivityFilter] = useState<ActivityFilter>("processing");
  const activityFilterRef = useRef<ActivityFilter>("processing");
  const [uploadDragActive, setUploadDragActive] = useState(false);
  const [error, setError] = useState("");
  const [compilePollError, setCompilePollError] = useState("");
  const [busy, setBusy] = useState("");
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [compileJobs, setCompileJobs] = useState<Record<string, CompileJobDto>>({});
  const artifactInput = useRef<HTMLInputElement>(null);
  const targetArtifact = useRef<string | null>(null);
  const compileJobsRef = useRef<Record<string, CompileJobDto>>({});
  const compileActivityIds = useRef(new Map<string, string>());
  const compileJobIdsByActivity = useRef(new Map<string, string>());
  const artifactIdsByVersion = useRef(new Map<string, string>());
  const uploadedPendingArtifacts = useRef(new Map<string, LibraryArtifactDto>());
  const uploadControllers = useRef(new Map<string, AbortController>());
  const [uploadScheduler] = useState(() => new UploadScheduler(MAX_PARALLEL_UPLOADS));
  const uploadDragDepth = useRef(0);
  const libraryQueryRef = useRef("");
  const libraryPageRef = useRef(1);
  const libraryPageSizeRef = useRef(20);
  const libraryGeneration = useRef(0);
  const pendingTaskGeneration = useRef(0);
  const pendingRequest = useRef({ page: 1, pageSize: 20, query: "" });
  const [pendingPage, setPendingPage] = useState({ page: 1, pageSize: 20, total: null as number | null, hasMore: false, query: "" });
  const libraryCursors = useRef(new Map<number, string>([[1, ""]]));
  const pendingCursors = useRef(new Map<number, string>([[1, ""]]));
  const readControllers = useRef(new Map<string, AbortController>());
  const beginRead = useCallback((key: string) => {
    readControllers.current.get(key)?.abort();
    const controller = new AbortController();
    readControllers.current.set(key, controller);
    return controller;
  }, []);
  const snapshotGeneration = useRef(0);
  const reviewCursors = useRef(new Map<number, string>());
  const reviewQueryRef = useRef("");
  const reviewFilterRef = useRef<ReviewFilter>("all");
  const reviewPageRef = useRef(1);
  const reviewPageSizeRef = useRef(20);
  const compileJobsGeneration = useRef(0);
  const detailGeneration = useRef(0);
  const citationReceptionGeneration = useRef(0);
  const citationReceptionController = useRef<AbortController | null>(null);
  const citationReceptionActionController = useRef<AbortController | null>(null);
  const selectedIdRef = useRef<string | null>(null);
  const submittingVersions = useRef(new Set<string>());
  const compileSubmissionQueue = useRef(new CompileSubmissionQueue());
  const compileRunAttempts = useRef(new Map<string, number>());
  const automaticallyRetriedJobs = useRef(new Set<string>());
  const [openWorkCount, setOpenWorkCount] = useState(0);
  const workGeneration = useRef(0);
  const compileArtifactsRef = useRef<(versions: CompileTarget[], automaticAttempt?: number) => Promise<void>>(async () => undefined);
  const workspaceMounted = useRef(true);
  const synthesisArtifacts = useMemo(() => {
    const merged = new Map(completedLibraryArtifacts.map((item) => [item.artifactId, item]));
    for (const item of libraryArtifacts) merged.set(item.artifactId, item);
    return [...merged.values()];
  }, [completedLibraryArtifacts, libraryArtifacts]);
  useEffect(() => {
    workspaceMounted.current = true;
    const submissionQueue = compileSubmissionQueue.current;
    const controllers = uploadControllers.current;
    const reads = readControllers.current;
    return () => {
      workspaceMounted.current = false;
      compileAllController.current?.abort();
      libraryGeneration.current += 1;
      pendingTaskGeneration.current += 1;
      snapshotGeneration.current += 1;
      compileJobsGeneration.current += 1;
      detailGeneration.current += 1;
      citationReceptionGeneration.current += 1;
      for (const controller of reads.values()) controller.abort();
      citationReceptionController.current?.abort();
      citationReceptionActionController.current?.abort();
      submissionQueue.cancelPending();
      for (const controller of controllers.values()) controller.abort();
    };
  }, []);

  const patchActivity = useCallback((id: string, patch: Partial<Activity>) => {
    if (!workspaceMounted.current) return;
    setActivity((items) => patchActivityState(items, id, patch));
  }, []);

  const addActivity = useCallback((item: Activity) => {
    if (!workspaceMounted.current) return;
    setActivity((items) => upsertActivity(items, item));
  }, []);

  useEffect(() => {
    try {
      const stored = JSON.parse(window.localStorage.getItem(COMPILE_ATTEMPT_STORAGE_KEY) ?? "{}") as Record<string, unknown>;
      for (const [versionId, attempt] of Object.entries(stored)) {
        if (Number.isInteger(attempt) && Number(attempt) >= 1 && Number(attempt) <= MAX_COMPILE_RUN_ATTEMPTS) {
          compileRunAttempts.current.set(versionId, Number(attempt));
        }
      }
    } catch {
      window.localStorage.removeItem(COMPILE_ATTEMPT_STORAGE_KEY);
    }
  }, []);

  const rememberCompileAttempt = useCallback((versionId: string, attempt: number) => {
    compileRunAttempts.current.set(versionId, attempt);
    try {
      window.localStorage.setItem(COMPILE_ATTEMPT_STORAGE_KEY, JSON.stringify(Object.fromEntries(compileRunAttempts.current)));
    } catch {
      // Retry still works for the active session when browser storage is unavailable.
    }
  }, []);

  /** Background Claim-relation analysis is server work; the tab only shows it. */
  const loadWorkActivities = useCallback(async () => {
    if (!workspaceMounted.current) return;
    const generation = workGeneration.current + 1;
    workGeneration.current = generation;
    const response = await fetch("/api/work?kind=claim_relations", { cache: "no-store" });
    const payload = await readJson(response);
    if (!workspaceMounted.current || generation !== workGeneration.current) return;
    if (!response.ok) throw new Error(responseError(payload, "后台任务读取失败，请稍后重试"));
    const { open, finished } = payload as { open: WorkItemDto[]; finished: WorkItemDto[] };
    const rows = [...finished, ...open].map(claimAnalysisActivity);
    setActivity((items) => rows.reduce((current, row) => upsertActivity(current, row), items));
    setOpenWorkCount(open.filter(isOpenWork).length);
  }, []);

  const loadLibrary = useCallback(async ({ query, status, page = libraryPageRef.current, pageSize = libraryPageSizeRef.current }: { query: string; status: LibraryStatus; page?: number; pageSize?: number }) => {
    if (!workspaceMounted.current) return;
    const generation = libraryGeneration.current + 1;
    libraryGeneration.current = generation;
    const controller = beginRead("library");
    if (page === 1) libraryCursors.current.clear();
    setLibraryLoading(true);
    try {
      const parameters = new URLSearchParams({ limit: String(pageSize), status });
      const cursor = libraryCursors.current.get(page);
      if (cursor) parameters.set("cursor", cursor);
      if (query) parameters.set("query", query);
      const response = await fetch(`/api/library?${parameters}`, { cache: "no-store", signal: controller.signal });
      const payload = await readJson(response);
      if (!workspaceMounted.current || generation !== libraryGeneration.current) return;
      if (!response.ok) throw new Error(responseError(payload, "文献库读取失败，请稍后重试"));
      const { artifacts: next, pageInfo } = payload as {
        artifacts: LibraryArtifactDto[];
        pageInfo: { page: number; pageSize: number; total: number | null; totalPages: number | null; hasMore: boolean; nextCursor: string | null };
      };
      for (const item of next) {
        if (item.latestVersion) rememberArtifactIdentity(artifactIdsByVersion.current, item.latestVersion.id, item.artifactId);
      }
      if (pageInfo.nextCursor) libraryCursors.current.set(page + 1, pageInfo.nextCursor);
      libraryPageRef.current = page;
      libraryPageSizeRef.current = pageInfo.pageSize;
      setLibraryArtifacts(next);
      setLibraryPage({ ...pageInfo, page });
      if (isUnfilteredCompletedLibrary(status, query)) {
        setCompletedLibraryArtifacts(next);
      }
    } catch (cause) {
      if (!controller.signal.aborted) throw cause;
    } finally {
      if (workspaceMounted.current && generation === libraryGeneration.current) setLibraryLoading(false);
    }
  }, [beginRead]);

  const loadCompletedLibrary = useCallback(async () => {
    if (!workspaceMounted.current) return;
    const controller = beginRead("completed");
    try {
      const response = await fetch("/api/library?limit=20&status=completed", { cache: "no-store", signal: controller.signal });
      const payload = await readJson(response);
      if (controller.signal.aborted || !workspaceMounted.current) return;
      if (!response.ok) throw new Error(responseError(payload, "已完成文献读取失败，请稍后重试"));
      const next = (payload as { artifacts: LibraryArtifactDto[] }).artifacts;
      for (const item of next) {
        if (item.latestVersion) rememberArtifactIdentity(artifactIdsByVersion.current, item.latestVersion.id, item.artifactId);
      }
      setCompletedLibraryArtifacts(next);
    } catch (cause) {
      if (!controller.signal.aborted) throw cause;
    }
  }, [beginRead]);

  const loadTaskPendingArtifacts = useCallback(async (request = pendingRequest.current) => {
    if (!workspaceMounted.current) return;
    pendingRequest.current = request;
    const { page, pageSize, query } = request;
    if (page === 1) pendingCursors.current.clear();
    const generation = ++pendingTaskGeneration.current;
    const controller = beginRead("pending");
    setPendingTaskLoading(true);
    try {
      const parameters = new URLSearchParams({ limit: String(pageSize), status: "pending", query });
      const cursor = pendingCursors.current.get(page);
      if (cursor) parameters.set("cursor", cursor);
      const response = await fetch(`/api/library?${parameters}`, { cache: "no-store", signal: controller.signal });
      const payload = await readJson(response);
      if (!workspaceMounted.current || generation !== pendingTaskGeneration.current) return;
      if (!response.ok) throw new Error(responseError(payload, "待 Compile 文献读取失败，请稍后重试"));
      const result = payload as { artifacts: LibraryArtifactDto[]; pageInfo: { total: number | null; hasMore: boolean; nextCursor: string | null } };
      if (result.pageInfo.nextCursor) pendingCursors.current.set(page + 1, result.pageInfo.nextCursor);
      for (const item of result.artifacts) {
        if (item.latestVersion) rememberArtifactIdentity(artifactIdsByVersion.current, item.latestVersion.id, item.artifactId);
      }
      setPendingTaskArtifacts(result.artifacts);
      setPendingPage({ page, pageSize, total: result.pageInfo.total, hasMore: result.pageInfo.hasMore, query });
    } catch (cause) {
      if (!controller.signal.aborted) throw cause;
    } finally {
      if (workspaceMounted.current && generation === pendingTaskGeneration.current) setPendingTaskLoading(false);
    }
  }, [beginRead]);

  const loadSnapshots = useCallback(async ({ query = reviewQueryRef.current, status = reviewFilterRef.current, page = reviewPageRef.current, pageSize = reviewPageSizeRef.current }: { query?: string; status?: ReviewFilter; page?: number; pageSize?: number } = {}) => {
    if (!workspaceMounted.current) return [];
    const generation = snapshotGeneration.current + 1;
    snapshotGeneration.current = generation;
    const controller = beginRead("snapshots");
    setReviewListLoading(true);
    try {
      if (page === 1) reviewCursors.current.clear();
      const parameters = new URLSearchParams({ query, status, limit: String(pageSize) });
      const cursor = reviewCursors.current.get(page);
      if (cursor) parameters.set("cursor", cursor);
      const response = await fetch(`/api/evidence-graphs?${parameters}`, { cache: "no-store", signal: controller.signal });
      const payload = await readJson(response);
      if (!workspaceMounted.current || generation !== snapshotGeneration.current) return [];
      if (!response.ok) throw new Error(responseError(payload, "论文证据列表读取失败，请稍后重试"));
      const { snapshots: next, pageInfo } = payload as {
        snapshots: SnapshotSummaryDto[];
        pageInfo: { page: number; pageSize: number; total: number | null; totalPages: number | null; hasMore: boolean; nextCursor: string | null };
      };
      if (pageInfo.nextCursor) reviewCursors.current.set(page + 1, pageInfo.nextCursor);
      reviewPageRef.current = page;
      reviewPageSizeRef.current = pageInfo.pageSize;
      setSnapshots(next);
      setReviewPage({ ...pageInfo, page });
      return next;
    } catch (cause) {
      if (!controller.signal.aborted) throw cause;
      return [];
    } finally {
      if (workspaceMounted.current && generation === snapshotGeneration.current) setReviewListLoading(false);
    }
  }, [beginRead]);

  const refreshData = useCallback(async (view: WorkspaceView = activeViewRef.current) => {
    if (view === "library") {
      await loadLibrary({ query: libraryQueryRef.current, status: "all" });
    } else if (view === "verification") {
      await loadCompletedLibrary();
    } else if (view === "activity" && activityFilterRef.current === "pending") {
      await loadTaskPendingArtifacts();
    } else if (view === "review") {
      await loadSnapshots();
    }
  }, [loadCompletedLibrary, loadLibrary, loadSnapshots, loadTaskPendingArtifacts]);

  const loadDetail = useCallback(async (snapshotId: string) => {
    if (!workspaceMounted.current) return;
    const generation = detailGeneration.current + 1;
    detailGeneration.current = generation;
    const controller = beginRead("detail");
    setLoadingDetail(true);
    try {
      const response = await fetch(`/api/evidence-graphs/${encodeURIComponent(snapshotId)}`, { cache: "no-store", signal: controller.signal });
      const payload = await readJson(response);
      if (!workspaceMounted.current || generation !== detailGeneration.current) return;
      if (!response.ok) throw new Error(responseError(payload, "论文证据读取失败，请稍后重试"));
      setDetail(payload as SnapshotDetailDto);
    } catch (cause) {
      if (!workspaceMounted.current || controller.signal.aborted || generation !== detailGeneration.current) return;
      setDetail(null);
      setError(cause instanceof Error ? cause.message : "论文证据读取失败，请稍后重试");
    } finally {
      if (workspaceMounted.current && generation === detailGeneration.current) setLoadingDetail(false);
    }
  }, [beginRead]);

  const loadCitationReception = useCallback(async (
    snapshotId: string,
    { preserve = false }: { preserve?: boolean } = {},
  ) => {
    if (!workspaceMounted.current) return;
    const generation = citationReceptionGeneration.current + 1;
    citationReceptionGeneration.current = generation;
    citationReceptionController.current?.abort();
    const controller = new AbortController();
    citationReceptionController.current = controller;
    if (!preserve) setCitationReception(null);
    setCitationReceptionLoading(true);
    setCitationReceptionError("");
    try {
      const parameters = new URLSearchParams({ snapshotId });
      const response = await fetch(`/api/citation-reception?${parameters}`, {
        cache: "no-store",
        signal: controller.signal,
      });
      const payload = await readJson(response);
      if (controller.signal.aborted
        || !workspaceMounted.current
        || generation !== citationReceptionGeneration.current) return;
      if (!response.ok) throw new Error(responseError(payload, "后续引用证据读取失败，请稍后重试"));
      const reception = payload as CitationReceptionDto;
      if (reception.targetSnapshotId !== snapshotId) {
        throw new Error("后续引用证据未能正确载入，请重新读取");
      }
      setCitationReception(reception);
    } catch (cause) {
      if (controller.signal.aborted
        || !workspaceMounted.current
        || generation !== citationReceptionGeneration.current) return;
      if (!preserve) setCitationReception(null);
      setCitationReceptionError(cause instanceof Error ? cause.message : "后续引用证据读取失败，请稍后重试");
    } finally {
      if (workspaceMounted.current && generation === citationReceptionGeneration.current) {
        setCitationReceptionLoading(false);
        if (citationReceptionController.current === controller) citationReceptionController.current = null;
      }
    }
  }, []);

  const analyzeCitationReception = useCallback(async (snapshotId: string, reprocess = false) => {
    if (!workspaceMounted.current || citationReceptionActionController.current) return;
    const controller = new AbortController();
    citationReceptionActionController.current = controller;
    setCitationReceptionAnalyzing(true);
    setCitationReceptionActionError("");
    try {
      const response = await fetchStreamed("/api/citation-reception", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ snapshotId, ...(reprocess ? { reprocess: true } : {}) }),
        signal: controller.signal,
      });
      const payload = await readJson(response);
      if (controller.signal.aborted || !workspaceMounted.current) return;
      if (!response.ok) throw new Error(responseError(payload, "引用分析启动失败，请稍后重试"));
      if (selectedIdRef.current === snapshotId) await loadCitationReception(snapshotId, { preserve: true });
    } catch (cause) {
      if (controller.signal.aborted || !workspaceMounted.current) return;
      setCitationReceptionActionError(cause instanceof Error ? cause.message : "引用分析启动失败，请稍后重试");
    } finally {
      if (citationReceptionActionController.current === controller) {
        citationReceptionActionController.current = null;
        if (workspaceMounted.current) setCitationReceptionAnalyzing(false);
      }
    }
  }, [loadCitationReception]);

  const loadCompileJobs = useCallback(async () => {
    if (!workspaceMounted.current) return;
    const generation = compileJobsGeneration.current + 1;
    compileJobsGeneration.current = generation;
    // Read-only: the server-side work pump advances and imports every job.
    const response = await fetch("/api/compile", { cache: "no-store" });
    const payload = await readJson(response);
    if (!workspaceMounted.current || generation !== compileJobsGeneration.current) return;
    if (!response.ok) throw new Error(responseError(payload, "Compile 任务读取失败，请稍后重试"));
    const { jobs } = payload as { jobs: CompileJobDto[] };
    const latest = new Map<string, CompileJobDto>();
    for (const job of jobs) if (!latest.has(job.artifactVersionId)) latest.set(job.artifactVersionId, job);
    const next = Object.fromEntries(latest);
    const previous = compileJobsRef.current;
    let shouldRefresh = false;
    const nextActivities: Activity[] = [];
    for (const job of latest.values()) {
      const previousJob = previous[job.artifactVersionId];
      if (previousJob && previousJob.status !== job.status && (job.status === "completed" || job.status === "failed")) shouldRefresh = true;
      let activityId = compileActivityIds.current.get(job.id);
      const isActive = job.status === "queued" || job.status === "running";
      if (!activityId && (isActive || job.status === "failed")) {
        activityId = `resume:${job.id}`;
        compileActivityIds.current.set(job.id, activityId);
      }
      if (activityId) {
        nextActivities.push({
          id: activityId,
          label: job.fileName ?? "未命名文献",
          ...compileActivityStatus(job),
          detail: compileDetail(job),
          kind: "compile",
          artifactId: artifactIdsByVersion.current.get(job.artifactVersionId),
          artifactVersionId: job.artifactVersionId,
          jobId: job.id,
          sourceUpdatedAt: job.updatedAt,
        });
      }
    }
    // Retry bookkeeping follows the bounded service job window, not every paper ever visited.
    for (const versionId of compileRunAttempts.current.keys()) {
      if (!latest.has(versionId) && !submittingVersions.current.has(versionId)) compileRunAttempts.current.delete(versionId);
    }
    const listedJobIds = new Set(jobs.map((job) => job.id));
    for (const jobId of automaticallyRetriedJobs.current) {
      if (!listedJobIds.has(jobId)) automaticallyRetriedJobs.current.delete(jobId);
    }
    setActivity((items) => reconcileCompileActivities(items, nextActivities, listedJobIds));
    for (const jobId of compileActivityIds.current.keys()) {
      if (!listedJobIds.has(jobId)) compileActivityIds.current.delete(jobId);
    }
    for (const [activityId, jobId] of compileJobIdsByActivity.current) {
      if (!listedJobIds.has(jobId)) compileJobIdsByActivity.current.delete(activityId);
    }
    compileJobsRef.current = next;
    setCompileJobs(next);
    if (shouldRefresh) await refreshData();
  }, [refreshData]);

  const cancelCompileTasks = useCallback(async (jobIds: string[] | null) => {
    const response = await fetch("/api/compile", {
      method: "DELETE",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(jobIds === null ? { all: true } : { job_ids: jobIds }),
    });
    const payload = await readJson(response);
    if (!response.ok) throw new Error(responseError(payload, "未能终止任务，请稍后重试"));
    await Promise.all([loadCompileJobs(), refreshData()]);
  }, [loadCompileJobs, refreshData]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      void Promise.all([loadCompileJobs(), loadWorkActivities()]).catch((cause) => {
        if (workspaceMounted.current) setError(cause instanceof Error ? cause.message : "页面数据加载失败，请稍后重试");
      });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [loadCompileJobs, loadWorkActivities]);

  useEffect(() => {
    activeViewRef.current = activeView;
    const reads = readControllers.current;
    const frame = window.requestAnimationFrame(() => {
      void refreshData(activeView).catch((cause) => {
        if (workspaceMounted.current) setError(cause instanceof Error ? cause.message : "页面数据加载失败，请稍后重试");
      });
    });
    return () => {
      window.cancelAnimationFrame(frame);
      for (const key of ["library", "completed", "pending", "snapshots", "detail"]) reads.get(key)?.abort();
    };
  }, [activeView, refreshData]);

  const hasActiveCompile = Object.values(compileJobs).some((job) => job.status === "queued" || job.status === "running");
  const hasActiveWork = hasActiveCompile || openWorkCount > 0;
  useEffect(() => {
    if (!hasActiveWork) return;
    let cancelled = false;
    let timer = 0;
    const poll = async () => {
      try {
        await Promise.all([loadCompileJobs(), loadWorkActivities()]);
        if (workspaceMounted.current) setCompilePollError("");
      } catch (cause) {
        if (workspaceMounted.current) setCompilePollError(cause instanceof Error ? cause.message : "Compile 状态读取失败，请稍后重试");
      } finally {
        // Observing only; a hidden tab polls less often and never drives work.
        const delay = document.visibilityState === "hidden" ? 15_000 : 2_000;
        if (!cancelled) timer = window.setTimeout(() => void poll(), delay);
      }
    };
    timer = window.setTimeout(() => void poll(), 2_000);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [hasActiveWork, loadCompileJobs, loadWorkActivities]);

  useEffect(() => {
    if (!selectedId || (activeView !== "paper" && activeView !== "review")) return;
    const frame = window.requestAnimationFrame(() => void loadDetail(selectedId));
    return () => window.cancelAnimationFrame(frame);
  }, [activeView, loadDetail, selectedId]);

  useEffect(() => {
    if (!selectedId || activeView !== "paper") return;
    const frame = window.requestAnimationFrame(() => void loadCitationReception(selectedId));
    return () => {
      window.cancelAnimationFrame(frame);
      citationReceptionController.current?.abort();
    };
  }, [activeView, loadCitationReception, selectedId]);

  useEffect(() => {
    if (!selectedId || activeView !== "paper" || citationReception?.status !== "processing") return;
    const timer = window.setTimeout(
      () => void loadCitationReception(selectedId, { preserve: true }),
      2_000,
    );
    return () => window.clearTimeout(timer);
  }, [activeView, citationReception, loadCitationReception, selectedId]);

  const beginArtifactUpload = (artifactId: string | null) => {
    targetArtifact.current = artifactId;
    artifactInput.current?.click();
  };

  const uploadArtifacts = async (files: FileList | File[] | null, explicitArtifactId?: string | null) => {
    if (!files?.length) return;
    setError("");
    const artifactId = explicitArtifactId === undefined ? targetArtifact.current : explicitArtifactId;
    targetArtifact.current = null;
    const uploads = consumeUploadSelection(files, () => {
      if (artifactInput.current) artifactInput.current.value = "";
    });
    if (activeView === "library") {
      activityFilterRef.current = "pending";
      setActivityFilter("pending");
      setActiveView("activity");
    }
    const uploadOne = async (file: File) => {
      const id = crypto.randomUUID();
      const controller = new AbortController();
      uploadControllers.current.set(id, controller);
      addActivity({ id, label: file.name, state: "working", detail: "等待上传", kind: "upload", artifactId: artifactId ?? undefined, cancellable: true });
      try {
        const receipt = await uploadScheduler.run(controller.signal, async () => {
          patchActivity(id, { detail: artifactId ? "正在上传新版本" : "正在上传文件" });
          return uploadArtifact(file, controller.signal, { artifactId: artifactId ?? undefined });
        }, artifactId ?? undefined);
        rememberArtifactIdentity(artifactIdsByVersion.current, receipt.id, receipt.artifactId);
        patchActivity(id, { artifactId: receipt.artifactId, artifactVersionId: receipt.id, state: "done", detail: "上传完成", cancellable: false });
        // Publish each receipt immediately, independently of the rest of the
        // upload batch and any Compile submissions/results still in progress.
        if (!compileJobsRef.current[receipt.id] && !submittingVersions.current.has(receipt.id)) {
          const pending: LibraryArtifactDto = {
            artifactId: receipt.artifactId, title: receipt.fileName, artifactType: "paper",
            libraryStatus: "pending", versionCount: receipt.versionNumber, latestVersion: receipt,
            compile: null, snapshot: null, humanReview: null, updatedAt: receipt.createdAt,
            machineVerification: { status: "unassessed", runCount: 0, limitationCount: 0 },
          };
          uploadedPendingArtifacts.current.set(receipt.artifactId, pending);
          setPendingTaskArtifacts((current) => [pending, ...current.filter((item) => item.artifactId !== receipt.artifactId)]);
        }
      } catch (cause) {
        if (controller.signal.aborted) {
          patchActivity(id, { state: "cancelled", detail: "上传已终止", cancellable: false });
        } else {
          patchActivity(id, { state: "failed", detail: cause instanceof Error ? cause.message : "文献上传失败，请稍后重试", cancellable: false });
        }
      } finally {
        uploadControllers.current.delete(id);
      }
    };
    await Promise.all(uploads.map(uploadOne));
    if (workspaceMounted.current) await refreshData().catch(() => undefined);
  };

  const hasDraggedFiles = (event: ReactDragEvent<HTMLElement>) => event.dataTransfer.types.includes("Files");
  const handleUploadDragEnter = (event: ReactDragEvent<HTMLDivElement>) => {
    if (!hasDraggedFiles(event)) return;
    event.preventDefault();
    uploadDragDepth.current += 1;
    setUploadDragActive(true);
  };
  const handleUploadDragLeave = (event: ReactDragEvent<HTMLDivElement>) => {
    if (!hasDraggedFiles(event)) return;
    uploadDragDepth.current = Math.max(0, uploadDragDepth.current - 1);
    if (!uploadDragDepth.current) setUploadDragActive(false);
  };
  const handleUploadDragOver = (event: ReactDragEvent<HTMLDivElement>) => {
    if (!hasDraggedFiles(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  };
  const handleUploadDrop = (event: ReactDragEvent<HTMLDivElement>) => {
    if (!hasDraggedFiles(event)) return;
    event.preventDefault();
    uploadDragDepth.current = 0;
    setUploadDragActive(false);
    void uploadArtifacts(Array.from(event.dataTransfer.files), null);
  };

  const compileArtifact = async (version: CompileTarget, queueGeneration: number, activityId: string) => {
    try {
      const job = await submitCompileJob(version.id, {
        idempotencyKey: crypto.randomUUID(),
      });
      if (!workspaceMounted.current) return;
      compileJobsGeneration.current += 1;
      compileActivityIds.current.set(job.id, activityId);
      compileJobIdsByActivity.current.set(activityId, job.id);
      const next = { ...compileJobsRef.current, [job.artifactVersionId]: job };
      compileJobsRef.current = next;
      setCompileJobs(next);
      patchActivity(activityId, {
        ...compileActivityStatus(job),
        detail: compileDetail(job),
        artifactId: version.artifactId,
        artifactVersionId: job.artifactVersionId,
        jobId: job.id,
        sourceUpdatedAt: job.updatedAt,
      });
      if (!compileSubmissionQueue.current.isTaskCurrent(queueGeneration, activityId)) {
        patchActivity(activityId, { detail: "正在终止任务…", cancellable: false });
        try {
          await cancelCompileTasks([job.id]);
        } catch (cause) {
          const message = cause instanceof Error ? cause.message : "未能终止任务，请稍后重试";
          patchActivity(activityId, { state: "failed", detail: message, cancellable: false });
          setError(message);
        }
        return;
      }
      if (job.status === "completed") await refreshData();
    } catch (cause) {
      if (!workspaceMounted.current) return;
      if (!compileSubmissionQueue.current.isTaskCurrent(queueGeneration, activityId)) {
        patchActivity(activityId, { state: "cancelled", detail: "任务已终止", cancellable: false });
        if (!compileSubmissionQueue.current.isCurrent(queueGeneration)) {
          try {
            await cancelCompileTasks(null);
          } catch (cancelCause) {
            const message = cancelCause instanceof Error ? cancelCause.message : "未能终止任务，请稍后重试";
            patchActivity(activityId, { state: "failed", detail: message, cancellable: false });
            setError(message);
          }
        }
        return;
      }
      const message = cause instanceof Error ? cause.message : "Compile 失败";
      patchActivity(activityId, { state: "failed", detail: message });
      setError(message);
    }
  };

  const compileArtifacts = async (versions: CompileTarget[], automaticAttempt?: number) => {
    const queue = [...new Map(versions.map((version) => [version.id, version])).values()]
      .filter((version) => !submittingVersions.current.has(version.id));
    if (!queue.length) return;
    const queueGeneration = compileSubmissionQueue.current.generation();
    for (const version of queue) {
      submittingVersions.current.add(version.id);
      const artifactId = version.artifactId ?? artifactIdsByVersion.current.get(version.id);
      if (artifactId) uploadedPendingArtifacts.current.delete(artifactId);
      rememberCompileAttempt(version.id, automaticAttempt ?? 1);
    }
    try {
      for (const version of queue) {
        if (!compileSubmissionQueue.current.isCurrent(queueGeneration)) break;
        const activityId = crypto.randomUUID();
        setError("");
        addActivity({
          id: activityId,
          label: version.fileName,
          state: "working",
          detail: automaticAttempt
            ? `正在自动重试 Compile（${automaticAttempt}/${MAX_COMPILE_RUN_ATTEMPTS}）`
            : "正在提交 Compile 任务",
          kind: "compile",
          artifactId: version.artifactId,
          artifactVersionId: version.id,
          cancellable: true,
          stage: "queued",
          progress: 0,
        });
        const executed = await compileSubmissionQueue.current.run(
          queueGeneration,
          activityId,
          async () => {
            await compileArtifact(version, queueGeneration, activityId);
            return true;
          },
        );
        if (executed === undefined) {
          patchActivity(activityId, { state: "cancelled", detail: "任务已终止", cancellable: false, stage: undefined });
        }
      }
      if (compileSubmissionQueue.current.isCurrent(queueGeneration)) {
        if (!compileAllController.current) await refreshData();
      }
    } catch (cause) {
      if (compileSubmissionQueue.current.isCurrent(queueGeneration)) {
        setError(cause instanceof Error ? cause.message : "文献库读取失败，请稍后重试");
      }
    } finally {
      for (const version of queue) submittingVersions.current.delete(version.id);
    }
  };
  useEffect(() => {
    compileArtifactsRef.current = compileArtifacts;
  });

  useEffect(() => {
    for (const job of Object.values(compileJobs)) {
      if (automaticallyRetriedJobs.current.has(job.id)) continue;
      const attempt = compileRunAttempts.current.get(job.artifactVersionId) ?? 1;
      const nextAttempt = nextCompileRunAttempt(job, attempt);
      if (!nextAttempt) continue;
      const version: CompileTarget = {
        id: job.artifactVersionId,
        fileName: job.fileName ?? "未命名文献",
        artifactId: artifactIdsByVersion.current.get(job.artifactVersionId),
      };
      automaticallyRetriedJobs.current.add(job.id);
      void compileArtifactsRef.current([version], nextAttempt);
    }
  }, [compileJobs, patchActivity]);

  const uploadSynthesisPdf = async (file: File) => {
    const activityId = crypto.randomUUID();
    const controller = new AbortController();
    uploadControllers.current.set(activityId, controller);
    setError("");
    addActivity({ id: activityId, label: file.name, state: "working", detail: "正在上传新论文", kind: "upload", cancellable: true });
    try {
      const version = await uploadScheduler.run(controller.signal, () => uploadArtifact(file, controller.signal, {
        mediaType: "application/pdf",
        errorMessage: "PDF 入库失败",
      }));
      rememberArtifactIdentity(artifactIdsByVersion.current, version.id, version.artifactId);
      patchActivity(activityId, { state: "done", detail: "上传完成，已开始提取论文证据", artifactId: version.artifactId, artifactVersionId: version.id, cancellable: false });
      void compileArtifacts([version]);
      await refreshData().catch(() => undefined);
      return { artifactVersionId: version.id, fileName: version.fileName };
    } catch (cause) {
      const message = controller.signal.aborted ? "上传已终止" : cause instanceof Error ? cause.message : "PDF 上传失败，请稍后重试";
      patchActivity(activityId, { state: controller.signal.aborted ? "cancelled" : "failed", detail: message, cancellable: false });
      throw new Error(message);
    } finally {
      uploadControllers.current.delete(activityId);
    }
  };

  const retryCompileActivities = (items: Activity[]) => {
    const versions = items.flatMap((item): CompileTarget[] => item.kind === "compile" && item.artifactVersionId
      ? [{ id: item.artifactVersionId, fileName: item.label, artifactId: item.artifactId }]
      : []);
    if (versions.length) void compileArtifacts(versions);
  };

  const compileAllPending = async () => {
    if (compileAllController.current) return;
    const controller = new AbortController();
    compileAllController.current = controller;
    setCompileAllProgress(0);
    const query = pendingRequest.current.query;
    try {
      await streamPendingCompile<ArtifactVersionDto>({
        signal: controller.signal,
        read: async (cursor, signal) => {
          const params = new URLSearchParams({ status: "pending", limit: "20", query });
          if (cursor) params.set("cursor", cursor);
          const response = await fetch(`/api/library?${params}`, { cache: "no-store", signal });
          const payload = await readJson(response);
          if (!response.ok) throw new Error(responseError(payload, "待 Compile 文献读取失败"));
          const result = payload as { artifacts: LibraryArtifactDto[]; pageInfo: { nextCursor: string | null } };
          return { items: result.artifacts.flatMap((item) => item.latestVersion && hasCompilableLibraryVersion(item) ? [item.latestVersion] : []), nextCursor: result.pageInfo.nextCursor };
        },
        waitForCapacity: async (signal) => {
          while (Object.values(compileJobsRef.current).filter((job) => job.status === "queued" || job.status === "running").length >= 20) {
            signal.throwIfAborted();
            await new Promise<void>((resolve, reject) => {
              const abort = () => { window.clearTimeout(timer); reject(signal.reason); };
              const timer = window.setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, 1500);
              signal.addEventListener("abort", abort, { once: true });
            });
          }
        },
        submit: async (version) => { await compileArtifacts([version]); },
        onProgress: (count) => { if (workspaceMounted.current) setCompileAllProgress(count); },
      });
    } catch (cause) {
      if (!controller.signal.aborted && workspaceMounted.current) setError(cause instanceof Error ? cause.message : "批量 Compile 提交失败");
    } finally {
      if (compileAllController.current === controller) compileAllController.current = null;
      if (workspaceMounted.current) {
        setCompileAllProgress(null);
        await refreshData().catch((cause) => setError(cause instanceof Error ? cause.message : "文献列表刷新失败"));
      }
    }
  };

  const cancelActivity = async (item: Activity) => {
    if (item.kind === "upload") {
      uploadControllers.current.get(item.id)?.abort();
      return;
    }
    const jobId = item.jobId ?? compileJobIdsByActivity.current.get(item.id);
    if (!jobId) {
      const cancellation = compileSubmissionQueue.current.cancel(item.id);
      if (cancellation === "queued") {
        patchActivity(item.id, { state: "cancelled", detail: "任务已终止", cancellable: false, stage: undefined });
      } else if (cancellation === "running") {
        patchActivity(item.id, { detail: "正在终止任务…", cancellable: false, stage: undefined });
      }
      return;
    }
    patchActivity(item.id, { detail: "正在终止任务…", cancellable: false });
    try {
      await cancelCompileTasks([jobId]);
    } catch (cause) {
      patchActivity(item.id, { detail: cause instanceof Error ? cause.message : "未能终止任务，请稍后重试", cancellable: true });
      setError(cause instanceof Error ? cause.message : "未能终止任务，请稍后重试");
    }
  };

  const cancelAllActivities = async () => {
    compileAllController.current?.abort();
    const localCancellations = new Map(
      compileSubmissionQueue.current.cancelPending().map((item) => [item.id, item.started]),
    );
    for (const controller of uploadControllers.current.values()) controller.abort();
    setActivity((items) => items.map((item) => {
      const acceptedJob = item.state === "working" && item.kind === "compile" && item.cancellable && item.stage !== "importing";
      const localSubmission = item.state === "working" && item.kind === "compile" && !item.jobId && item.stage === "queued";
      if (localSubmission && localCancellations.get(item.id) === false) {
        return { ...item, state: "cancelled" as const, detail: "任务已终止", cancellable: false, stage: undefined };
      }
      return acceptedJob || localSubmission
        ? { ...item, detail: "正在终止任务…", cancellable: false, ...(localSubmission ? { stage: undefined } : {}) }
        : item;
    }));
    try {
      await cancelCompileTasks(null);
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "未能终止任务，请稍后重试";
      setError(message);
      await loadCompileJobs().catch(() => undefined);
    }
  };

  const resetCitationReception = useCallback(() => {
    citationReceptionGeneration.current += 1;
    citationReceptionController.current?.abort();
    citationReceptionActionController.current?.abort();
    citationReceptionActionController.current = null;
    setCitationReception(null);
    setCitationReceptionLoading(false);
    setCitationReceptionError("");
    setCitationReceptionAnalyzing(false);
    setCitationReceptionActionError("");
  }, []);

  /** Failed task rows are list entries only; dismissing one never touches the paper. */
  const dismissFailedActivities = (items: Activity[]) => {
    const ids = new Set(items.map((item) => item.id));
    setActivity((current) => current.filter((item) => !ids.has(item.id)));
  };

  const review = async (snapshotId: string, decision: ReviewDecision, rationale: string) => {
    const note = rationale.trim();
    if (note.length < 3 || note.length > 4000) {
      const message = "复核说明须为 3–4000 字，请修改后再提交。";
      setError(message);
      throw new Error(message);
    }
    setBusy("review");
    setError("");
    try {
      const response = await fetch(`/api/evidence-graphs/${encodeURIComponent(snapshotId)}/reviews`, {
        method: "POST",
        headers: { "content-type": "application/json", "idempotency-key": crypto.randomUUID() },
        body: JSON.stringify({ decision, rationale: note }),
      });
      const payload = await readJson(response);
      if (!response.ok) throw new Error(responseError(payload, "复核记录保存失败，请稍后重试"));
      if (!workspaceMounted.current) return;
      await refreshData();
      if (selectedIdRef.current === snapshotId) await loadDetail(snapshotId);
    } catch (cause) {
      if (!workspaceMounted.current) return;
      setError(cause instanceof Error ? cause.message : "复核记录保存失败，请稍后重试");
      throw cause;
    } finally {
      if (workspaceMounted.current) setBusy("");
    }
  };

  const selectSnapshot = useCallback((snapshotId: string) => {
    selectedIdRef.current = snapshotId;
    resetCitationReception();
    setDetail(null);
    setLoadingDetail(true);
    setSelectedId(snapshotId);
  }, [resetCitationReception]);

  useEffect(() => {
    if (activeView !== "review" || selectedId || !snapshots[0]) return;
    const frame = window.requestAnimationFrame(() => selectSnapshot(snapshots[0].id));
    return () => window.cancelAnimationFrame(frame);
  }, [activeView, selectSnapshot, selectedId, snapshots]);

  const openSnapshot = (snapshotId: string, claimId?: string) => {
    setPaperClaimId(claimId ?? null);
    setPaperReturnView(
      activeView === "knowledgeGraph" || activeView === "visualLibrary" || activeView === "search" || activeView === "verification" || activeView === "review"
        ? activeView
        : "library",
    );
    selectSnapshot(snapshotId);
    setActiveView("paper");
  };

  const openReview = (snapshotId?: string) => {
    const target = snapshotId ?? selectedId ?? snapshots[0]?.id ?? null;
    if (target) selectSnapshot(target);
    setActiveView("review");
  };

  const compareClaim = (claim: AtomicClaim) => {
    if (!selectedId) return;
    setSearchClaim({ snapshotId: selectedId, claimId: claim.id, proposition: claim.proposition });
    setOpenAlexSearchScope(null);
    setActiveView("search");
  };

  const searchOpenAlexScope = (scope: OpenAlexSearchScope) => {
    setSearchClaim(null);
    setOpenAlexSearchScope(scope);
    setActiveView("search");
  };

  const sourceTitle = (snapshot: SnapshotSummaryDto) => {
    return snapshot.title
      ?? libraryArtifacts.find((item) => item.snapshot?.id === snapshot.id)?.title
      ?? (detail?.snapshot.id === snapshot.id ? detail.graph.artifacts[0]?.canonical_name : null)
      ?? `未命名文献 ${shortHash(snapshot.graphSha256)}`;
  };
  const selectedSnapshot = snapshots.find((item) => item.id === selectedId) ?? null;
  const selectedTitle = selectedSnapshot ? sourceTitle(selectedSnapshot)
    : libraryArtifacts.find((item) => item.snapshot?.id === selectedId)?.title
      ?? (detail?.snapshot.id === selectedId ? detail.graph.artifacts[0]?.canonical_name : undefined);
  const activeTaskCount = activeActivityCount(activity);
  const visibleDetail = detail?.snapshot.id === selectedId ? detail : null;
  const visibleCitationReception = citationReception?.targetSnapshotId === selectedId
    ? citationReception
    : null;
  const visibleError = error || compilePollError;
  const navigateTo = (view: NavigationView) => {
    if (view === "geochemicalAtlas") setAtlasStarted(true);
    if (view === "knowledgeGraph") setKnowledgeGraphStarted(true);
    if (view === "verification") setVerificationStarted(true);
    if (view === "search") {
      setSearchClaim(null);
      setOpenAlexSearchScope(null);
      setUploadDragActive(false);
    }
    if (view === "review") openReview();
    else setActiveView(view);
  };

  return (
    <main className="app-shell" id="main-content">
      <a className="skip-link" href="#view-content">跳到主要内容</a>
      <header className="app-masthead">
        <button className="brand" type="button" onClick={(event) => { navigateTo("search"); if (event.detail > 0) event.currentTarget.blur(); }} aria-label="DeepShovel 观点校验首页" title="DeepShovel 首页">
          <span className="brand-mark"><EvidenceMark /></span>
          <span className="brand-copy"><strong>DeepShovel</strong><small>论文证据分析平台</small></span>
        </button>
        <div className="workspace-rail">
          <nav className="global-nav" aria-label="主要功能">
            {PRIMARY_NAVIGATION.map((item) => (
              <WorkspaceNavButton
                key={item.view}
                active={activeView === item.view}
                description={item.description}
                icon={item.icon}
                label={item.label}
                onClick={() => navigateTo(item.view)}
              />
            ))}
          </nav>
          <nav className="quality-nav" aria-label="质量控制">
            {QUALITY_NAVIGATION.map((item) => (
              <WorkspaceNavButton
                key={item.view}
                active={activeView === item.view}
                ariaLabel={item.view === "activity" ? activityTaskButtonLabel(activeTaskCount) : undefined}
                description={item.description}
                icon={item.icon}
                label={item.label}
                onClick={() => navigateTo(item.view)}
              />
            ))}
          </nav>
        </div>
      </header>

      <div className="app-stage" data-active-view={activeView}>
        <div
          className={`view-content${uploadDragActive ? " upload-drag-active" : ""}`}
          id="view-content"
          tabIndex={-1}
          onDragEnter={activeView === "search" ? undefined : handleUploadDragEnter}
          onDragLeave={activeView === "search" ? undefined : handleUploadDragLeave}
          onDragOver={activeView === "search" ? undefined : handleUploadDragOver}
          onDrop={activeView === "search" ? undefined : handleUploadDrop}
        >
          {uploadDragActive ? <div className="upload-drop-overlay" aria-hidden="true"><UiIcon name="upload" size={28} /><strong>松开即可上传</strong><span>PDF、文档、表格或图像</span></div> : null}
          {visibleError ? <div className="error-banner" role="alert"><UiIcon name="warning" /><span>{visibleError}</span><button onClick={() => { setError(""); setCompilePollError(""); }} aria-label="关闭错误"><UiIcon name="close" /></button></div> : null}
          <div hidden={activeView !== "search"}>
            <div className="search-home">
              <EvidenceSearch
                active={activeView === "search"}
                key={searchClaim ? `${searchClaim.snapshotId}:${searchClaim.claimId}` : openAlexSearchScope?.id ?? "global-search"}
                initialClaim={searchClaim ?? undefined}
                initialOpenAlexScope={openAlexSearchScope ?? undefined}
                onClearInitialClaim={() => setSearchClaim(null)}
                onClearOpenAlexScope={() => setOpenAlexSearchScope(null)}
                onOpenSnapshot={openSnapshot}
              />
            </div>
          </div>
          {knowledgeGraphStarted ? (
            <div hidden={activeView !== "knowledgeGraph"}>
              <EvidenceKnowledgeGraph onOpenSnapshot={openSnapshot} onSearchScope={searchOpenAlexScope} />
            </div>
          ) : null}
          {atlasStarted ? <div className="atlas-retained-view" hidden={activeView !== "geochemicalAtlas"}><GeochemicalAtlas /></div> : null}
          {verificationStarted ? (
            <section hidden={activeView !== "verification"} className="verification-home" aria-label="论文校验工作台">
              <EvidenceSynthesis active={activeView === "verification"} mode="paper" artifacts={synthesisArtifacts} onOpenSnapshot={openSnapshot} onUploadPdf={uploadSynthesisPdf} />
            </section>
          ) : null}
          {activeView === "search" || activeView === "verification" ? null : activeView === "library" ? (
            <LibraryView
              artifacts={libraryArtifacts}
              loading={libraryLoading}
              query={libraryQuery}
              disabled={Boolean(busy)}
              pageInfo={libraryPage}
              onSearch={(query) => {
                libraryQueryRef.current = query;
                libraryPageRef.current = 1;
                setLibraryQuery(query);
                void loadLibrary({ query, status: "all", page: 1 }).catch((cause) => setError(cause instanceof Error ? cause.message : "文献库读取失败，请稍后重试"));
              }}
              onPageChange={(page) => {
                libraryPageRef.current = page;
                void loadLibrary({ query: libraryQueryRef.current, status: "all", page })
                  .catch((cause) => setError(cause instanceof Error ? cause.message : "文献库读取失败，请稍后重试"));
              }}
              onPageSizeChange={(pageSize) => {
                libraryPageRef.current = 1;
                libraryPageSizeRef.current = pageSize;
                void loadLibrary({ query: libraryQueryRef.current, status: "all", page: 1, pageSize })
                  .catch((cause) => setError(cause instanceof Error ? cause.message : "文献库读取失败，请稍后重试"));
              }}
              onAddArtifact={() => beginArtifactUpload(null)}
              onCompile={(version) => void compileArtifacts([version])}
              onAddVersion={beginArtifactUpload}
              onOpenSnapshot={openSnapshot}
            />
          ) : activeView === "knowledgeGraph" ? null : activeView === "visualLibrary" ? (
            <PaperVisualLibrary onOpenSnapshot={openSnapshot} />
          ) : activeView === "geochemicalAtlas" ? null : activeView === "paper" ? (
            <PaperDetail
              key={`${selectedId ?? "paper"}:${paperClaimId ?? ""}`}
              initialClaimId={paperClaimId}
              detail={visibleDetail}
              loading={loadingDetail}
              sourceTitle={selectedTitle}
              citationReception={visibleCitationReception}
              citationReceptionLoading={citationReceptionLoading}
              citationReceptionError={citationReceptionError}
              citationReceptionAnalyzing={citationReceptionAnalyzing}
              citationReceptionActionError={citationReceptionActionError}
              onBack={() => setActiveView(paperReturnView)}
              backLabel={PAPER_RETURN_LABELS[paperReturnView]}
              onCompareClaim={compareClaim}
              onRetryCitationReception={() => {
                if (selectedId) void loadCitationReception(selectedId);
              }}
              onAnalyzeCitationReception={() => {
                if (selectedId) void analyzeCitationReception(selectedId);
              }}
              onOpenCitationSnapshot={openSnapshot}
              onOpenReview={() => openReview(selectedId ?? undefined)}
            />
          ) : activeView === "review" ? (
            <ReviewWorkspace
              snapshots={snapshots}
              query={reviewQuery}
              filter={reviewFilter}
              pageInfo={reviewPage}
              listLoading={reviewListLoading}
              selectedId={selectedId}
              detail={visibleDetail}
              loading={loadingDetail}
              disabled={Boolean(busy)}
              sourceTitle={sourceTitle}
              onSelect={selectSnapshot}
              onReview={review}
              onOpenPaper={openSnapshot}
              onOpenLibrary={() => setActiveView("library")}
              onSearch={(query) => {
                reviewQueryRef.current = query;
                reviewPageRef.current = 1;
                setReviewQuery(query);
                void loadSnapshots({ query, page: 1 }).catch((cause) => setError(cause instanceof Error ? cause.message : "复核队列读取失败，请稍后重试"));
              }}
              onFilterChange={(status) => {
                reviewFilterRef.current = status;
                reviewPageRef.current = 1;
                setReviewFilter(status);
                void loadSnapshots({ status, page: 1 }).catch((cause) => setError(cause instanceof Error ? cause.message : "复核队列读取失败，请稍后重试"));
              }}
              onPageChange={(page) => {
                reviewPageRef.current = page;
                void loadSnapshots({ page }).catch((cause) => setError(cause instanceof Error ? cause.message : "复核队列读取失败，请稍后重试"));
              }}
              onPageSizeChange={(pageSize) => {
                reviewPageRef.current = 1;
                reviewPageSizeRef.current = pageSize;
                void loadSnapshots({ page: 1, pageSize }).catch((cause) => setError(cause instanceof Error ? cause.message : "复核队列读取失败，请稍后重试"));
              }}
            />
          ) : (
            <ActivityView
              activity={activity}
              pendingArtifacts={pendingTaskArtifacts}
              pendingLoading={pendingTaskLoading}
              pendingPage={pendingPage}
              compileAllProgress={compileAllProgress}
              onCompileAll={() => void compileAllPending()}
              onPendingRequest={(request) => {
                void loadTaskPendingArtifacts(request).catch((cause) => setError(cause instanceof Error ? cause.message : "待 Compile 文献读取失败"));
              }}
              disabled={Boolean(busy)}
              filter={activityFilter}
              onFilterChange={(filter) => { activityFilterRef.current = filter; setActivityFilter(filter); }}
              onUpload={() => beginArtifactUpload(null)}
              onCompile={(version) => void compileArtifacts([version])}
              onRetry={(item) => retryCompileActivities([item])}
              onRetryMany={retryCompileActivities}
              onCancel={(item) => void cancelActivity(item)}
              onCancelAll={() => void cancelAllActivities()}
              onDismissActivities={dismissFailedActivities}
            />
          )}
        </div>
      </div>

      <input ref={artifactInput} hidden multiple type="file" accept={ARTIFACT_ACCEPT} onChange={(event) => void uploadArtifacts(event.target.files)} />
    </main>
  );
}
