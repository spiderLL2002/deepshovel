#!/usr/bin/env python3
"""Rebuild the bounded public trace from the author's saved research records.

No network access and no new scientific inference. Exact selected fields are
copied; source hashes identify the complete original files. Full publisher
PDFs, long quotations, opaque handoffs, and account metadata are not exported.
"""
from pathlib import Path
from collections import Counter
import argparse, hashlib, json, re

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

# Counts are derived from final root dispositions, not stale screening labels.
screened=[]
for cid, candidate in state['candidates'].items():
    match=re.fullmatch(r'(REE|OAE)-R([1-8])-0[1-3]',cid)
    if not match: raise ValueError('Unexpected screening candidate ID: '+cid)
    question_field='question' if 'question' in candidate else 'scientific_question'
    screened.append({'id':cid,'round_from_id':int(match.group(2)),
        'domain_from_id':match.group(1),'question_source_field':question_field,
        'question':candidate[question_field],
        'recorded_round_fields':pick(candidate,['round','decision_round']),
        'root_disposition':candidate['root_disposition'],
        'manuscript_eligible':candidate['manuscript_eligible']})
screened.sort(key=lambda x:(x['round_from_id'],x['id']))
round_counts=Counter(x['round_from_id'] for x in screened)
disposition_counts=Counter(x['root_disposition'] for x in screened)
manuscript=claims['manuscript']
data=(root/manuscript['path']).read_bytes()
if hashlib.sha256(data).hexdigest()!=manuscript['sha256']:
    raise ValueError('Scientific manuscript binding differs')
sources[manuscript['path']]=manuscript['sha256']
close8=next(x for x in state['decisions'] if x.get('round')==8 and x.get('action','').startswith('close_round'))
delivery8=next(x for x in state['decisions'] if x.get('round')==8 and x.get('action','').startswith('deliver_complete'))
put('records/eight-round-screening.json',{
    'record_type':'minimal publication projection of the author research state; not a service log or a prospective benchmark',
    'source_record':'state.json','source_sha256':sources['state.json'],
    'selection':'All candidate IDs matching (REE|OAE)-R[1-8]-0[1-3]; no candidate omitted',
    'candidate_count':len(screened),'round_counts':dict(sorted(round_counts.items())),
    'disposition_counts':dict(sorted(disposition_counts.items())),
    'candidates':screened,
    'recorded_gate_counts':pick(state['manuscript_gate'],['completed_rounds','candidate_count','eligible_count','candidate']),
    'round8_close':pick(close8,['at','round','action','dispositions','eligible','next_phase']),
    'initial_delivery_event':pick(delivery8,['at','round','action','scientific_expert_certification','submitted']),
    'later_scientific_manuscript':dict(manuscript,public_path='../gp15/scientific-manuscript.en.md',scientific_version=claims['scientific_version']),
    'interpretation':'48 screened candidate questions in eight rounds, one advanced to drafting and a delivered research manuscript. Six not-selected candidates remain distinct from rejection. This does not establish 48 completed analyses, 48 independent discoveries, journal acceptance, or the counterfactual benefit of DeepShovel.'})

review_path='reviews/round8-ree-transfer-preexecution-review.md'
review_data=(root/review_path).read_bytes();sources[review_path]=hashlib.sha256(review_data).hexdigest()
review_text=review_data.decode()
review_time=re.search(r'Recorded UTC: (\S+)',review_text).group(1)
review_rule=next(line for line in review_text.splitlines() if line.startswith('5. **Station sensitivity:**'))
review_receipt=load('reviews/round8-ree-transfer-preexecution-receipt.json')
draft=load('ree/round8/protocol-draft.json')
plan=load('plans/ree-ph-fixed-transfer-v1.json')
script_path='ree/round8/analyze_ree_ph_transfer.py'
script_bytes=(root/script_path).read_bytes();sources[script_path]=hashlib.sha256(script_bytes).hexdigest()
run_id,run=next((key,value) for key,value in state['runs'].items() if value['plan_id']=='ree-ph-fixed-transfer-v1')
result_root='results/ree_ph_fixed_transfer_v1/'
boot=load(result_root+'cluster_bootstrap.json')
loso=load(result_root+'leave_one_station_out.json')
run_manifest=load(result_root+'run_manifest.json')
output_paths=[result_root+n for n in ['cluster_bootstrap.json','leave_one_station_out.json','run_manifest.json']]
selected_outputs=[x for x in run['outputs'] if x['path'] in output_paths]
for x in selected_outputs:
    if sources[x['path']]!=x['sha256']: raise ValueError('Historical output differs: '+x['path'])
