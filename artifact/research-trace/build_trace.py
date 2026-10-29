#!/usr/bin/env python3
"""Rebuild the bounded public trace from the author's saved research records.

No network access and no new scientific inference. Exact selected fields are
copied; source hashes identify the complete original files. Full publisher
PDFs, long quotations, opaque handoffs, and account metadata are not exported.
"""
from pathlib import Path
import argparse, hashlib, json

parser = argparse.ArgumentParser()
parser.add_argument('--source-root', type=Path, required=True)
parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent)
a = parser.parse_args()
root, out = a.source_root.resolve(), a.output.resolve()
(out/'records').mkdir(parents=True, exist_ok=True)
sources = {}
def load(rel):
    p = root / rel
    sources[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return json.loads(p.read_text())
def put(name, data):
    (out/name).write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')
def pick(obj, keys):
    return {k: obj[k] for k in keys if k in obj}
def record_receipt(rel):
    d=load(rel)
    return dict(source_record=rel, **pick(d, ['method','url','requested_at','complete','http_status','effective_status','response_sha256','response_bytes','artifact_sha_verified']))

r7 = 'deepshovel/round7/'
q=load(r7+'hydrothermal-search-request.json')
r=load(r7+'hydrothermal-rerank.json')
c=next(c for c in r['clusters'] if 'Hongo' in c['representative']['source']['artifactName'])
rep=c['representative']
binding=load(r7+'hongo-source-binding.json')
source_claim=next(v for v in binding['claims'] if v['id']==rep['claim']['id'])
anchors=[]
for x in binding['anchors']:
    if x['id'] in ['anchor.figure.12','anchor.text.mix.model.mg','anchor.text.mix.mechanism']:
        anchors.append({'id':x['id'],'artifact_version_id':x['artifact_version_id'],'anchor_type':x['anchor_type'],'selector':pick(x['selector'],['page','figure','section'])})
paths=pick(rep['shortestEvidencePath'],['rootClaimId','truncated','depth'])
paths['nodes']=[pick(n,['id','kind']) for n in rep['shortestEvidencePath']['nodes']]
paths['links']=[pick(e,['fromId','toId','relation','linkType']) for e in rep['shortestEvidencePath']['links']]
hongo={'record_type':'selected saved retrieval and source-graph fields; not a new service response',
       'source_doi':'https://doi.org/10.2343/geochemj.41.1',
       'request':q,'retrieval':{'ok':r['ok'],'stage':r['stage'],'resultStatus':r['resultStatus'],
       'cluster_id':c['id'],'cluster_relation':c['relation'],'distinctPaperFamilies':c['distinctPaperFamilies'],
       'representative':dict(pick(rep,['snapshotId','paperId','paperFamilyId']),source=pick(rep['source'],['artifactId','artifactName','artifactVersionId','versionNumber','mediaType']),claim=pick(rep['claim'],['id','canonicalClaim','claimType','attribution','rhetoricalRole','scope']),queryRelation=rep['queryRelation'],shortestEvidencePath=paths)},
       'source_binding':pick(binding,['artifact_id','artifact_version_id','original_sha256','snapshot_id','graph_sha256']),
       'source_claim':pick(source_claim,['id','source_id','proposition','qualifiers','scope']), 'selected_anchors_without_quotations':anchors,
       'interpretation_boundary':'The returned supports/overlap label concerns a source-screening question. It does not establish a new result, causal identification, or calibrated correctness. The original-source check did not adopt the model-generated Ce-conflict claim as a scientific discovery.'}
put('records/hongo-retrieval.json',hongo)

candidates=load('ree/round7/candidates.json')
cand=next(x for x in candidates['candidates'] if x['id']=='REE-R7-02')
state=load('state.json')
close7=next(x for x in state['decisions'] if x.get('round')==7 and x.get('action','').startswith('close_round'))
sel8=next(x for x in state['decisions'] if x.get('round')==8 and x.get('action','').startswith('register_exactly'))
freeze8=next(x for x in state['decisions'] if x.get('round')==8 and x.get('action')=='freeze_first_deep_analysis')
put('records/hongo-decision.json',{'record_type':'exact selected historical decision fields','candidate_record_created_at':candidates['created_at'],'candidate_record_updated_at':candidates['updated_at'],'candidate':pick(cand,['id','question','competing_explanations','discriminating_observations','potential_increment','status','reasons','writing_eligible','effect_calculation_executed']),'round_close':close7,'causal_limit':'The contemporary notes say the DeepShovel-bound source strengthens an existing rejection. They do not establish a counterfactual acceptance without DeepShovel.'})
receipts=[record_receipt(r7+'search-http/responses/'+key+'/receipt.json') for key in ['f54edf1166b864429295a8fcc13ef93360a0766e411a731da63ce65c531fbc85','dbf961d00151b3afdb90bfc76206ac1ef69734e716e56eb86a68b59193777a80']]
receipts += [record_receipt(r7+'chain-http/responses/'+key+'/receipt.json') for key in ['5f32b13f93052ea9e6454c14479edcc625ec5a2431f57d75aa330bd90338e850','79d95c516414b0b69b2a1c60e5f9ce124f206b1d56f4ef4e56f46b0413d8d3af','b7b124771d4dc782838657d08f2f77ce688470330ab866096bf6bf7d4265a386','60728cb89db801124b970f27ca10a474c0b9c6a93e06cefec799d40e239a9412']]
put('records/hongo-receipts.json',receipts)

r8='deepshovel/round8/'
lin=load(r8+'lin2024-source-binding.json')
linclaim=next(c for c in lin['claims'] if c['id'].startswith('claim:sha256:5fda7f389'))
inputs=load('ree/round8/transfer-source-input.json')
author=next(x for x in inputs['sources'] if x['role']=='author_paper')
protocol=load('ree/round8/protocol-frozen-v1.json')
claims=load('iteration-2026-10-27/manuscript/claim-source-map.final.json')
selected=[x for x in claims['claims'] if x['claim_id'] in ['C01','C03','C12']]
linanchors=[{'id':x['id'],'artifact_version_id':x['artifact_version_id'],'selector':pick(x['selector'],['page','figure','section'])} for x in lin['anchors'] if x['id']=='anchor.figure.3']
for c in selected:
    for src in c['bound_sources']:
        p=root/src['path']
        if not p.exists(): raise FileNotFoundError(p)
        actual=hashlib.sha256(p.read_bytes()).hexdigest()
        if actual!=src['sha256']: raise ValueError(f"Stored claim-source hash differs: {src['path']}")
        sources[src['path']]=actual
linreceipt=record_receipt(r8+'http/responses/6aa8835ad8e197cf7fca04fa77120ae50c5b75b8e876b9fe4a5140f02ababc10/receipt.json')
put('gp15-source-lineage.json',{'record_type':'retrospective publication index assembled from existing identity fields; not an automatic graph writeback log',
  'original_source_doi':'https://doi.org/10.2475/001c.118215',
  'deepshovel_source':dict(pick(lin,['artifact_id','artifact_version_id','original_sha256','snapshot_id','graph_sha256']),selected_claim=pick(linclaim,['id','source_id','proposition','qualifiers','scope']),selected_anchors=linanchors,artifact_receipt=linreceipt),
  'research_input':{'source_input_record':'ree/round8/transfer-source-input.json','source_input_sha256':sources['ree/round8/transfer-source-input.json'],'author_paper':author},
  'frozen_protocol':pick(protocol,['schema_version','candidate_id','frozen','frozen_at_utc','input_sha256','scientific_target','model','primary','support','bootstrap','leave_one_station_out']),
  'state_events':[sel8,freeze8],
  'final_claim_source_map':{'schema':claims['schema'],'scientific_version':claims['scientific_version'],'claims':selected},
  'mapping_semantics':{'source_to_research':'Exact original PDF identity shared by DeepShovel artifact/version and the frozen research input. Figure/claim locators guide source inspection; the published fixed implementation defines the model.',
     'inherited':'Published model definition, calibration depth/aggregation, source interpretation and caveats.',
     'new_analysis_inputs':'GP15 parent-bottle matching, eligibility/support rules, station grouping, fixed comparator evaluation and local result tables.',
     'new_claims':'C03 empirical RMSE result; C12 conditional nonnegative-depth-weight deduction. Neither is a claim that existed in the original source graph.',
     'not_demonstrated':'Automatic insertion of C03/C12 into the production source graph, automatic edge-level mapping, subsequent network retrieval of those inserted nodes, or a causal accuracy gain from DeepShovel.'}})

snippets=[]
for rel, wanted in [('ree/round7/REPORT.md',['A later DeepShovel']),('ree/round7/HONGO-NEAREST-CHECK.md',['**Direct overlap','Methods constrain','The graph\'s pre-existing'])]:
    data=(root/rel).read_bytes();sources[rel]=hashlib.sha256(data).hexdigest();txt=data.decode()
    for para in txt.split('\n\n'):
        if any(para.startswith(w) for w in wanted):
            line=txt[:txt.index(para)].count('\n')+1
            snippets.append({'source_record':rel,'start_line':line,'text':para})
put('records/historical-source-check-excerpts.json',snippets)
put('source-record-hashes.json',{'scope':'Hashes of complete historical author records; these are distinct from hashes of the public projections. Original copyrighted PDFs are not redistributed here.','files':sources})
print(f'Wrote bounded trace to {out}; {len(sources)} original records bound.')
