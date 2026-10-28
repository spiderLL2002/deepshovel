-- Rebuildable graph metadata; immutable Claims remain in evidence_retrieval_documents.
CREATE TABLE graph_read_counts (project_id TEXT PRIMARY KEY, all_count INTEGER NOT NULL DEFAULT 0, processed_count INTEGER NOT NULL DEFAULT 0, edge_count INTEGER NOT NULL DEFAULT 0, unclassified_papers INTEGER NOT NULL DEFAULT 0);
--> statement-breakpoint
CREATE TABLE graph_active_claims (snapshot_id TEXT NOT NULL, claim_id TEXT NOT NULL, project_id TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT, PRIMARY KEY(snapshot_id,claim_id));
--> statement-breakpoint
CREATE INDEX graph_active_claims_page_idx ON graph_active_claims(project_id,created_at DESC,snapshot_id DESC,claim_id DESC);
--> statement-breakpoint
CREATE INDEX graph_active_claims_status_page_idx ON graph_active_claims(project_id,(completed_at IS NULL),created_at DESC,snapshot_id DESC,claim_id DESC);
--> statement-breakpoint
CREATE TABLE graph_taxonomy_nodes (project_id TEXT NOT NULL, level TEXT NOT NULL, id TEXT NOT NULL, display_name TEXT NOT NULL, parent_id TEXT, paper_count INTEGER NOT NULL DEFAULT 0, claim_count INTEGER NOT NULL DEFAULT 0, primary_claim_count INTEGER NOT NULL DEFAULT 0, processed_count INTEGER NOT NULL DEFAULT 0, edge_count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(project_id,level,id));
--> statement-breakpoint
CREATE TABLE graph_taxonomy_memberships (snapshot_id TEXT NOT NULL, project_id TEXT NOT NULL, level TEXT NOT NULL, id TEXT NOT NULL, display_name TEXT NOT NULL, parent_id TEXT, is_primary INTEGER NOT NULL, created_at TEXT NOT NULL, claim_count INTEGER NOT NULL, processed_count INTEGER NOT NULL, PRIMARY KEY(snapshot_id,level,id));
--> statement-breakpoint
CREATE INDEX graph_taxonomy_membership_scope_idx ON graph_taxonomy_memberships(project_id,level,id,created_at DESC,snapshot_id DESC);
--> statement-breakpoint
CREATE INDEX graph_taxonomy_membership_pending_idx ON graph_taxonomy_memberships(project_id,level,id,created_at DESC,snapshot_id DESC) WHERE claim_count>processed_count;
--> statement-breakpoint
CREATE INDEX graph_taxonomy_membership_processed_idx ON graph_taxonomy_memberships(project_id,level,id,created_at DESC,snapshot_id DESC) WHERE processed_count>0;
--> statement-breakpoint
CREATE TRIGGER graph_membership_insert AFTER INSERT ON graph_taxonomy_memberships BEGIN
INSERT INTO graph_taxonomy_nodes(project_id,level,id,display_name,parent_id,paper_count,claim_count,primary_claim_count,processed_count)
 VALUES(NEW.project_id,NEW.level,NEW.id,NEW.display_name,NEW.parent_id,1,NEW.claim_count,NEW.is_primary*NEW.claim_count,NEW.processed_count)
 ON CONFLICT(project_id,level,id) DO UPDATE SET paper_count=paper_count+1,claim_count=claim_count+NEW.claim_count,
 primary_claim_count=primary_claim_count+NEW.is_primary*NEW.claim_count,processed_count=processed_count+NEW.processed_count;
 UPDATE graph_taxonomy_nodes SET edge_count=edge_count+(SELECT COUNT(*) FROM (SELECT * FROM corpus_claim_graph_edges INDEXED BY corpus_claim_graph_edge_source_idx WHERE source_snapshot_id=NEW.snapshot_id UNION ALL SELECT * FROM corpus_claim_graph_edges INDEXED BY corpus_claim_graph_edge_target_idx WHERE target_snapshot_id=NEW.snapshot_id) e WHERE e.processor_version='evidence-knowledge-graph/4.0' AND e.project_id=NEW.project_id AND ((e.source_snapshot_id=NEW.snapshot_id AND EXISTS(SELECT 1 FROM graph_taxonomy_memberships m WHERE m.snapshot_id=e.target_snapshot_id AND m.level=NEW.level AND m.id=NEW.id)) OR (e.target_snapshot_id=NEW.snapshot_id AND EXISTS(SELECT 1 FROM graph_taxonomy_memberships m WHERE m.snapshot_id=e.source_snapshot_id AND m.level=NEW.level AND m.id=NEW.id)))) WHERE project_id=NEW.project_id AND level=NEW.level AND id=NEW.id;
