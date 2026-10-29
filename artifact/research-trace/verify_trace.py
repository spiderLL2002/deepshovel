#!/usr/bin/env python3
"""Check identities and recorded chronology, not scientific truth or causal gain."""
from pathlib import Path
from datetime import datetime
import argparse, hashlib, json, subprocess, sys, tempfile

p=argparse.ArgumentParser()
p.add_argument('--source-root', type=Path, help='Optional original author workspace for byte/field checks.')
p.add_argument('--gp15-payload', type=Path, help='Public GP15 payload; defaults to the sibling package if present.')
a=p.parse_args()
b=Path(__file__).resolve().parent
load=lambda path: json.loads((b/path).read_text())
check={}
def require(name, condition):
    if not condition: raise AssertionError(name)
    check[name]=True
h=load('records/hongo-retrieval.json');d=load('records/hongo-decision.json');rs=load('records/hongo-receipts.json')
rep=h['retrieval']['representative'];binding=h['source_binding']
require('Hongo retrieved claim equals bound source claim',rep['claim']['id']==h['source_claim']['id'])
require('Hongo snapshot identity preserved',rep['snapshotId']==binding['snapshot_id'])
require('Hongo artifact version identity preserved',rep['source']['artifactVersionId']==binding['artifact_version_id']=='artifact-version:sha256:'+binding['original_sha256'])
require('Original artifact download hash matches binding',rs[-1]['response_sha256']==binding['original_sha256'] and rs[-1]['artifact_sha_verified'])
require('Figure 12 locator retained',any(x['id']=='anchor.figure.12' and x['selector']['page']==13 for x in h['selected_anchors_without_quotations']))
times=[datetime.fromisoformat(x['requested_at']) for x in rs]
require('Recorded requests precede candidate update and round close',max(times)<datetime.fromisoformat(d['candidate_record_updated_at'])<datetime.fromisoformat(d['round_close']['at']))
require('Candidate decision explicitly cites DeepShovel-bound source',any('DeepShovel-bound Hongo2007' in x for x in d['candidate']['reasons']))
require('Final disposition retained',d['round_close']['dispositions']['REE-R7-02']=='rejected_current_form' and d['candidate']['effect_calculation_executed'] is False)
g=load('gp15-source-lineage.json');s=g['deepshovel_source'];ri=g['research_input'];fp=g['frozen_protocol']
require('Lin original PDF shared by service and research input',s['original_sha256']==ri['author_paper']['sha256']==s['artifact_receipt']['response_sha256'])
require('Frozen protocol binds the exact research source input',fp['input_sha256']==ri['source_input_sha256'])
require('Lin source retrieved before protocol freeze',datetime.fromisoformat(s['artifact_receipt']['requested_at'])<datetime.fromisoformat(fp['frozen_at_utc']))
require('C01 C03 C12 distinct research claims retained',[x['claim_id'] for x in g['final_claim_source_map']['claims']]==['C01','C03','C12'])
sha=load('source-record-hashes.json')['files']
for claim in g['final_claim_source_map']['claims']:
    for x in claim['bound_sources']:
        require('Recorded source hash '+claim['claim_id']+' '+x['path'],sha.get(x['path'])==x['sha256'])
payload = a.gp15_payload or b.parent/'gp15/reproduction/payload'
public_count=0
if payload.exists():
    bound={'ree/round8/transfer-source-input.json':ri['source_input_sha256'], 'ree/round8/protocol-frozen-v1.json':sha['ree/round8/protocol-frozen-v1.json'], 'ree/round8/'+ri['author_paper']['path']:ri['author_paper']['sha256']}
    for claim in g['final_claim_source_map']['claims']:
        for item in claim['bound_sources']: bound[item['path']]=item['sha256']
    for path,expected in bound.items():
        require('Public GP15 bytes '+path,hashlib.sha256((payload/path).read_bytes()).hexdigest()==expected)
    public_count=len(bound)
elif a.gp15_payload:
    raise FileNotFoundError(payload)
if a.source_root:
    for path,expected in sha.items():
        require('Original bytes '+path,hashlib.sha256((a.source_root/path).read_bytes()).hexdigest()==expected)
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([sys.executable,'-B',str(b/'build_trace.py'),'--source-root',str(a.source_root),'--output',tmp],check=True,stdout=subprocess.DEVNULL)
        for q in Path(tmp).rglob('*.json'):
            require('Projection fields '+str(q.relative_to(tmp)),q.read_bytes()==(b/q.relative_to(tmp)).read_bytes())
print(json.dumps({'status':'pass','checks':len(check),'original_files_checked':len(sha) if a.source_root else 0,'public_gp15_files_checked':public_count,'meaning':'Record identity, extracted-field fidelity when originals supplied, and chronology checks only; not a scientific correctness, counterfactual utility, or production replay benchmark.','results':check},ensure_ascii=False,indent=2))