loso_projection={}
for leg,result in loso.items():
    cases=[]
    for case in result['cases']:
        projected=pick(case,['deleted_station','same_layer_support_as_point','missing_bin_indexes'])
        projected['layers']=[pick(layer,['bin_index','n_records','n_stations']) for layer in case['layers']]
        aggregate=case['aggregate_on_same_layer_support']
        projected['aggregate_on_same_layer_support']=None if aggregate is None else pick(aggregate,['n_nonempty_layers','nonempty_bin_indexes','n_supported_layers','full_domain_support'])
        cases.append(projected)
    loso_projection[leg]=dict(pick(result,['required_bin_indexes','rule']),cases=cases)
public_bindings={path:sources[path] for path in ['plans/ree-ph-fixed-transfer-v1.json',script_path,*output_paths]}
put('records/rsi-protocol-execution.json',{
    'record_type':'exact selected fields from historical AI research review, frozen protocol and recorded execution; not a new review of the WWW paper',
    'source_state_sha256':sources['state.json'],
    'review':{'source_record':review_path,'sha256':sources[review_path],'recorded_at_utc':review_time,
        'rule_excerpt':{'start_line':review_text[:review_text.index(review_rule)].count('\n')+1,'text':review_rule},
        'receipt':dict(pick(review_receipt,['review_type','decision','response_execution','leg2_sample_chemistry_read','report','report_sha256']),reviewed_sha256={k:review_receipt['reviewed_sha256'][k] for k in [script_path,'ree/round8/protocol-draft.json']})},
    'reviewed_draft':{'source_record':'ree/round8/protocol-draft.json','sha256':sources['ree/round8/protocol-draft.json'],**pick(draft,['frozen','support','bootstrap','leave_one_station_out'])},
    'frozen_protocol':{'source_record':'ree/round8/protocol-frozen-v1.json','sha256':sources['ree/round8/protocol-frozen-v1.json'],**pick(protocol,['candidate_id','frozen','frozen_at_utc','input_sha256','analysis_script_sha256','support','bootstrap','leave_one_station_out','preexecution_review'])},
    'freeze_event':pick(freeze8,['at','round','action','plan','review','protocol','first_response_access']),
    'execution':{'run_id':run_id,**pick(run,['plan_id','status','started_at','finished_at','exit_code','missing_outputs','stale_outputs']),
        'identity':pick(run['identity'],['plan','code']),'selected_output_bindings':selected_outputs},
    'run_manifest':pick(run_manifest,['input_sha256','protocol_sha256','analysis_script_sha256','n_eligible_pairs_in_depth_union','leg_counts','n_layer_rows','prediction_clipping','new_model_fit']),
    'bootstrap_outputs':{leg:pick(value,['n_stations','stations','required_bins','status','valid_replicates','attempted_replicates','valid_fraction','interval_scope']) for leg,value in boot.items()},
    'leave_one_station_out_outputs':loso_projection,
    'public_gp15_payload_bindings':public_bindings,
    'code_locators':{'source_record':script_path,'sha256':sources[script_path],
        'minimum_station_guard_line':294,'complete_draw_fraction_guard_line':309,'lost_layer_aggregate_guard_line':335},
    'causal_and_execution_limits':[
        'The saved preexecution review explicitly checks a revised rule. The original message first proposing these thresholds is not included in these records, so this trace does not assign first authorship or establish a before/after policy change.',
        'The review is hash-bound by the frozen protocol, and the recorded execution binds that protocol and code. Recorded chronology is not a controlled estimate of research-performance improvement.',
        'Both observed bootstrap runs met the thresholds. All 14 observed leave-one-station-out cases retained their original nonempty layers; the missing-layer suppression branch was implemented but not triggered in this run.',
        'These are conditional station-resampling rules, not proof of nominal confidence coverage or independent laboratory replication.'
    ]})
put('source-record-hashes.json',{'scope':'Hashes of complete historical author records; these are distinct from hashes of the public projections. Original copyrighted PDFs are not redistributed here.','files':sources})
print(f'Wrote bounded trace to {out}; {len(sources)} original records bound.')