END;
--> statement-breakpoint
CREATE TRIGGER graph_membership_delete AFTER DELETE ON graph_taxonomy_memberships BEGIN
UPDATE graph_taxonomy_nodes SET paper_count=paper_count-1,claim_count=claim_count-OLD.claim_count,
 primary_claim_count=primary_claim_count-OLD.is_primary*OLD.claim_count,processed_count=processed_count-OLD.processed_count
 WHERE project_id=OLD.project_id AND level=OLD.level AND id=OLD.id;
 DELETE FROM graph_taxonomy_nodes WHERE project_id=OLD.project_id AND level=OLD.level AND id=OLD.id AND paper_count=0;
END;
--> statement-breakpoint
CREATE TRIGGER graph_membership_update AFTER UPDATE ON graph_taxonomy_memberships BEGIN
UPDATE graph_taxonomy_nodes SET primary_claim_count=primary_claim_count+(NEW.is_primary-OLD.is_primary)*NEW.claim_count,
 processed_count=processed_count+NEW.processed_count-OLD.processed_count
 WHERE project_id=NEW.project_id AND level=NEW.level AND id=NEW.id;
END;
--> statement-breakpoint
CREATE TRIGGER graph_membership_edge_delete BEFORE DELETE ON graph_taxonomy_memberships BEGIN
UPDATE graph_taxonomy_nodes SET edge_count=edge_count-(SELECT COUNT(*) FROM (SELECT * FROM corpus_claim_graph_edges INDEXED BY corpus_claim_graph_edge_source_idx WHERE source_snapshot_id=OLD.snapshot_id UNION ALL SELECT * FROM corpus_claim_graph_edges INDEXED BY corpus_claim_graph_edge_target_idx WHERE target_snapshot_id=OLD.snapshot_id) e WHERE e.processor_version='evidence-knowledge-graph/4.0' AND e.project_id=OLD.project_id AND ((e.source_snapshot_id=OLD.snapshot_id AND EXISTS(SELECT 1 FROM graph_taxonomy_memberships m WHERE m.snapshot_id=e.target_snapshot_id AND m.level=OLD.level AND m.id=OLD.id)) OR (e.target_snapshot_id=OLD.snapshot_id AND EXISTS(SELECT 1 FROM graph_taxonomy_memberships m WHERE m.snapshot_id=e.source_snapshot_id AND m.level=OLD.level AND m.id=OLD.id)))) WHERE project_id=OLD.project_id AND level=OLD.level AND id=OLD.id;
END;
--> statement-breakpoint
CREATE TRIGGER graph_active_insert AFTER INSERT ON evidence_active_snapshots BEGIN
INSERT OR IGNORE INTO graph_read_counts(project_id) VALUES(NEW.project_id);
 INSERT INTO graph_active_claims SELECT d.snapshot_id,d.claim_id,d.project_id,d.created_at,c.completed_at
 FROM evidence_retrieval_documents d LEFT JOIN corpus_claim_graph_completions c ON c.snapshot_id=d.snapshot_id AND c.claim_id=d.claim_id AND c.processor_version='evidence-knowledge-graph/4.0' WHERE d.snapshot_id=NEW.snapshot_id;
 UPDATE graph_read_counts SET all_count=all_count+NEW.document_count,
 processed_count=processed_count+(SELECT COUNT(*) FROM graph_active_claims WHERE snapshot_id=NEW.snapshot_id AND completed_at IS NOT NULL),
 edge_count=edge_count+(SELECT COUNT(*) FROM (SELECT * FROM corpus_claim_graph_edges INDEXED BY corpus_claim_graph_edge_source_idx WHERE source_snapshot_id=NEW.snapshot_id UNION ALL SELECT * FROM corpus_claim_graph_edges INDEXED BY corpus_claim_graph_edge_target_idx WHERE target_snapshot_id=NEW.snapshot_id) e WHERE e.project_id=NEW.project_id AND e.processor_version='evidence-knowledge-graph/4.0' AND ((e.source_snapshot_id=NEW.snapshot_id AND EXISTS(SELECT 1 FROM evidence_active_snapshots a WHERE a.snapshot_id=e.target_snapshot_id)) OR (e.target_snapshot_id=NEW.snapshot_id AND EXISTS(SELECT 1 FROM evidence_active_snapshots a WHERE a.snapshot_id=e.source_snapshot_id)))),
 unclassified_papers=unclassified_papers+NOT EXISTS(SELECT 1 FROM openalex_paper_classifications WHERE snapshot_id=NEW.snapshot_id)
 WHERE project_id=NEW.project_id;
 INSERT INTO graph_taxonomy_memberships SELECT topic.snapshot_id,topic.project_id,'domain',topic.domain_id,topic.domain_name,NULL,MAX(topic.is_primary),active.created_at,active.document_count,(SELECT COUNT(*) FROM graph_active_claims c WHERE c.snapshot_id=active.snapshot_id AND c.completed_at IS NOT NULL) FROM openalex_paper_topics topic JOIN evidence_active_snapshots active ON active.snapshot_id=topic.snapshot_id WHERE topic.snapshot_id=NEW.snapshot_id GROUP BY topic.snapshot_id,topic.domain_id UNION ALL SELECT topic.snapshot_id,topic.project_id,'field',topic.field_id,topic.field_name,topic.domain_id,MAX(topic.is_primary),active.created_at,active.document_count,(SELECT COUNT(*) FROM graph_active_claims c WHERE c.snapshot_id=active.snapshot_id AND c.completed_at IS NOT NULL) FROM openalex_paper_topics topic JOIN evidence_active_snapshots active ON active.snapshot_id=topic.snapshot_id WHERE topic.snapshot_id=NEW.snapshot_id GROUP BY topic.snapshot_id,topic.field_id UNION ALL SELECT topic.snapshot_id,topic.project_id,'subfield',topic.subfield_id,topic.subfield_name,topic.field_id,MAX(topic.is_primary),active.created_at,active.document_count,(SELECT COUNT(*) FROM graph_active_claims c WHERE c.snapshot_id=active.snapshot_id AND c.completed_at IS NOT NULL) FROM openalex_paper_topics topic JOIN evidence_active_snapshots active ON active.snapshot_id=topic.snapshot_id WHERE topic.snapshot_id=NEW.snapshot_id GROUP BY topic.snapshot_id,topic.subfield_id UNION ALL SELECT topic.snapshot_id,topic.project_id,'topic',topic.topic_id,topic.display_name,topic.subfield_id,MAX(topic.is_primary),active.created_at,active.document_count,(SELECT COUNT(*) FROM graph_active_claims c WHERE c.snapshot_id=active.snapshot_id AND c.completed_at IS NOT NULL) FROM openalex_paper_topics topic JOIN evidence_active_snapshots active ON active.snapshot_id=topic.snapshot_id WHERE topic.snapshot_id=NEW.snapshot_id GROUP BY topic.snapshot_id,topic.topic_id;
