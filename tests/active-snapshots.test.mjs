import assert from 'node:assert/strict';
import fs from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import test from 'node:test';

const migration = fs.readFileSync(new URL('../drizzle/0019_graph_active_snapshots.sql', import.meta.url), 'utf8');
const version = 'evidence-retrieval/2.0';
const columns = 'snapshot_id, project_id, index_version, paper_id, paper_family_id, document_count, created_at';
const expected = `WITH families AS (
 SELECT *,ROW_NUMBER() OVER(PARTITION BY project_id,index_version,paper_family_id
 ORDER BY julianday(created_at) DESC,created_at DESC,snapshot_id DESC) AS family_rank
 FROM evidence_retrieval_manifests WHERE index_version='${version}'
), papers AS (
 SELECT *,ROW_NUMBER() OVER(PARTITION BY project_id,index_version,paper_id
 ORDER BY julianday(created_at) DESC,created_at DESC,snapshot_id DESC) AS paper_rank
 FROM families WHERE family_rank=1
) SELECT ${columns} FROM papers WHERE paper_rank=1 ORDER BY project_id,paper_id`;
function fixture(initial = []) {
 const db = new DatabaseSync(':memory:');
 db.exec(`CREATE TABLE evidence_retrieval_manifests (
 snapshot_id TEXT PRIMARY KEY,project_id TEXT,index_version TEXT,paper_id TEXT,paper_family_id TEXT,
 document_count INTEGER,created_at TEXT,manifest_json TEXT);
 CREATE TABLE transitions(snapshot_id TEXT,delta INTEGER);`);
 const insert = db.prepare(`INSERT INTO evidence_retrieval_manifests VALUES(?,?,?,?,?,?,?,?)`);
 for (const row of initial) insert.run(...row, '{}');
 db.exec(migration);
 db.exec(`CREATE TRIGGER observe_insert AFTER INSERT ON evidence_active_snapshots BEGIN
 INSERT INTO transitions VALUES(NEW.snapshot_id,1); END;
 CREATE TRIGGER observe_delete BEFORE DELETE ON evidence_active_snapshots BEGIN
 INSERT INTO transitions VALUES(OLD.snapshot_id,-1); END;`);
 return { db, insert: row => insert.run(...row, '{}') };
}
function verify(db) {
 assert.deepEqual(db.prepare(`SELECT ${columns} FROM evidence_active_snapshots ORDER BY project_id,paper_id`).all(), db.prepare(expected).all());
}

test('active metadata matches family-then-paper selection through ties, zero Claims, backfill and deletes', () => {
 const old = ['s0','p',version,'doi:one','family-a',4,'2026-09-01T00:00:00Z'];
 const {db,insert} = fixture([old]);
 try {
 verify(db);
 // Same instant in different date formats preserves the previous textual tie-break.
 insert(['s1','p',version,'doi:one','family-a',0,'2026-09-01T08:00:00+08:00']);
 verify(db);
 assert.equal(db.prepare('SELECT document_count AS n FROM evidence_active_snapshots').get().n, 0);
 insert(['s2','p',version,'doi:one','family-b',5,'2026-09-02']);
 insert(['s3','p',version,'doi:two','family-b',3,'2026-09-03']);
 insert(['s4','other',version,'doi:one','family-a',2,'2026-09-04']);
 insert(['s5','p','evidence-retrieval/future','doi:one','family-a',1,'2027-01-01']);
 verify(db);
 db.exec("DELETE FROM evidence_retrieval_manifests WHERE snapshot_id='s3'");
 verify(db);
 db.exec("DELETE FROM evidence_retrieval_manifests WHERE snapshot_id='s0'");
 verify(db);
 // Recomputing an unchanged winner emits no deactivate/reactivate transition.
 db.exec('DELETE FROM transitions');
 insert(['historical','p',version,'doi:one','family-a',7,'2020-01-01']);
 assert.equal(db.prepare('SELECT COUNT(*) AS n FROM transitions').get().n, 0);
 db.exec("DELETE FROM evidence_retrieval_manifests WHERE snapshot_id='historical'");
 assert.equal(db.prepare('SELECT COUNT(*) AS n FROM transitions').get().n, 0);
 db.exec('DELETE FROM evidence_retrieval_manifests');
 verify(db);
 assert.equal(db.prepare('SELECT COUNT(*) AS n FROM evidence_family_snapshots').get().n, 0);
 } finally {db.close();}
});

test('incremental activation agrees with the original window query after each interleaved write', () => {
 const {db,insert} = fixture();
 let seed = 991;
 const random = max => { seed=(Math.imul(seed,1664525)+1013904223)>>>0; return seed%max; };
 try {
 for(let i=0;i<250;i++) {
 insert([`s${i}`,`project${random(3)}`,version,`paper${random(13)}`,`family${random(27)}`,random(10),`2026-09-${String(1+random(28)).padStart(2,'0')}`]);
 if(i%3===0) db.prepare('DELETE FROM evidence_retrieval_manifests WHERE snapshot_id=?').run(`s${random(i+1)}`);
 verify(db);
 }
 const plan=db.prepare(`EXPLAIN QUERY PLAN SELECT snapshot_id FROM evidence_retrieval_manifests
 WHERE project_id=? AND index_version=? AND paper_family_id=?
 ORDER BY julianday(created_at) DESC,created_at DESC,snapshot_id DESC LIMIT 1`).all('p',version,'f');
 assert.match(JSON.stringify(plan), /evidence_manifest_family_winner_idx/);
 assert.doesNotMatch(JSON.stringify(plan), /TEMP B-TREE|SCAN evidence_retrieval_manifests/);
 } finally {db.close();}
});
