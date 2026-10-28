import { readJson, responseError, type DataQualityJobDto, type DataQualityReportDto } from "./workspace-types.ts";

export type DataQualityResult = { job: DataQualityJobDto | null; report: DataQualityReportDto | null };

function waitForNextRead(signal: AbortSignal) {
  return new Promise<void>((resolve) => {
    const finish = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", finish);
      resolve();
    };
    const timer = setTimeout(finish, 1_800);
    signal.addEventListener("abort", finish, { once: true });
    if (signal.aborted) finish();
  });
}

/** Keep one read in flight until this version's job settles, even if its status stays unchanged. */
export async function watchDataQuality(
  artifactVersionId: string,
  signal: AbortSignal,
  onResult: (result: DataQualityResult) => void,
  waitForJob = false,
) {
  while (!signal.aborted) {
    const response = await fetch(`/api/data-quality?artifact_version_id=${encodeURIComponent(artifactVersionId)}`, {
      cache: "no-store",
      signal,
    });
    const payload = await readJson(response);
    if (signal.aborted) return;
    if (!response.ok && response.status !== 404) {
      throw new Error(responseError(payload, "数据检查状态读取失败，请稍后重试"));
    }
    const result = response.status === 404 ? { job: null, report: null } : payload as DataQualityResult;
    onResult(result);
    const active = result.job?.status === "queued" || result.job?.status === "running";
    if (!active && !(waitForJob && !result.job)) return;
    await waitForNextRead(signal);
  }
}
