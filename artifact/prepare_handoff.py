"""Deterministically select recorded anchor→observation→reasoning→claim paths.
Standard library only. No extraction, scientific classification, or network calls.
"""
import argparse,copy,hashlib,json
from collections import defaultdict,deque
from pathlib import Path

class StructureError(ValueError): pass

def derive(payload,recorded,graph_file_sha,observation_id=None):
 result=payload.get('result',payload);graph=recorded.get('graph',recorded)
 if not isinstance(result,dict) or not isinstance(graph,dict):raise StructureError('JSON objects required')
 claim_id=result.get('claim_id');snapshot_id=result.get('snapshot_id')
 if not claim_id or not snapshot_id or result.get('paper_id')!=graph.get('paper_id'):raise StructureError('claim/snapshot required and paper must match graph')
 snapshots=list(graph.get('snapshots',[]))
 if isinstance(recorded.get('snapshot'),dict):snapshots.append(recorded['snapshot'])
 matching=[x for x in snapshots if x.get('id')==snapshot_id]
 if not matching:raise StructureError('result snapshot does not match recorded graph snapshot metadata')
 for s in matching:
  if 'entity_ids' in s and claim_id not in s['entity_ids']:raise StructureError('selected claim outside snapshot entity manifest')
  if 'graphSha256' in s and snapshot_id!='snapshot:sha256:'+s['graphSha256']:raise StructureError('snapshot ID/hash metadata inconsistent')
 collections=['anchors','observations','reasoning','claims'];nodes={};types={}
 for col in collections:
  for node in graph.get(col,[]):
   if node['id'] in nodes:raise StructureError('duplicate node identity')
   nodes[node['id']]=node;types[node['id']]=col
 if types.get(claim_id)!='claims':raise StructureError('selected claim missing from graph claims')
 edges=graph.get('edges',[]);incoming=defaultdict(list)
 for edge in edges:incoming[edge['target_id']].append(edge)
 # Reverse traversal is restricted to original observation/reasoning premises.
 # Shortest recorded route for each reachable observation, with stable edge-ID order.
 paths={claim_id:[]};queue=deque([claim_id])
 while queue:
  target=queue.popleft()
  for e in sorted(incoming[target],key=lambda x:x['id']):
   source=e['source_id'];kind=types.get(source)
   eligible=(types[target]=='claims' and kind=='reasoning' and e['relation'] in {'infers','supports','contradicts','qualifies'}) or (types[target]=='reasoning' and kind in {'observations','reasoning'} and e['relation']=='premise_for')
   if eligible and source not in paths:
    paths[source]=[e]+paths[target]
    if kind=='reasoning':queue.append(source)
 selected_nodes={claim_id};selected_edges={};selected_obs=[]
 versions={v['id'] for v in graph.get('artifact_versions',[])}
 for obs in sorted(x for x in paths if types.get(x)=='observations'):
  if observation_id and obs!=observation_id:continue
  grounds=[e for e in incoming[obs] if e['relation']=='grounds' and types.get(e['source_id'])=='anchors']
  if not grounds:continue
  for e in grounds:
   anchor=nodes[e['source_id']]
   if not anchor.get('artifact_version_id') or anchor['artifact_version_id'] not in versions:raise StructureError('anchor artifact version not recorded in graph')
  selected_obs.append(obs)
  for e in grounds+paths[obs]:
   selected_edges[e['id']]=e;selected_nodes.update([e['source_id'],e['target_id']])
 if not selected_obs:raise StructureError('no anchored observation→reasoning→selected-claim path; complete recorded graph required')
 derived={'nodes':[copy.deepcopy(nodes[x]) for x in sorted(selected_nodes)],'edges':[copy.deepcopy(selected_edges[x]) for x in sorted(selected_edges)],'origin':'deterministic union of original grounds edges and one stable shortest directed reasoning route per anchored observation; recorded graph, not retrieval shortest path','source_graph_sha256':graph_file_sha,'source_graph_sha256_basis':'exact input recorded-graph JSON file bytes (not a minted production snapshot ID)','selected_observation_ids':selected_obs,'recorded_snapshot_id':snapshot_id}
 output=copy.deepcopy(payload);out_result=output.get('result',output);out_result['observation_evidence_path']=derived
 assert {k:v for k,v in out_result.items() if k!='observation_evidence_path'}=={k:v for k,v in result.items() if k!='observation_evidence_path'}
 return output

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--result',type=Path,required=True);p.add_argument('--graph',type=Path,required=True);p.add_argument('--observation-id');p.add_argument('--out',type=Path,required=True);a=p.parse_args()
 if a.out.resolve() in {a.result.resolve(),a.graph.resolve()} or a.out.exists():p.error('--out must be a new file distinct from both inputs')
 try:
  raw=a.graph.read_bytes();out=derive(json.loads(a.result.read_text()),json.loads(raw),hashlib.sha256(raw).hexdigest(),a.observation_id)
 except (StructureError,KeyError,TypeError,ValueError) as e:p.exit(2,'STRUCTURE_ERROR: '+str(e)+'; inputs unchanged\n')
 with a.out.open('x') as stream:json.dump(out,stream,ensure_ascii=False,indent=2)
 print(a.out)
if __name__=='__main__':main()
