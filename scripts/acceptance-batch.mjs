#!/usr/bin/env node
import { access, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import {
  AcceptanceApiClient,
  AcceptanceApiError,
  JsonlEventLog,
  analyzeStates,
  inventoryPdfDirectories,
  isTerminalJob,
  readEventLog,
  readVerifiedPdf,
  reconstructProgress,
  safeJobMetadata,
  writeSummaryCsv,
} from "./acceptance-lib.mjs";

const USAGE = `Usage:
  node scripts/acceptance-batch.mjs \\
    --source-dir <pdf-directory> [--source-dir <pdf-directory> ...] \\
    --base-url <DeepShovel-Web-URL> --output-dir <run-directory> [options]

Options:
  --expected-count <n>          Exact combined inventory size (default: 14)
  --submit-workers <1..3>       Concurrent upload/submit workers (default: 3)
  --poll-ms <milliseconds>      Poll interval per active job (default: 2000)
  --actor <email>               Optional local actor header; never recorded
  --cookie <Cookie-header>      Optional Cookie header; never recorded
  --cookie-file <path>          Read the Cookie header from a 0600 file
  --resume <events.jsonl>       Resume the same inventory and existing jobs
  --dry-run                     Inventory/hash only; make no network requests
  --help                        Show this help
`;

function positiveInteger(value, name, minimum, maximum) {
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < minimum || parsed > maximum) {
    throw new Error(`${name} must be an integer from ${minimum} to ${maximum}`);
  }
  return parsed;
}

