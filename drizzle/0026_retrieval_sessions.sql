-- Server-side retrieval continuation state. The browser holds only an opaque
-- session ID; the Compiler-signed preview handoff, needs-context and
-- clarification tokens never leave the server. Rows expire 30 minutes after
-- their last use and are pruned on write; they hold no research data.
CREATE TABLE `retrieval_sessions` (
	`id` text PRIMARY KEY NOT NULL,
	`kind` text NOT NULL,
	`token` text NOT NULL,
	`created_at` text NOT NULL,
	`expires_at` text NOT NULL,
	CONSTRAINT "retrieval_sessions_kind_check" CHECK("retrieval_sessions"."kind" IN ('query_plan_handoff', 'context', 'clarification')),
	CONSTRAINT "retrieval_sessions_id_check" CHECK("retrieval_sessions"."id" GLOB 'rs1.*' AND length("retrieval_sessions"."id") = 47),
	CONSTRAINT "retrieval_sessions_token_check" CHECK(length("retrieval_sessions"."token") BETWEEN 1 AND 700000)
);
--> statement-breakpoint
CREATE INDEX `retrieval_sessions_expiry_idx` ON `retrieval_sessions` (`expires_at`);
