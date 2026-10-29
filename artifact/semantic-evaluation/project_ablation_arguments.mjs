import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const repo = process.env.DEEPSHOVEL_REPO;
if (!repo) throw new Error('Set DEEPSHOVEL_REPO to the production source checkout');
const { parseEvidenceGraph } = await import(pathToFileURL(resolve(repo, 'lib/evidence-graph/index.ts')).href);
const { createRetrievalArgumentProjector } = await import(pathToFileURL(resolve(repo, 'lib/evidence-retrieval/arguments.ts')).href);
const input = JSON.parse(await readFile(process.argv[2], 'utf8'));
const sourceDir = process.argv[3];
const result = {};
for (const c of input.cases) {
  if (result[c.source]) continue;
  const source = JSON.parse(await readFile(resolve(sourceDir, `${c.source}-graph.json`), 'utf8'));
  const graph = await parseEvidenceGraph(source.graph);
  const claim = graph.claims.find(x => x.id === c.claim_id);
  if (!claim) throw new Error(`Missing designated claim: ${c.id}`);
  result[c.source] = {
    claim: claim.proposition,
    scope: claim.scope,
    argument: createRetrievalArgumentProjector(graph)(c.snapshot_id, c.claim_id),
  };
}
process.stdout.write(JSON.stringify(result));