END;
--> statement-breakpoint
CREATE TRIGGER graph_active_delete BEFORE DELETE ON evidence_active_snapshots BEGIN
UPDATE graph_read_counts SET all_count=all_count-OLD.document_count,
 processed_count=processed_count-(SELECT COUNT(*) FROM graph_active_claims WHERE snapshot_id=OLD.snapshot_id AND completed_at IS NOT NULL),
 edge_count=edge_count-(SELECT COUNT(*) FROM (SELECT * FROM corpus_claim_graph_edges INDEXED BY corpus_claim_graph_edge_source_idx WHERE source_snapshot_id=OLD.snapshot_id UNION ALL SELECT * FROM corpus_claim_graph_edges INDEXED BY corpus_claim_graph_edge_target_idx WHERE target_snapshot_id=OLD.snapshot_id) e WHERE e.project_id=OLD.project_id AND e.processor_version='evidence-knowledge-graph/4.0' AND ((e.source_snapshot_id=OLD.snapshot_id AND EXISTS(SELECT 1 FROM evidence_active_snapshots a WHERE a.snapshot_id=e.target_snapshot_id)) OR (e.target_snapshot_id=OLD.snapshot_id AND EXISTS(SELECT 1 FROM evidence_active_snapshots a WHERE a.snapshot_id=e.source_snapshot_id)))),
 unclassified_papers=unclassified_papers-NOT EXISTS(SELECT 1 FROM openalex_paper_classifications WHERE snapshot_id=OLD.snapshot_id)
 WHERE project_id=OLD.project_id;
 DELETE FROM graph_taxonomy_memberships WHERE snapshot_id=OLD.snapshot_id;
 DELETE FROM graph_active_claims WHERE snapshot_id=OLD.snapshot_id;
