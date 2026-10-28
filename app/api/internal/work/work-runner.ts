/**
 * One bounded pass of the durable background work: Compile progression and
 * import, retrieval indexing and Claim-relation analysis. The local work pump
 * calls it repeatedly through POST /api/internal/work; every step persists its
 * progress before the next one, so any restart resumes where work stopped.
 */
import { getBindings, PROJECT_ID } from "@/lib/server/evidence-store";
import { countOpenWork } from "@/lib/server/work-queue";
import { advanceCompileJobs, type CompileProgressSummary } from "../../compile/compile-worker.ts";
import { runClaimRelationWork } from "./claim-relation-work.ts";
import { runRetrievalIndexWork } from "./retrieval-index-work.ts";
import { emptyPass, type WorkPassSummary } from "./work-support.ts";

export type WorkTickSummary = {
  compile: CompileProgressSummary;
  work: WorkPassSummary;
  open: number;
  more: boolean;
};

export async function runWorkTick({ budgetMs }: { budgetMs: number }): Promise<WorkTickSummary> {
  const deadline = Date.now() + budgetMs;
  const { DB } = await getBindings();
  const compile = await advanceCompileJobs({ deadline });
  const work = emptyPass();
  const indexMore = await runRetrievalIndexWork(DB, deadline, work);
  const relationMore = await runClaimRelationWork(DB, deadline, work);
  const open = await countOpenWork(DB, PROJECT_ID);
  return {
    compile,
    work,
    open,
    more: compile.more || indexMore || relationMore || Date.now() >= deadline,
  };
}
