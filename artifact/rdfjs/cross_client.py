"""Compare two JSON-LD processor datasets for one supplied record (optional QA)."""
import argparse, hashlib, json
from pathlib import Path
import rdflib
from rdflib import Graph, URIRef, Literal, Namespace
p=argparse.ArgumentParser();p.add_argument('--jsonld',type=Path,required=True);p.add_argument('--js-dataset',type=Path,required=True);p.add_argument('--original-record',type=Path,required=True);p.add_argument('--js-recovered',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
def term(t):
 if isinstance(t,URIRef):return ('NamedNode',str(t),'','')
 if isinstance(t,Literal):return ('Literal',str(t),str(t.datatype or ''),t.language or '')
 return ('BlankNode',str(t),'','')
def jt(t):
 dt=t.get('datatype',{});dt=dt.get('value','') if isinstance(dt,dict) else dt
 return (t['termType'],t['value'],dt or '',t.get('language',''))
g=Graph().parse(a.jsonld,format='json-ld');js=json.loads(a.js_dataset.read_text());assert all(q['graph']['termType']=='DefaultGraph' for q in js)
py_set={(term(s),term(pr),term(o)) for s,pr,o in g};js_set={(jt(q['subject']),jt(q['predicate']),jt(q['object'])) for q in js};assert py_set==js_set,'RDF term datasets differ'
ds=Namespace('urn:deepshovel:reuse:v1:');record=list(g.subjects(rdflib.RDF.type,ds.PreparedRecord));assert len(record)==1;record=record[0]
query='''PREFIX ds: <urn:deepshovel:reuse:v1:>
SELECT ?value ?unit ?conclusion ?sourcehash WHERE {
 ?record a ds:PreparedRecord; ds:preparedValue ?value; ds:preparedUnit ?unit; ds:preparedConclusion ?conclusion; ds:selectedAnchor ?anchor.
 ?anchor ds:sourceVersion ?version. ?version ds:sha256 ?sourcehash.
}'''
rows=[{str(k):str(v) for k,v in row.asdict().items()} for row in g.query(query)];assert len(rows)==1
original=a.original_record.read_bytes();recovered=str(g.value(record,ds.originalExport)).encode('utf-8');assert recovered==original;assert a.js_recovered.read_bytes()==original
obj=json.loads(recovered);assert hashlib.sha256(obj['canonical_payload'].encode()).hexdigest()==obj['record_id'].rsplit(':',1)[1]
result={'scope':'Two independently maintained processors, one fixed record; RDF term equality and literal recovery only','rdflib_version':rdflib.__version__,'python_rdf_triples':len(g),'javascript_default_graph_quads':len(js),'all_rdf_terms_equal':True,'input_sha256':hashlib.sha256(a.jsonld.read_bytes()).hexdigest(),'record_id':obj['record_id'],'sparql_rows':rows,'recovered_utf8_bytes':len(recovered),'recovered_sha256':hashlib.sha256(recovered).hexdigest(),'both_recovered_exports_equal_actual_source_bytes':True,'recorded_file_check':str(g.value(record,ds.recordedFileCheck)),'current_file_check':str(g.value(record,ds.currentFileCheck))}
a.out.parent.mkdir(parents=True,exist_ok=True)
with a.out.open('x') as f:json.dump(result,f,ensure_ascii=False,indent=2);f.write('\n')
print(json.dumps(result,ensure_ascii=False,indent=2))