END;
--> statement-breakpoint
CREATE TRIGGER graph_completion_insert AFTER INSERT ON corpus_claim_graph_completions WHEN NEW.processor_version='evidence-knowledge-graph/4.0' AND EXISTS(SELECT 1 FROM graph_active_claims WHERE snapshot_id=NEW.snapshot_id AND claim_id=NEW.claim_id) BEGIN
UPDATE graph_active_claims SET completed_at=NEW.completed_at WHERE snapshot_id=NEW.snapshot_id AND claim_id=NEW.claim_id;
 UPDATE graph_read_counts SET processed_count=processed_count+1 WHERE project_id=NEW.project_id;
 UPDATE graph_taxonomy_memberships SET processed_count=processed_count+1 WHERE snapshot_id=NEW.snapshot_id;
END;
--> statement-breakpoint
CREATE TRIGGER graph_edge_insert AFTER INSERT ON corpus_claim_graph_edges WHEN NEW.processor_version='evidence-knowledge-graph/4.0' AND EXISTS(SELECT 1 FROM evidence_active_snapshots WHERE snapshot_id=NEW.source_snapshot_id) AND EXISTS(SELECT 1 FROM evidence_active_snapshots WHERE snapshot_id=NEW.target_snapshot_id) BEGIN
UPDATE graph_read_counts SET edge_count=edge_count+1 WHERE project_id=NEW.project_id;
 UPDATE graph_taxonomy_nodes SET edge_count=edge_count+1 WHERE project_id=NEW.project_id
 AND (level,id) IN (SELECT s.level,s.id FROM graph_taxonomy_memberships s JOIN graph_taxonomy_memberships t ON t.level=s.level AND t.id=s.id
 WHERE s.snapshot_id=NEW.source_snapshot_id AND t.snapshot_id=NEW.target_snapshot_id);
