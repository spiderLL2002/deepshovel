"""Offline structural check of exactly the frozen 20 slots. Standard library only.
Uses unchanged prepare_handoff.derive; contains no relevance or scientific assessment.
"""
import json,hashlib,pathlib,argparse,collections
from prepare_handoff import derive
H=pathlib.Path(__file__).resolve().parent

def check(raw_dir=None):
 selection=json.loads((H/'selection.json').read_text());inputs=json.loads((H/'inputs.json').read_text());out=[]
 for slot,result in zip(selection['slots'],inputs):
  key=slot['snapshot_id'].rsplit(':',1)[-1]
  path=(raw_dir/f'{key}.json') if raw_dir else H/'graphs'/f'{key}.json'
  raw=path.read_bytes();recorded=json.loads(raw)
  if raw_dir:recorded=recorded['body']
  g=recorded['graph'];r=dict(slot);r['graph_input_sha256']=hashlib.sha256(raw).hexdigest()
  r['input_required_fields_present']=all(result.get(k) for k in ['claim_id','snapshot_id','paper_id'])
  kinds={n.get('kind') for n in result['shortest_evidence_path']['nodes']}
  r['shortest_has_observation']='observation' in kinds;r['shortest_has_anchor']='source_anchor' in kinds
  r['shortest_has_both']=r['shortest_has_observation'] and r['shortest_has_anchor']
  r['full_graph_anchored_path']=False;r['page_located_anchor']=False;r['artifact_version_identity']=False
  try:
   derived=derive(result,recorded,r['graph_input_sha256'])['observation_evidence_path'];r['full_graph_anchored_path']=True
   anchors={n['id']:n for n in g.get('anchors',[])};versions={n['id']:n for n in g.get('artifact_versions',[])}
   selected=[anchors[n['id']] for n in derived['nodes'] if n['id'] in anchors]
   r['selected_observation_ids']=derived['selected_observation_ids'];r['selected_edge_ids']=[e['id'] for e in derived['edges']]
   r['page_located_anchor']=bool(selected) and all(isinstance(n.get('selector',{}).get('page'),int) and n['selector']['page']>0 for n in selected)
   r['artifact_version_identity']=bool(selected) and all(n.get('artifact_version_id') in versions and versions[n['artifact_version_id']].get('sha256') and n['artifact_version_id']=='artifact-version:sha256:'+versions[n['artifact_version_id']]['sha256'] for n in selected)
   r['source_version_matches_selected_anchors']=all(n.get('artifact_version_id')==result.get('source',{}).get('artifactVersionId') for n in selected)
   problems=[]
   if not r['page_located_anchor']:problems.append('one or more selected anchors lack positive integer page')
   if not r['artifact_version_identity']:problems.append('selected anchor lacks matching recorded artifact-version ID/hash')
   if not r['source_version_matches_selected_anchors']:problems.append('selected anchor source version differs from retrieval source version')
   r['success']=not problems;r['failure_reason']='; '.join(problems) or None
  except Exception as e:r['success']=False;r['failure_reason']=type(e).__name__+': '+str(e)
  out.append(r)
 summary={'scope':'20 consecutive saved planned-preview slots: first 10 Nd loading + first 10 Asmari; purposefully specified queries; no relevance filtering; no new model queries','slots':len(out),'unique_claim_snapshot_pairs':len({(x['claim_id'],x['snapshot_id']) for x in out}),'unique_snapshots':len({x['snapshot_id'] for x in out}),'counts':{k:sum(bool(x.get(k)) for x in out) for k in ['input_required_fields_present','shortest_has_observation','shortest_has_anchor','shortest_has_both','full_graph_anchored_path','page_located_anchor','artifact_version_identity','success']},'full_graph_success_without_shortest_both':sum(x['success'] and not x['shortest_has_both'] for x in out),'failures':dict(collections.Counter(x['failure_reason'] for x in out if not x['success'])),'by_cohort':{c:{k:sum(bool(x.get(k)) for x in out if x['cohort']==c) for k in ['shortest_has_both','full_graph_anchored_path','success']} for c in ['Nd_loading','Asmari']},'limitations':['Structure only, not scientific correctness, random coverage, or user benefit.','Semantic text/values omitted from released projections; no PDF/source content copied.','No source PDF bytes checked in this cohort; artifact-version ID/hash metadata checked only.','Nd time-series and adsorption-stop live cases still returned related evidence; this cohort does not correct or supersede them.']}
 return out,summary
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--raw-dir',type=pathlib.Path);p.add_argument('--out-dir',type=pathlib.Path,default=H);a=p.parse_args();a.out_dir.mkdir(exist_ok=True)
 outcomes,summary=check(a.raw_dir)
 for name,data in [('outcomes',outcomes),('summary',summary)]: (a.out_dir/(name+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps(summary,ensure_ascii=False,indent=2))
