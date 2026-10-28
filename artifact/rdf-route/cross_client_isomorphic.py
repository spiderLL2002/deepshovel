"""Compare two JSON-LD processor datasets for one supplied record (optional QA)."""
import argparse, hashlib, json
from pathlib import Path
import rdflib
from rdflib import Graph, URIRef, BNode, Literal, Namespace
from rdflib.compare import isomorphic
p=argparse.ArgumentParser();p.add_argument('--jsonld',type=Path,required=True);p.add_argument('--js-dataset',type=Path,required=True);p.add_argument('--original-record',type=Path,required=True);p.add_argument('--js-recovered',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
def from_js(t):
 kind=t['termType']
 if kind=='NamedNode':return URIRef(t['value'])
 if kind=='BlankNode':return BNode(t['value'])
 if kind=='Literal':
  dt=t.get('datatype',{});dt=dt.get('value','') if isinstance(dt,dict) else dt
  language=t.get('language') or None
  if language:
   assert dt=='http://www.w3.org/1999/02/22-rdf-syntax-ns#langString'
   return Literal(t['value'],lang=language,normalize=False)
  return Literal(t['value'],datatype=URIRef(dt) if dt else None,normalize=False)
 raise ValueError('Unsupported RDF term type: '+kind)
# Preserve lexical literal values; blank-node labels are processor-local.
rdflib.NORMALIZE_LITERALS=False
g=Graph().parse(a.jsonld,format='json-ld')
js=json.loads(a.js_dataset.read_text());jsg=Graph()
for q in js:
 assert q['graph']['termType']=='DefaultGraph', 'Named graphs are outside this check'
 assert q['subject']['termType'] in ('NamedNode','BlankNode')
 assert q['predicate']['termType']=='NamedNode'
 jsg.add(tuple(from_js(q[k]) for k in ('subject','predicate','object')))
assert isomorphic(g,jsg),'RDF graphs differ up to blank-node renaming'
ds=Namespace('urn:deepshovel:reuse:v1:');record=list(g.subjects(rdflib.RDF.type,ds.PreparedRecord));assert len(record)==1;record=record[0]
query='''PREFIX ds: <urn:deepshovel:reuse:v1:>
SELECT ?value ?unit ?conclusion ?sourcehash WHERE {
 ?record a ds:PreparedRecord; ds:preparedValue ?value; ds:preparedUnit ?unit; ds:preparedConclusion ?conclusion; ds:selectedAnchor ?anchor.
 ?anchor ds:sourceVersion ?version. ?version ds:sha256 ?sourcehash.
}'''
rows=[{str(k):str(v) for k,v in row.asdict().items()} for row in g.query(query)];assert len(rows)==1
original=a.original_record.read_bytes();recovered=str(g.value(record,ds.originalExport)).encode('utf-8');assert recovered==original;assert a.js_recovered.read_bytes()==original
obj=json.loads(recovered);assert hashlib.sha256(obj['canonical_payload'].encode()).hexdigest()==obj['record_id'].rsplit(':',1)[1]
result={'scope':'Two independently maintained processors, one fixed record; RDF graph isomorphism and literal recovery only; blank-node labels are not compared','rdflib_version':rdflib.__version__,'python_rdf_triples':len(g),'javascript_default_graph_quads':len(js),'rdf_graphs_isomorphic':True,'javascript_rdf_triples':len(jsg),'input_sha256':hashlib.sha256(a.jsonld.read_bytes()).hexdigest(),'record_id':obj['record_id'],'sparql_rows':rows,'recovered_utf8_bytes':len(recovered),'recovered_sha256':hashlib.sha256(recovered).hexdigest(),'both_recovered_exports_equal_actual_source_bytes':True,'recorded_file_check':str(g.value(record,ds.recordedFileCheck)),'current_file_check':str(g.value(record,ds.currentFileCheck))}
a.out.parent.mkdir(parents=True,exist_ok=True)
with a.out.open('x') as f:json.dump(result,f,ensure_ascii=False,indent=2);f.write('\n')
print(json.dumps(result,ensure_ascii=False,indent=2))
