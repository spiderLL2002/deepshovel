#!/usr/bin/env python3
"""Check identities and recorded chronology, not scientific truth or causal gain."""
from pathlib import Path
from datetime import datetime
from collections import Counter
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
census=load('records/eight-round-screening.json')
rows=census['candidates']
require('All 48 screening IDs are unique',len(rows)==len({x['id'] for x in rows})==48)
round_counts=Counter(x['round_from_id'] for x in rows)
require('Eight rounds contain exactly six candidates each',dict(round_counts)=={i:6 for i in range(1,9)})
require('Stored round counts recompute from candidates',census['round_counts']=={str(k):v for k,v in sorted(round_counts.items())})
require('Every round has three candidates in each domain',all(sum(x['round_from_id']==i and x['domain_from_id']==domain for x in rows)==3 for i in range(1,9) for domain in ['REE','OAE']))
require('Candidate question and recorded round fields retained',all(x['question'] and all(v==x['round_from_id'] for v in x['recorded_round_fields'].values()) for x in rows))
dispositions=Counter(x['root_disposition'] for x in rows)
require('Final dispositions recompute independently of old screening labels',dict(dispositions)==census['disposition_counts'])
require('Six unselected candidates are separate from 41 rejected forms',dispositions=={'rejected_current_form':35,'rejected_current_form_after_frozen_analysis':6,'not_selected_data_or_novelty_gate_incomplete':6,'eligible_to_draft':1})
eligible=[x['id'] for x in rows if x['manuscript_eligible']]
require('Only REE-R8-01 advanced to drafting',eligible==['REE-R8-01'] and census['round8_close']['dispositions']['REE-R8-01']=='eligible_to_draft' and census['round8_close']['eligible']==1)
require('Census agrees with recorded manuscript gate',census['recorded_gate_counts']=={'completed_rounds':8,'candidate_count':48,'eligible_count':1,'candidate':'REE-R8-01'})
require('Census source is the original state hash',census['source_sha256']==sha['state.json'])
u=load('records/rsi-protocol-execution.json');review=u['review'];draft=u['reviewed_draft'];protocol=u['frozen_protocol'];execution=u['execution']
require('Preexecution review hash bound by frozen protocol',review['sha256']==protocol['preexecution_review']['sha256']==review['receipt']['report_sha256']==sha[review['source_record']])
require('Reviewed draft hash matches receipt',draft['sha256']==review['receipt']['reviewed_sha256'][draft['source_record']]==sha[draft['source_record']])
require('Reviewed draft and frozen rules agree',all(draft[k]==protocol[k] for k in ['support','bootstrap','leave_one_station_out']) and draft['frozen'] is False and protocol['frozen'] is True)
require('Review records no response execution',review['receipt']['response_execution'] is False and review['receipt']['leg2_sample_chemistry_read'] is False)
require('Review precedes freeze and actual execution',datetime.fromisoformat(review['recorded_at_utc'])<datetime.fromisoformat(protocol['frozen_at_utc'])<=datetime.fromisoformat(u['freeze_event']['at'])<datetime.fromisoformat(execution['started_at'])<datetime.fromisoformat(execution['finished_at']))
require('Six-station and 90-percent thresholds are exact frozen fields',protocol['support']['min_stations_for_cluster_interval']==6 and protocol['support']['min_valid_bootstrap_fraction']==0.9)
require('Frozen deletion rule preserves denominator',protocol['leave_one_station_out']['report_all_six_layers'] and 'suppress aggregate comparison' in protocol['leave_one_station_out']['missing_original_nonempty_layer'])
require('Recorded execution succeeded with no missing or stale outputs',execution['status']=='SUCCEEDED' and execution['exit_code']==0 and execution['missing_outputs']==[] and execution['stale_outputs']==[])
run_code={x['path']:x['sha256'] for x in execution['identity']['code']}
require('Executed code and protocol are the reviewed frozen identities',run_code[protocol['source_record']]==protocol['sha256']==sha[protocol['source_record']] and run_code[u['code_locators']['source_record']]==protocol['analysis_script_sha256']==review['receipt']['reviewed_sha256'][u['code_locators']['source_record']])
require('Execution plan equals recorded freeze binding',execution['identity']['plan']==u['freeze_event']['plan']['sha256']==sha[u['freeze_event']['plan']['path']])
require('Output manifest binds the same input protocol and code',all(u['run_manifest'][k]==protocol[k] for k in ['input_sha256','analysis_script_sha256']) and u['run_manifest']['protocol_sha256']==protocol['sha256'])
for item in execution['selected_output_bindings']:
    require('Historical output hash '+item['path'],item['sha256']==sha[item['path']]==u['public_gp15_payload_bindings'][item['path']])