END;
--> statement-breakpoint
CREATE TRIGGER graph_classification_insert AFTER INSERT ON openalex_paper_classifications WHEN EXISTS(SELECT 1 FROM evidence_active_snapshots WHERE snapshot_id=NEW.snapshot_id) BEGIN
UPDATE graph_read_counts SET unclassified_papers=unclassified_papers-1 WHERE project_id=NEW.project_id;
END;
--> statement-breakpoint
CREATE TRIGGER graph_topic_insert AFTER INSERT ON openalex_paper_topics BEGIN
DELETE FROM graph_taxonomy_memberships WHERE snapshot_id=NEW.snapshot_id;
INSERT INTO graph_taxonomy_memberships SELECT topic.snapshot_id,topic.project_id,'domain',topic.domain_id,topic.domain_name,NULL,MAX(topic.is_primary),active.created_at,active.document_count,(SELECT COUNT(*) FROM graph_active_claims c WHERE c.snapshot_id=active.snapshot_id AND c.completed_at IS NOT NULL) FROM openalex_paper_topics topic JOIN evidence_active_snapshots active ON active.snapshot_id=topic.snapshot_id WHERE topic.snapshot_id=NEW.snapshot_id GROUP BY topic.snapshot_id,topic.domain_id UNION ALL SELECT topic.snapshot_id,topic.project_id,'field',topic.field_id,topic.field_name,topic.domain_id,MAX(topic.is_primary),active.created_at,active.document_count,(SELECT COUNT(*) FROM graph_active_claims c WHERE c.snapshot_id=active.snapshot_id AND c.completed_at IS NOT NULL) FROM openalex_paper_topics topic JOIN evidence_active_snapshots active ON active.snapshot_id=topic.snapshot_id WHERE topic.snapshot_id=NEW.snapshot_id GROUP BY topic.snapshot_id,topic.field_id UNION ALL SELECT topic.snapshot_id,topic.project_id,'subfield',topic.subfield_id,topic.subfield_name,topic.field_id,MAX(topic.is_primary),active.created_at,active.document_count,(SELECT COUNT(*) FROM graph_active_claims c WHERE c.snapshot_id=active.snapshot_id AND c.completed_at IS NOT NULL) FROM openalex_paper_topics topic JOIN evidence_active_snapshots active ON active.snapshot_id=topic.snapshot_id WHERE topic.snapshot_id=NEW.snapshot_id GROUP BY topic.snapshot_id,topic.subfield_id UNION ALL SELECT topic.snapshot_id,topic.project_id,'topic',topic.topic_id,topic.display_name,topic.subfield_id,MAX(topic.is_primary),active.created_at,active.document_count,(SELECT COUNT(*) FROM graph_active_claims c WHERE c.snapshot_id=active.snapshot_id AND c.completed_at IS NOT NULL) FROM openalex_paper_topics topic JOIN evidence_active_snapshots active ON active.snapshot_id=topic.snapshot_id WHERE topic.snapshot_id=NEW.snapshot_id GROUP BY topic.snapshot_id,topic.topic_id;
END;
--> statement-breakpoint
CREATE TRIGGER graph_completion_delete AFTER DELETE ON corpus_claim_graph_completions WHEN OLD.processor_version='evidence-knowledge-graph/4.0' AND EXISTS(SELECT 1 FROM graph_active_claims WHERE snapshot_id=OLD.snapshot_id AND claim_id=OLD.claim_id) BEGIN
UPDATE graph_active_claims SET completed_at=NULL WHERE snapshot_id=OLD.snapshot_id AND claim_id=OLD.claim_id;
 UPDATE graph_read_counts SET processed_count=processed_count-1 WHERE project_id=OLD.project_id;
 UPDATE graph_taxonomy_memberships SET processed_count=processed_count-1 WHERE snapshot_id=OLD.snapshot_id;
END;
--> statement-breakpoint
CREATE TRIGGER graph_edge_delete AFTER DELETE ON corpus_claim_graph_edges WHEN OLD.processor_version='evidence-knowledge-graph/4.0' AND EXISTS(SELECT 1 FROM evidence_active_snapshots WHERE snapshot_id=OLD.source_snapshot_id) AND EXISTS(SELECT 1 FROM evidence_active_snapshots WHERE snapshot_id=OLD.target_snapshot_id) BEGIN
UPDATE graph_read_counts SET edge_count=edge_count-1 WHERE project_id=OLD.project_id;
 UPDATE graph_taxonomy_nodes SET edge_count=edge_count-1 WHERE project_id=OLD.project_id
 AND (level,id) IN (SELECT s.level,s.id FROM graph_taxonomy_memberships s JOIN graph_taxonomy_memberships t ON t.level=s.level AND t.id=s.id
 WHERE s.snapshot_id=OLD.source_snapshot_id AND t.snapshot_id=OLD.target_snapshot_id);
