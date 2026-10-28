-- Derived literal-recall text only. SQLite lower/coalesce preserves the existing
-- ASCII case folding and canonical/original separator exactly; no tokenizer.
CREATE TABLE evidence_quick_search (
  project_id TEXT NOT NULL,
  snapshot_id TEXT NOT NULL,
  claim_id TEXT NOT NULL,
  search_text TEXT NOT NULL,
  PRIMARY KEY (snapshot_id, claim_id)
) WITHOUT ROWID;
--> statement-breakpoint
CREATE INDEX evidence_quick_search_project_snapshot_idx
ON evidence_quick_search (project_id, snapshot_id);
--> statement-breakpoint
INSERT INTO evidence_quick_search
SELECT project_id, snapshot_id, claim_id,
  lower(coalesce(json_extract(document_json, '$.canonical_claim'), '') || ' ' ||
        coalesce(json_extract(document_json, '$.original_claim'), ''))
FROM evidence_retrieval_documents;
--> statement-breakpoint
-- Retrieval documents are immutable: source insertion and authorized deletion
-- update this rebuildable projection in the same transaction.
CREATE TRIGGER evidence_quick_search_insert
AFTER INSERT ON evidence_retrieval_documents BEGIN
  INSERT INTO evidence_quick_search (project_id, snapshot_id, claim_id, search_text)
  VALUES (NEW.project_id, NEW.snapshot_id, NEW.claim_id,
    lower(coalesce(json_extract(NEW.document_json, '$.canonical_claim'), '') || ' ' ||
          coalesce(json_extract(NEW.document_json, '$.original_claim'), '')));
END;
--> statement-breakpoint
CREATE TRIGGER evidence_quick_search_delete
AFTER DELETE ON evidence_retrieval_documents BEGIN
  DELETE FROM evidence_quick_search
  WHERE snapshot_id = OLD.snapshot_id AND claim_id = OLD.claim_id;
END;