loso_total=0;missing_support_cases=0
for leg in ['leg1','leg2']:
    boot=u['bootstrap_outputs'][leg]
    require(leg+' actual bootstrap support and fraction',boot['n_stations']==len(boot['stations']) and boot['attempted_replicates']==protocol['bootstrap']['replicates'] and boot['valid_fraction']==boot['valid_replicates']/boot['attempted_replicates'])
    require(leg+' observed bootstrap meets frozen reporting gates',boot['n_stations']>=protocol['support']['min_stations_for_cluster_interval'] and boot['valid_fraction']>=protocol['support']['min_valid_bootstrap_fraction'] and boot['status']=='conditional_station_resampling_interval_available')
    deletion=u['leave_one_station_out_outputs'][leg]
    require(leg+' one deletion per recorded station',{x['deleted_station'] for x in deletion['cases']}==set(boot['stations']) and len(deletion['cases'])==len(boot['stations']))
    for case in deletion['cases']:
        required=set(deletion['required_bin_indexes'])
        present={x['bin_index'] for x in case['layers'] if x['n_records']}
        same=present==required
        require(leg+' deletion '+case['deleted_station']+' preserves explicit support and aggregation rule',len(case['layers'])==6 and {x['bin_index'] for x in case['layers']}==set(range(6)) and case['same_layer_support_as_point']==same and case['missing_bin_indexes']==sorted(required-present) and (case['aggregate_on_same_layer_support'] is not None)==same)
        loso_total+=1;missing_support_cases+=not same
require('Fourteen observed deletions did not trigger missing-layer suppression',loso_total==14 and missing_support_cases==0)
require('Historical valid draws are 975 and 978 of 1000',[u['bootstrap_outputs'][leg]['valid_replicates'] for leg in ['leg1','leg2']]==[975,978])
payload = a.gp15_payload or b.parent/'gp15/reproduction/payload'
public_count=0
if payload.exists():
    bound={'ree/round8/transfer-source-input.json':ri['source_input_sha256'], 'ree/round8/protocol-frozen-v1.json':sha['ree/round8/protocol-frozen-v1.json'], 'ree/round8/'+ri['author_paper']['path']:ri['author_paper']['sha256']}
    for claim in g['final_claim_source_map']['claims']:
        for item in claim['bound_sources']: bound[item['path']]=item['sha256']
    bound.update(u['public_gp15_payload_bindings'])
    for path,expected in bound.items():
        require('Public GP15 bytes '+path,hashlib.sha256((payload/path).read_bytes()).hexdigest()==expected)
    public_count=len(bound)
    manuscript=census['later_scientific_manuscript']
    require('Public scientific manuscript equals the recorded p20 output',hashlib.sha256((b/manuscript['public_path']).read_bytes()).hexdigest()==manuscript['sha256'])
    public_count+=1
    code_lines=(payload/u['code_locators']['source_record']).read_text().splitlines()
    for name,needle in [('minimum_station_guard_line',"cfg['support']['min_stations_for_cluster_interval']"),('complete_draw_fraction_guard_line',"cfg['support']['min_valid_bootstrap_fraction']"),('lost_layer_aggregate_guard_line','aggregate(layers) if complete else None')]:
        require('Frozen executable guard '+name,needle in code_lines[u['code_locators'][name]-1])
elif a.gp15_payload:
    raise FileNotFoundError(payload)
if a.source_root:
    for path,expected in sha.items():
        require('Original bytes '+path,hashlib.sha256((a.source_root/path).read_bytes()).hexdigest()==expected)
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([sys.executable,'-B',str(b/'build_trace.py'),'--source-root',str(a.source_root),'--output',tmp],check=True,stdout=subprocess.DEVNULL)
        for q in Path(tmp).rglob('*.json'):
            require('Projection fields '+str(q.relative_to(tmp)),q.read_bytes()==(b/q.relative_to(tmp)).read_bytes())
print(json.dumps({'status':'pass','checks':len(check),'original_files_checked':len(sha) if a.source_root else 0,'public_gp15_files_checked':public_count,'screening':{'rounds':len(round_counts),'candidates':len(rows),'advanced_to_draft':len(eligible),'dispositions':dict(dispositions)},'rsi_execution':{'bootstrap_valid_draws':[975,978],'bootstrap_attempts_per_leg':1000,'observed_station_deletions':loso_total,'observed_missing_layer_suppressions':missing_support_cases},'meaning':'Record identity, selected-field fidelity when originals supplied, chronology, candidate counts and recorded support rules; not scientific correctness, counterfactual utility, or live service replay.','results':check},ensure_ascii=False,indent=2))