END;
--> statement-breakpoint
CREATE TRIGGER graph_classification_delete AFTER DELETE ON openalex_paper_classifications WHEN EXISTS(SELECT 1 FROM evidence_active_snapshots WHERE snapshot_id=OLD.snapshot_id) BEGIN
UPDATE graph_read_counts SET unclassified_papers=unclassified_papers+1 WHERE project_id=OLD.project_id;
END;
--> statement-breakpoint
CREATE TRIGGER graph_topic_delete AFTER DELETE ON openalex_paper_topics BEGIN
DELETE FROM graph_taxonomy_memberships WHERE snapshot_id=OLD.snapshot_id;
INSERT INTO graph_taxonomy_memberships SELECT topic.snapshot_id,topic.project_id,'domain',topic.domain_id,topic.domain_name,NULL,MAX(topic.is_primary),active.created_at,active.document_count,(SELECT COUNT(*) FROM graph_active_claims c WHERE c.snapshot_id=active.snapshot_id AND c.completed_at IS NOT NULL) FROM openalex_paper_topics topic JOIN evidence_active_snapshots active ON active.snapshot_id=topic.snapshot_id WHERE topic.snapshot_id=OLD.snapshot_id GROUP BY topic.snapshot_id,topic.domain_id UNION ALL SELECT topic.snapshot_id,topic.project_id,'field',topic.field_id,topic.field_name,topic.domain_id,MAX(topic.is_primary),active.created_at,active.document_count,(SELECT COUNT(*) FROM graph_active_claims c WHERE c.snapshot_id=active.snapshot_id AND c.completed_at IS NOT NULL) FROM openalex_paper_topics topic JOIN evidence_active_snapshots active ON active.snapshot_id=topic.snapshot_id WHERE topic.snapshot_id=OLD.snapshot_id GROUP BY topic.snapshot_id,topic.field_id UNION ALL SELECT topic.snapshot_id,topic.project_id,'subfield',topic.subfield_id,topic.subfield_name,topic.field_id,MAX(topic.is_primary),active.created_at,active.document_count,(SELECT COUNT(*) FROM graph_active_claims c WHERE c.snapshot_id=active.snapshot_id AND c.completed_at IS NOT NULL) FROM openalex_paper_topics topic JOIN evidence_active_snapshots active ON active.snapshot_id=topic.snapshot_id WHERE topic.snapshot_id=OLD.snapshot_id GROUP BY topic.snapshot_id,topic.subfield_id UNION ALL SELECT topic.snapshot_id,topic.project_id,'topic',topic.topic_id,topic.display_name,topic.subfield_id,MAX(topic.is_primary),active.created_at,active.document_count,(SELECT COUNT(*) FROM graph_active_claims c WHERE c.snapshot_id=active.snapshot_id AND c.completed_at IS NOT NULL) FROM openalex_paper_topics topic JOIN evidence_active_snapshots active ON active.snapshot_id=topic.snapshot_id WHERE topic.snapshot_id=OLD.snapshot_id GROUP BY topic.snapshot_id,topic.topic_id;
END;
--> statement-breakpoint
CREATE TRIGGER graph_topic_update AFTER UPDATE ON openalex_paper_topics BEGIN
DELETE FROM graph_taxonomy_memberships WHERE snapshot_id=NEW.snapshot_id;
INSERT INTO graph_taxonomy_memberships SELECT topic.snapshot_id,topic.project_id,'domain',topic.domain_id,topic.domain_name,NULL,MAX(topic.is_primary),active.created_at,active.document_count,(SELECT COUNT(*) FROM graph_active_claims c WHERE c.snapshot_id=active.snapshot_id AND c.completed_at IS NOT NULL) FROM openalex_paper_topics topic JOIN evidence_active_snapshots active ON active.snapshot_id=topic.snapshot_id WHERE topic.snapshot_id=NEW.snapshot_id GROUP BY topic.snapshot_id,topic.domain_id UNION ALL SELECT topic.snapshot_id,topic.project_id,'field',topic.field_id,topic.field_name,topic.domain_id,MAX(topic.is_primary),active.created_at,active.document_count,(SELECT COUNT(*) FROM graph_active_claims c WHERE c.snapshot_id=active.snapshot_id AND c.completed_at IS NOT NULL) FROM openalex_paper_topics topic JOIN evidence_active_snapshots active ON active.snapshot_id=topic.snapshot_id WHERE topic.snapshot_id=NEW.snapshot_id GROUP BY topic.snapshot_id,topic.field_id UNION ALL SELECT topic.snapshot_id,topic.project_id,'subfield',topic.subfield_id,topic.subfield_name,topic.field_id,MAX(topic.is_primary),active.created_at,active.document_count,(SELECT COUNT(*) FROM graph_active_claims c WHERE c.snapshot_id=active.snapshot_id AND c.completed_at IS NOT NULL) FROM openalex_paper_topics topic JOIN evidence_active_snapshots active ON active.snapshot_id=topic.snapshot_id WHERE topic.snapshot_id=NEW.snapshot_id GROUP BY topic.snapshot_id,topic.subfield_id UNION ALL SELECT topic.snapshot_id,topic.project_id,'topic',topic.topic_id,topic.display_name,topic.subfield_id,MAX(topic.is_primary),active.created_at,active.document_count,(SELECT COUNT(*) FROM graph_active_claims c WHERE c.snapshot_id=active.snapshot_id AND c.completed_at IS NOT NULL) FROM openalex_paper_topics topic JOIN evidence_active_snapshots active ON active.snapshot_id=topic.snapshot_id WHERE topic.snapshot_id=NEW.snapshot_id GROUP BY topic.snapshot_id,topic.topic_id;
END;
--> statement-breakpoint
INSERT INTO graph_active_claims SELECT d.snapshot_id,d.claim_id,d.project_id,d.created_at,c.completed_at FROM evidence_retrieval_documents d JOIN evidence_active_snapshots a ON a.snapshot_id=d.snapshot_id LEFT JOIN corpus_claim_graph_completions c ON c.snapshot_id=d.snapshot_id AND c.claim_id=d.claim_id AND c.processor_version='evidence-knowledge-graph/4.0';
--> statement-breakpoint
INSERT INTO graph_read_counts SELECT a.project_id,SUM(a.document_count),
 (SELECT COUNT(*) FROM graph_active_claims c WHERE c.project_id=a.project_id AND c.completed_at IS NOT NULL),
 (SELECT COUNT(*) FROM corpus_claim_graph_edges e JOIN evidence_active_snapshots s ON s.snapshot_id=e.source_snapshot_id JOIN evidence_active_snapshots t ON t.snapshot_id=e.target_snapshot_id WHERE e.project_id=a.project_id AND e.processor_version='evidence-knowledge-graph/4.0'),
 SUM(NOT EXISTS(SELECT 1 FROM openalex_paper_classifications c WHERE c.snapshot_id=a.snapshot_id)) FROM evidence_active_snapshots a GROUP BY a.project_id;
