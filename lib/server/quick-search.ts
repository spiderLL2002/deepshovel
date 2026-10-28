import ontology from "../../compiler/compiler_service/retrieval_ontology.json" with { type: "json" };

/** Local literal/curated-alias recall only; never a relevance or relation judgment. */
export function quickSearchGroups(query: string): string[][] {
  const text = query.toLowerCase();
  const groups = ontology.concepts.filter(({ labels }) => labels.some(label => {
    const value = label.toLowerCase();
    return /[\u3400-\u9fff]/u.test(value)
      ? text.includes(value)
      : new RegExp(`(?:^|[^a-z0-9])${value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?:$|[^a-z0-9])`, "u").test(text);
  })).map(({ labels }) => [...new Set(labels.map(label => label.toLowerCase()))]);
  const words = text.match(/[a-z0-9]+(?:-[a-z0-9]+)*|[\u3400-\u9fff]+/gu) ?? [];
  if (!groups.length) {
    for (const word of words) {
      if (/^[\u3400-\u9fff]+$/u.test(word)) {
        // One or two Chinese characters form a word of their own (汞, 华南);
        // longer runs match through their bigrams.
        if (word.length <= 2) groups.push([word]);
        else for (let i = 0; i < word.length - 1; i++) groups.push([word.slice(i, i + 2)]);
      } else if (word.length > 2) groups.push([word]);
    }
  } else {
    // Keep a short Chinese word that no matched concept covers (稀土 汞 → 汞).
    const labels = groups.flat();
    for (const word of words) {
      if (/^[\u3400-\u9fff]{1,2}$/u.test(word) && !labels.some(label => label.includes(word))) groups.push([word]);
    }
  }
  return groups.slice(0, 16);
}

export async function quickSearchRefs(DB: D1Database, projectId: string, snapshots: string[], query: string) {
  const groups = quickSearchGroups(query);
  if (!groups.length || !snapshots.length) return [];
  // Score each row once without materializing its text. Keep active IDs as the
  // outer loop: materialization alone can choose a corpus × active-ID scan.
  const result = await DB.prepare(`
    WITH active AS (SELECT value AS snapshot_id FROM json_each(?)),
    scored AS MATERIALIZED (
      SELECT d.snapshot_id, d.claim_id,
        (SELECT count(*) FROM json_each(?) g WHERE EXISTS (
          SELECT 1 FROM json_each(g.value) t WHERE instr(
            d.search_text,
            CASE WHEN length(t.value) <= 3 AND t.value NOT GLOB '*[^a-z0-9]*'
              THEN ' ' || t.value || ' ' ELSE t.value END) > 0
        )) AS score
      FROM active a CROSS JOIN evidence_quick_search d ON a.snapshot_id = d.snapshot_id
      WHERE d.project_id = ?
    ) SELECT snapshot_id, claim_id FROM scored WHERE score > 0
      ORDER BY score DESC, snapshot_id, claim_id LIMIT 30
  `).bind(JSON.stringify(snapshots), JSON.stringify(groups), projectId)
    .all<{ snapshot_id: string; claim_id: string }>();
  return result.results.map(row => ({ snapshotId: row.snapshot_id, claimId: row.claim_id }));
}
