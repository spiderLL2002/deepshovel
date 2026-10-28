"""Offline production-code contract exercise, not a retrieval benchmark.
Run with deepshovel-2/compiler/.venv/bin/python. No network or production writes.
"""
import sys,json,hashlib,tempfile,os,argparse
from datetime import datetime,timezone
from pathlib import Path
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--repo',type=Path,default=None,help='DeepShovel checkout; overrides DEEPSHOVEL_REPO')
parser.add_argument('--out',type=Path,default=None,help='New output directory (must not exist)')
args=parser.parse_args()
repo_value=args.repo or os.environ.get('DEEPSHOVEL_REPO') or Path(__file__).resolve().parents[2]/'vendor'
if not repo_value: parser.error('provide --repo or DEEPSHOVEL_REPO')
ROOT=Path(repo_value).expanduser().resolve()
if not (ROOT/'compiler/compiler_service/graph_contract.py').is_file(): parser.error('invalid repository')
SCRIPT=Path(__file__).resolve()
RUN_SHA=hashlib.sha256(SCRIPT.read_bytes()).hexdigest()
STAMP=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
OUT=(args.out or SCRIPT.parent/'runs'/f'{STAMP}-{RUN_SHA[:12]}').resolve()
OUT.mkdir(parents=True,exist_ok=False)
sys.path.insert(0,str(ROOT/'compiler'))
import fixture_helpers as h
from compiler_service.graph_contract import Scope,proposition_key,scoped_claim_id
from compiler_service.retrieval_contract import SearchScopeFilter,ClaimSearchDocument,RelationDecision
from compiler_service.retrieval_service import _scope_hard_match,_scope_relation,_Candidate,_cluster_candidates
from compiler_service.retrieval_vectors import NumpyVectorIndex
from compiler_service.retrieval_text import time_unit_factor
statement='Neodymium isotope offset between ferrihydrite and solution is stable.'
# Numerical values source-derived from the paper's Nd demonstration; strings,
# vectors, missing-data and altered-scope variants are explicitly constructed.
rows=[('loading','variable ferrihydrite loading','>=48 h',-0.113,'supports'),('unknown',None,None,None,'supports'),('series','time series','24--168 h',-0.098,'supports'),('series-repeat','time series','24--168 h',-0.098,'supports'),('series-other-relation','time series','24--168 h',-0.098,'equivalent')]
docs=[]; identities=[]
for name,condition,time,estimate,relation in rows:
    scope=Scope(id='scope-'+name,location=None,time=time,formation=None,sample_type='ferrihydrite',method=None,condition=condition,measurement='Nd isotope offset',uncertainty=None,entities=['Nd'])
    key=proposition_key(statement); identity=scoped_claim_id(key,scope)
    d=h._document(name,statement);d['scope']=scope.canonical();d['proposition_key']=key;d['scoped_claim_id']=identity
    docs.append(d);identities.append({'claim_id':name,'scope':scope.model_dump(),'proposition_key':key,'scoped_claim_id':identity,'estimate_per_mille':estimate,'assigned_relation':relation})
fixture={'kind':'source-derived contract fixture with synthetic variants; not human annotations','source':'WWW2027_Demo_v2/paper/main.tex Nd example; estimates -0.098 and -0.113 per mille','limitations':'No source-path validation. Numeric endpoints deliberately absent in production Scope.canonical. Fixed artificial vectors and assigned relations. Not a quality benchmark.','documents':docs,'identity_records':identities}
(OUT/'fixture.json').write_text(json.dumps(fixture,ensure_ascii=False,indent=2))
cases=[('unbound',{}),('time-series',{'condition':['time series']}),('loading',{'condition':['variable ferrihydrite loading']}),('other-condition',{'condition':['pH 7']}),('condition-or',{'condition':['time series','variable ferrihydrite loading']}),('exact-time-label',{'time':{'label':'24--168 h','start':None,'end':None,'unit':None}}),('overlapping-label-not-exact',{'time':{'label':'48--168 h','start':None,'end':None,'unit':None}}),('numeric-hours-label-only-candidate',{'time':{'label':None,'start':48,'end':168,'unit':'h'}}),('unknown-unit',{'time':{'label':None,'start':48,'end':168,'unit':'epochs'}})]
results=[]
with tempfile.TemporaryDirectory(prefix='scope-contract-') as tmp:
    vector=NumpyVectorIndex(data_dir=Path(tmp),model_name='fake-multilingual',model_revision='fake-rev-1',dimension=2)
    service=h._service(Path(tmp),vector=vector)
    service.index_snapshot(h._index_request(h._manifest(document_count=len(docs)),docs))
    parsed=[ClaimSearchDocument.model_validate(d) for d in docs]
    # Controlled rank: loading and unknown outrank all time-series variants.
    vector.upsert(project_id='project-1',snapshot_id='snapshot-1',claim_ids=[d.claim_id for d in parsed],claim_types=['descriptive']*5,polarities=['affirmed']*5,scopes=[d.scope for d in parsed],vectors=[[1,0],[.99,.01],[.6,.8],[.5,.86],[.4,.91]])
    for name,payload in cases:
        scope=SearchScopeFilter.model_validate(payload) if payload else None
        expected=[d.claim_id for d in parsed if scope is None or _scope_hard_match(scope,d.scope)]
        configurations={}
        for config in ['no_filter','metadata_filter','metadata_filter_plus_identity']:
            active=None if config=='no_filter' else scope
            by_limit={}
            for limit in [1,10]:
                common=dict(project_id='project-1',snapshot_ids=['snapshot-1'],limit=limit,claim_types=[],polarity=None,scope=active)
                lexical=service.store.lexical_search(**common,dense_model=service.dense_model,dense_model_revision=service.dense_revision,dense_dimension=service.dense_dimension,query_values=['neodymium'],must_values=[],exclude_values=[])
                dense=vector.search(**common,vector=[1,0])
                by_limit[str(limit)]={k:[{'claim_id':x.claim_id,'score':x.score} for x in hits] for k,hits in [('bm25',lexical),('dense',dense)]}
            configurations[config]=by_limit
        actual={channel:[x['claim_id'] for x in configurations['metadata_filter']['10'][channel]] for channel in ['bm25','dense']}
        assert all(set(value)==set(expected) for value in actual.values()),(name,actual,expected)
        assert configurations['metadata_filter']==configurations['metadata_filter_plus_identity']
        results.append({'case':name,'query_scope':payload,'expected_by_production_hard_match':expected,'scope_relations':{d.claim_id:('unknown' if scope is None else _scope_relation(scope,d.scope)) for d in parsed},'configurations':configurations})
    candidates=[]
    for d,row in zip(parsed,rows):
        decision=RelationDecision(candidate_key=h.relation_candidate_key(d.snapshot_id,d.claim_id),relation=row[-1],relation_direction='result_to_query',relation_confidence=1.0,scope_relation='unknown')
        candidates.append(_Candidate(document=d,lexical=1,dense=1,rrf=1,decision=decision))
    clusters=_cluster_candidates(candidates,limit=10)
    cluster_output=[c.model_dump() for c in clusters]