--> statement-breakpoint
INSERT INTO graph_taxonomy_memberships SELECT topic.snapshot_id,topic.project_id,'domain',topic.domain_id,topic.domain_name,NULL,MAX(topic.is_primary),active.created_at,active.document_count,(SELECT COUNT(*) FROM graph_active_claims c WHERE c.snapshot_id=active.snapshot_id AND c.completed_at IS NOT NULL) FROM openalex_paper_topics topic JOIN evidence_active_snapshots active ON active.snapshot_id=topic.snapshot_id WHERE topic.snapshot_id=topic.snapshot_id GROUP BY topic.snapshot_id,topic.domain_id UNION ALL SELECT topic.snapshot_id,topic.project_id,'field',topic.field_id,topic.field_name,topic.domain_id,MAX(topic.is_primary),active.created_at,active.document_count,(SELECT COUNT(*) FROM graph_active_claims c WHERE c.snapshot_id=active.snapshot_id AND c.completed_at IS NOT NULL) FROM openalex_paper_topics topic JOIN evidence_active_snapshots active ON active.snapshot_id=topic.snapshot_id WHERE topic.snapshot_id=topic.snapshot_id GROUP BY topic.snapshot_id,topic.field_id UNION ALL SELECT topic.snapshot_id,topic.project_id,'subfield',topic.subfield_id,topic.subfield_name,topic.field_id,MAX(topic.is_primary),active.created_at,active.document_count,(SELECT COUNT(*) FROM graph_active_claims c WHERE c.snapshot_id=active.snapshot_id AND c.completed_at IS NOT NULL) FROM openalex_paper_topics topic JOIN evidence_active_snapshots active ON active.snapshot_id=topic.snapshot_id WHERE topic.snapshot_id=topic.snapshot_id GROUP BY topic.snapshot_id,topic.subfield_id UNION ALL SELECT topic.snapshot_id,topic.project_id,'topic',topic.topic_id,topic.display_name,topic.subfield_id,MAX(topic.is_primary),active.created_at,active.document_count,(SELECT COUNT(*) FROM graph_active_claims c WHERE c.snapshot_id=active.snapshot_id AND c.completed_at IS NOT NULL) FROM openalex_paper_topics topic JOIN evidence_active_snapshots active ON active.snapshot_id=topic.snapshot_id WHERE topic.snapshot_id=topic.snapshot_id GROUP BY topic.snapshot_id,topic.topic_id;
