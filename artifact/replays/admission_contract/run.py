"""Replay production structural/source contract functions; no model or production writes."""
import argparse,os,sys,json,hashlib,copy
from pathlib import Path
from datetime import datetime,timezone
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--repo',type=Path);p.add_argument('--out',type=Path);a=p.parse_args()
r=a.repo or os.environ.get('DEEPSHOVEL_REPO') or Path(__file__).resolve().parents[2]/'vendor'
if not r:p.error('provide --repo or DEEPSHOVEL_REPO')
repo=Path(r).expanduser().resolve();sys.path.insert(0,str(repo/'compiler'))
from compiler_service.graph_contract import PaperEvidenceGraph
from compiler_service.models import DocumentIR
from compiler_service.compiler import _extraction_contract_violations
from compiler_service.canonical import to_canonical_graph,graph_sha256
from pydantic import ValidationError
from compiler_service.errors import ContractValidationError
script=Path(__file__).resolve();sha=lambda data:hashlib.sha256(data).hexdigest();stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ');runner_sha=sha(script.read_bytes())
out=(a.out or script.parent/'runs'/f'{stamp}-{runner_sha[:12]}').resolve();out.mkdir(parents=True,exist_ok=False)
f=json.loads((script.parent/'fixture.json').read_text());document=DocumentIR.model_validate(f['document'],strict=True)
def cycle(g):
 g['reasoning'].append({**g['reasoning'][0],'id':'reason.local.2'})
 for i,(s,t) in enumerate([('reason.local.1','reason.local.2'),('reason.local.2','reason.local.1')]):g['edges'].append(dict(id='edge.cycle.'+str(i),source_id=s,target_id=t,relation='premise_for',rationale=None,confidence=None))
cases=[('baseline',lambda g:None),('dangling_anchor',lambda g:g['claims'][0].update(anchor_ids=['anchor.missing'])),('quote_mismatch',lambda g:g['anchors'][0]['locator'].update(quote='This sentence is absent from the stored artifact.')),('disconnected_claim',lambda g:g['edges'].pop()),('reasoning_cycle',cycle),('content_identity_mismatch',lambda g:g['claims'][0].update(proposition_key='proposition:sha256:'+'0'*64)),('artifact_hash_mismatch',lambda g:g['anchors'][0].update(artifact_sha256='0'*64))]
results=[]
for name,mutate in cases:
 g=copy.deepcopy(f['graph']);mutate(g);stage='graph_model_validate';record={'case':name,'input_graph':g}
 try:
  graph=PaperEvidenceGraph.model_validate(g,strict=True);stage='extraction_contract_violations';violations=_extraction_contract_violations(graph,document)
  if violations:record.update(accepted=False,stage=stage,errors=[{'code':v.issue.value,'loc':list(v.loc)} for v in violations])
  else:
   stage='canonicalization';canonical=to_canonical_graph(graph,document.artifact,parser=document.parser,created_at='2026-01-01T00:00:00Z');record.update(accepted=True,stage=stage,errors=[],canonical_sha256=graph_sha256(canonical))
 except ValidationError as e:record.update(accepted=False,stage=stage,errors=e.errors(include_url=False,include_context=False,include_input=False))
 except ContractValidationError as e:record.update(accepted=False,stage=stage,errors=[{'code':v.issue.value,'loc':list(v.loc)} for v in e.violations])
 results.append(record)
assert results[0]['accepted'] and all(not r['accepted'] for r in results[1:])
files=['graph_contract.py','models.py','compiler.py','canonical.py','errors.py']
output={'kind':f['kind'],'run_utc':stamp,'runner_sha256':runner_sha,'fixture_sha256':sha((script.parent/'fixture.json').read_bytes()),'source_sha256':{'compiler/compiler_service/'+x:sha((repo/'compiler/compiler_service'/x).read_bytes()) for x in files},'results':results,'limits':'Direct production structural/source validators, not end-to-end compiler repair/import API. No semantic correctness, graph-hash envelope mismatch, or cumulative drop-budget test.'}
(out/'results.json').write_text(json.dumps(output,indent=2));(out/'document.json').write_text(json.dumps(f['document'],indent=2));summary={'baseline_accepted':results[0]['accepted'],'negative_controls':6,'negative_rejected':sum(not r['accepted'] for r in results[1:]),'cases':[{k:v for k,v in r.items() if k!='input_graph'} for r in results]};(out/'summary.json').write_text(json.dumps(summary,indent=2));print(out);print(json.dumps(summary,indent=2))

module_files={name:str(Path(module.__file__).resolve()) for name,module in sys.modules.items() if name.startswith('compiler_service') and getattr(module,'__file__',None)}
selected_repo=ROOT if 'ROOT' in globals() else repo
assert all(Path(path).is_relative_to(selected_repo) for path in module_files.values()), 'production module imported outside selected checkout'
result_dir=OUT if 'OUT' in globals() else out
(result_dir/'module-files.json').write_text(json.dumps(module_files,indent=2))
