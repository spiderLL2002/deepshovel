-- Compile state writes refresh library_artifact_projection. Cover its latest
-- snapshot lookup, including missing versions, without scanning project history
-- or reading the immutable graph payload table.
CREATE INDEX evidence_graph_project_version_latest_idx
ON evidence_graph_snapshots(project_id, root_artifact_version_id, created_at DESC, id DESC);