export function parseArgs(argv) {
  const options = {
    sourceDirs: [],
    expectedCount: 14,
    submitWorkers: 3,
    pollMs: 2_000,
    requiredGptPeak: 2,
    dryRun: false,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    const value = () => {
      const next = argv[index + 1];
      if (!next || next.startsWith("--")) throw new Error(`${flag} requires a value`);
      index += 1;
      return next;
    };
    if (flag === "--source-dir") options.sourceDirs.push(value());
    else if (flag === "--base-url") options.baseUrl = value();
    else if (flag === "--output-dir") options.outputDir = value();
    else if (flag === "--resume") options.resume = value();
    else if (flag === "--actor") options.actor = value();
    else if (flag === "--cookie") options.cookie = value();
    else if (flag === "--cookie-file") options.cookieFile = value();
    else if (flag === "--expected-count") options.expectedCount = positiveInteger(value(), flag, 1, 10_000);
    else if (flag === "--submit-workers") options.submitWorkers = positiveInteger(value(), flag, 1, 3);
    else if (flag === "--poll-ms") options.pollMs = positiveInteger(value(), flag, 250, 60_000);
    else if (flag === "--dry-run") options.dryRun = true;
    else if (flag === "--help") options.help = true;
    else throw new Error(`Unknown option: ${flag}`);
  }
  if (options.help) return options;
  if (options.sourceDirs.length === 0) throw new Error("At least one --source-dir value is required");
  if (!options.resume && !options.outputDir) throw new Error("--output-dir is required unless --resume is used");
  if (options.resume && options.outputDir) throw new Error("--output-dir cannot be combined with --resume");
  if (options.resume && options.dryRun) throw new Error("--dry-run cannot be combined with --resume");
  if (!options.dryRun && !options.baseUrl) throw new Error("--base-url is required unless --dry-run is used");
  if (options.cookie && options.cookieFile) throw new Error("Use only one of --cookie and --cookie-file");
  return options;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fileExists(filePath) {
  try { await access(filePath); return true; } catch { return false; }
}

async function workerPool(items, concurrency, task) {
  let cursor = 0;
  const worker = async () => {
    while (cursor < items.length) {
      const item = items[cursor];
      cursor += 1;
      await task(item);
    }
  };
  const settled = await Promise.allSettled(Array.from({ length: Math.min(concurrency, items.length) }, worker));
  const rejected = settled.find((result) => result.status === "rejected");
  if (rejected) throw rejected.reason;
}

async function writeSummaryJson(outputDir, analysis, documentCount) {
  const summary = {
    schema_version: "deepshovel-acceptance-summary/1.0",
    generated_at: new Date().toISOString(),
    document_count: documentCount,
    analysis,
  };
  await writeFile(path.join(outputDir, "summary.json"), `${JSON.stringify(summary, null, 2)}\n`, {
    encoding: "utf8",
    mode: 0o600,
  });
}

function observedFields(state, job) {
  return { order: state.item.order, sha256: state.item.sha256, ...safeJobMetadata(job) };
}

async function pollJob({ state, client, log, pollMs, sleepImpl, onProgress }) {
  let lastState = state.job ? `${state.job.status}:${state.job.stage}:${state.job.updatedAt}` : "";
  let consecutiveFailures = 0;
  while (!isTerminalJob(state.job)) {
    try {
      const job = await client.poll(state.job.id, state.upload.artifactVersionId);
      consecutiveFailures = 0;
      state.job = job;
      const nextState = `${job.status}:${job.stage}:${job.updatedAt}`;
      if (nextState !== lastState || isTerminalJob(job)) {
        await log.append("job_observed", observedFields(state, job));
        lastState = nextState;
        onProgress?.(state);
      }
    } catch (error) {
      if (!(error instanceof AcceptanceApiError) || !error.retryable || consecutiveFailures >= 5) throw error;
      consecutiveFailures += 1;
      await log.append("poll_deferred", {
        order: state.item.order,
        sha256: state.item.sha256,
        job_id: state.job.id,
        attempt: consecutiveFailures,
        http_status: error.status,
        error_code: error.code,
      });
    }
    if (!isTerminalJob(state.job)) await sleepImpl(pollMs);
  }
}

export async function runAcceptance(options, dependencies = {}) {
  const sleepImpl = dependencies.sleep ?? sleep;
  const inventory = await inventoryPdfDirectories(options.sourceDirs);
  if (inventory.length !== options.expectedCount) {
    throw new Error(`Expected ${options.expectedCount} PDFs, found ${inventory.length}`);
  }

  let outputDir = options.outputDir;
  let events = [];
  let runId;
  let eventsPath;
  if (options.resume) {
    eventsPath = path.resolve(options.resume);
    outputDir = path.dirname(eventsPath);
    ({ runId, events } = await readEventLog(eventsPath));
  } else {
    outputDir = path.resolve(outputDir);
    eventsPath = path.join(outputDir, "events.jsonl");
    if (await fileExists(eventsPath)) throw new Error("Output events.jsonl already exists; use --resume");
  }
  const log = new JsonlEventLog(eventsPath, runId);
  await log.initialize();
  if (options.resume) {
    await log.append("run_resumed");
  } else {
    await log.append("run_started", {
      expected_count: options.expectedCount,
      submit_workers: options.submitWorkers,
      poll_ms: options.pollMs,
      dry_run: options.dryRun,
    });
    for (const item of inventory) {
      await log.append("inventory_item", {
        order: item.order,
        source_group: item.sourceGroup,
        file_name: item.fileName,
        sha256: item.sha256,
        byte_size: item.byteSize,
        media_type: item.mediaType,
      });
    }
  }
  const states = reconstructProgress(inventory, events);

  if (options.dryRun) {
    await log.append("dry_run_completed", { document_count: inventory.length });
    await log.flush();
    await writeSummaryCsv(path.join(outputDir, "summary.csv"), states);
    await writeSummaryJson(outputDir, null, inventory.length);
    return { outputDir, states, analysis: null };
  }

  const cookie = options.cookieFile ? (await readFile(options.cookieFile, "utf8")).trim() : options.cookie;
  const client = new AcceptanceApiClient({
    baseUrl: options.baseUrl,
    runId: log.runId,
    actor: options.actor,
    cookie,
    fetchImpl: dependencies.fetchImpl,
    sleep: sleepImpl,
    onRetry: async (retry) => log.append("request_retry", {
      operation: retry.operation,
      attempt: retry.attempt,
      http_status: retry.status,
      error_code: retry.code,
    }),
  });
  const pollPromises = [];
  let pollFailure = null;
  const onProgress = dependencies.onProgress ?? ((state) => {
    process.stdout.write(`[${String(state.item.order).padStart(2, "0")}/${inventory.length}] ${state.job.stage}\n`);
  });

  const beginPolling = (state) => {
    if (!state.job || isTerminalJob(state.job)) return;
    pollPromises.push(pollJob({ state, client, log, pollMs: options.pollMs, sleepImpl, onProgress })
      .catch((error) => { pollFailure ??= error; }));
  };
  for (const state of states.values()) beginPolling(state);

  const pending = [...states.values()].filter((state) => !state.job);
  let submissionFailure = null;
  try {
    await workerPool(pending, options.submitWorkers, async (state) => {
      if (!state.upload) {
        const bytes = await readVerifiedPdf(state.item);
        state.upload = await client.upload(state.item, bytes);
        await log.append("upload_completed", {
          order: state.item.order,
          sha256: state.item.sha256,
          artifact_id: state.upload.artifactId,
          artifact_version_id: state.upload.artifactVersionId,
          duplicate: state.upload.duplicate,
          elapsed_ms: state.upload.elapsedMs,
        });
      }
      const submitted = await client.submit(state.item, state.upload.artifactVersionId);
      state.submission = { duplicate: submitted.duplicate, elapsedMs: submitted.elapsedMs };
      state.job = submitted.job;
      await log.append("compile_submitted", {
        order: state.item.order,
        sha256: state.item.sha256,
        duplicate: state.submission.duplicate,
        elapsed_ms: state.submission.elapsedMs,
        ...safeJobMetadata(state.job),
      });
      onProgress(state);
      beginPolling(state);
    });
  } catch (error) {
    submissionFailure = error;
  }
  await Promise.all(pollPromises);
  if (pollFailure) throw pollFailure;
  if (submissionFailure) throw submissionFailure;

  const analysis = analyzeStates(states, {
    expectedCount: options.expectedCount,
    requiredGptPeak: options.requiredGptPeak,
  });
  await log.append("run_completed", { analysis });
  await log.flush();
  await writeSummaryCsv(path.join(outputDir, "summary.csv"), states);
  await writeSummaryJson(outputDir, analysis, inventory.length);
  return { outputDir, states, analysis };
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) {
    process.stdout.write(USAGE);
    return;
  }
  const result = await runAcceptance(options);
  if (result.analysis) {
    process.stdout.write(`${JSON.stringify(result.analysis, null, 2)}\n`);
    if (!result.analysis.passed) process.exitCode = 2;
  } else {
    process.stdout.write(`Dry run complete: ${result.states.size} PDFs\n`);
  }
  process.stdout.write(`Metadata: ${result.outputDir}\n`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    process.stderr.write(`Acceptance failed: ${error instanceof Error ? error.message : "unknown error"}\n`);
    process.exitCode = 1;
  });
}
