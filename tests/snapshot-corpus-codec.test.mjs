import assert from 'node:assert/strict';
import test from 'node:test';
import {encodeActiveSnapshotIds, MAX_CORPUS_SNAPSHOTS} from '../lib/evidence-retrieval/contract.ts';

const ids = count => Array.from({length:count}, (_,i)=>`snapshot:sha256:${i.toString(16).padStart(64,'0')}`);
test('corpus codec retains small arrays and rejects oversized, duplicate and noncanonical large inputs',()=>{
  const small=ids(4096);
  assert.deepEqual(encodeActiveSnapshotIds(small),small);
  assert.throws(()=>encodeActiveSnapshotIds(ids(MAX_CORPUS_SNAPSHOTS + 1)),new RegExp(String(MAX_CORPUS_SNAPSHOTS),'u'));
  assert.throws(()=>encodeActiveSnapshotIds([...small,small[0]]),/unique/u);
  assert.throws(()=>encodeActiveSnapshotIds([...small,'snapshot-not-canonical']),/canonical/u);
  const large=ids(5000).reverse();
  const encoded=encodeActiveSnapshotIds(large);
  const bytes=Buffer.from(encoded.slice('sha256-v1:'.length),'base64url');
  assert.equal(bytes.length,5000*32);
  assert.equal(`snapshot:sha256:${bytes.subarray(0,32).toString('hex')}`,large[0]);
  assert.equal(`snapshot:sha256:${bytes.subarray(-32).toString('hex')}`,large.at(-1));
});
