/**
 * Server-side retrieval continuation sessions.
 *
 * The Compiler signs three continuation capabilities: the preview QueryPlan
 * handoff (v1), the needs-context token (n1) and the clarification token (c2).
 * They stay on the server. The browser receives an opaque `rs1.` session ID
 * that resolves to exactly one token of exactly one kind until it expires;
 * the Compiler still authenticates the token and its bound request fields.
 * A missing or expired session means "run preview again", never a fallback.
 */
import { isRetrievalSessionId } from "../evidence-retrieval/contract.ts";
import {
  QUERY_PLAN_HANDOFF_MAX_LENGTH,
  RETRIEVAL_SESSION_KINDS,
  RETRIEVAL_SESSION_TTL_SECONDS,
} from "../shared-contract.generated.ts";

export type RetrievalSessionKind = (typeof RETRIEVAL_SESSION_KINDS)[number];

/** Not shorter than the longest Compiler token lifetime (30 minutes). */
export const RETRIEVAL_SESSION_TTL_MS = RETRIEVAL_SESSION_TTL_SECONDS * 1_000;
const RETRIEVAL_SESSION_MAX_TOKEN_LENGTH = QUERY_PLAN_HANDOFF_MAX_LENGTH;
const PRUNE_BATCH = 200;

function base64Url(bytes: Uint8Array) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/u, "");
}

export function newRetrievalSessionId() {
  return `rs1.${base64Url(crypto.getRandomValues(new Uint8Array(32)))}`;
}

/** Store one Compiler token and return the opaque ID the browser may hold. */
export async function createRetrievalSession(
  DB: D1Database,
  kind: RetrievalSessionKind,
  token: string,
  now = new Date(),
) {
  if (typeof token !== "string" || token.length < 1 || token.length > RETRIEVAL_SESSION_MAX_TOKEN_LENGTH) {
    throw new TypeError("retrieval session token has an invalid shape");
  }
  const id = newRetrievalSessionId();
  const createdAt = now.toISOString();
  const expiresAt = new Date(now.getTime() + RETRIEVAL_SESSION_TTL_MS).toISOString();
  await DB.batch([
    // Bounded pruning on every write keeps the table small without a sweeper.
    DB.prepare(`DELETE FROM retrieval_sessions WHERE id IN (
      SELECT id FROM retrieval_sessions WHERE expires_at <= ? ORDER BY expires_at LIMIT ${PRUNE_BATCH})`)
      .bind(createdAt),
    DB.prepare(`INSERT INTO retrieval_sessions (id, kind, token, created_at, expires_at)
      VALUES (?, ?, ?, ?, ?)`).bind(id, kind, token, createdAt, expiresAt),
  ]);
  return id;
}

/**
 * The live token behind one session ID of the expected kind, else null. Use
 * slides the expiry: the browser resends a context or clarification
 * continuation with the final that follows its preview, and that final must
 * not fail only because the session was created 30 minutes before the
 * preview. The Compiler still enforces each token's own lifetime.
 */
export async function resolveRetrievalSession(
  DB: D1Database,
  kind: RetrievalSessionKind,
  id: unknown,
  now = new Date(),
) {
  if (!isRetrievalSessionId(id)) return null;
  const row = await DB.prepare(`UPDATE retrieval_sessions SET expires_at = ?
    WHERE id = ? AND kind = ? AND expires_at > ?
    RETURNING token`)
    .bind(new Date(now.getTime() + RETRIEVAL_SESSION_TTL_MS).toISOString(), id, kind, now.toISOString())
    .first<{ token: string }>();
  return row?.token ?? null;
}