# Identity is not itself a filter and does not generate snapshot IDs.
base=Scope.model_validate(identities[2]['scope']); changed=base.model_copy(update={'condition':'variable ferrihydrite loading'})
identity_checks={'same_statement_different_scope_different_id':scoped_claim_id(proposition_key(statement),base)!=scoped_claim_id(proposition_key(statement),changed),'same_scope_repeat_stable_id':identities[2]['scoped_claim_id']==identities[3]['scoped_claim_id'],'proposition_key_ignores_separate_scope':len({x['proposition_key'] for x in identities})==1,'scope_text_inside_statement_changes_proposition_key':proposition_key(statement)!=proposition_key(statement+' at pH 5.5'),'snapshot_behavior':'This fixture retains snapshot-1. scoped_claim_id does not itself mint or validate a new snapshot. Graph import/snapshot lifecycle not tested.'}
files=['graph_contract.py','retrieval_service.py','retrieval_store.py','retrieval_vectors.py','retrieval_contract.py','retrieval_text.py']
hashes={'compiler/compiler_service/'+f:hashlib.sha256((ROOT/'compiler/compiler_service'/f).read_bytes()).hexdigest() for f in files}
output={'run_utc':STAMP,'runner_sha256':RUN_SHA,'fixture_helpers_sha256':hashlib.sha256((SCRIPT.parent/'fixture_helpers.py').read_bytes()).hexdigest(),'cases':results,'identity_checks':identity_checks,'clusters':cluster_output,'source_sha256':hashes,'hour_unit_factor':time_unit_factor('h')}
(OUT/'results.json').write_text(json.dumps(output,ensure_ascii=False,indent=2))
output['identity_bound_result_refs']={c['case']:[{'snapshot_id':'snapshot-1','claim_id':x,'scoped_claim_id':next(d['scoped_claim_id'] for d in docs if d['claim_id']==x)} for x in c['expected_by_production_hard_match']] for c in results}
output['configuration_note']='Metadata-only and metadata+identity use the same production search calls. Identity binding is output bookkeeping in identity_bound_result_refs, not a third retrieval algorithm; invariant rankings are expected by construction.'
(OUT/'results.json').write_text(json.dumps(output,ensure_ascii=False,indent=2))
summary={'fixture_documents':len(docs),'query_cases':len(cases),'channel_full_pool_parity_checks':len(cases)*2,'parity_passed':True,'metadata_vs_metadata_plus_identity_equal_cases':len(cases),'cluster_count':len(clusters),'distinct_families_across_all_clusters':len({m.paper_family_id for c in clusters for m in c.member_refs}),'families_per_cluster':[c.distinct_paper_families for c in clusters],'identity_checks':identity_checks,'limitations':fixture['limitations']}
(OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
print('Output: '+str(OUT))
print(json.dumps(summary,ensure_ascii=False,indent=2))

module_files={name:str(Path(module.__file__).resolve()) for name,module in sys.modules.items() if name.startswith('compiler_service') and getattr(module,'__file__',None)}
selected_repo=ROOT if 'ROOT' in globals() else repo
assert all(Path(path).is_relative_to(selected_repo) for path in module_files.values()), 'production module imported outside selected checkout'
result_dir=OUT if 'OUT' in globals() else out
(result_dir/'module-files.json').write_text(json.dumps(module_files,indent=2))
