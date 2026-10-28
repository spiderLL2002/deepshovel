#!/usr/bin/env python3
"""Restricted one-record reconstruction; minimal answers are saved before RDF is read."""
import csv, json
from pathlib import Path
BASE = Path(__file__).resolve().parent

def save(name, data):
    (BASE/name).write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')

with (BASE/'value-citation-projection.csv').open(newline='') as f:
    rows = list(csv.DictReader(f))
assert len(rows) == 1
r = rows[0]
minimal = {
 'input':'value-citation-projection.csv',
 'questions':[
  {'id':1,'answer':{'prepared_value':r['prepared_value'],'unit':r['unit']},'evidence':['CSV data row 1, prepared_value','CSV data row 1, unit']},
  {'id':2,'answer':'unavailable','reason':'No original selected-observation numeric, raw or unit fields are present.'},
  {'id':3,'answer':'unavailable','reason':'No prepared quantity or conditions fields are present.'},
  {'id':4,'answer':'unavailable','reason':'No selected-observation ID, claim ID or directed route is present.'},
  {'id':5,'answer':'unavailable','reason':'DOI is present, but source-version SHA and recorded/corrected locators are absent.','retained_doi':r['doi'],'evidence':['CSV data row 1, doi']},
  {'id':6,'answer':'unavailable','reason':'No operator conclusion or qualification fields are present.'}
 ]
}
save('answer-minimal.json', minimal)
print('SAVED answer-minimal.json BEFORE opening input-record.jsonld', flush=True)

# The richer input is only opened after the independent minimal answer is on disk.
import rdflib
record = json.loads((BASE/'input-record.jsonld').read_text())
g = rdflib.Graph().parse(data=json.dumps(record), format='json-ld')
print('RDFLib', rdflib.__version__, 'triples', len(g))

DS = rdflib.Namespace('urn:deepshovel:reuse:v1:')
subject = next(g.subjects(rdflib.RDF.type, DS.PreparedRecord))
def literal(s, pred):
    return str(g.value(s, DS[pred]))
def embedded(s, pred):
    return json.loads(literal(s,pred))
prepared = embedded(subject,'json')
obs_subject = g.value(subject, DS.selectedObservation)
anchor_subject = g.value(subject, DS.selectedAnchor)
obs = embedded(obs_subject,'json')
anchor = embedded(anchor_subject,'json')
ref = embedded(subject,'reference')
path = embedded(subject,'selectedInspectionPath')
reading = embedded(subject,'sourceReading')
source_subject = g.value(anchor_subject, DS.sourceVersion)
# Follow directed edges from the selected observation; do not substitute retrieval relation.
from collections import deque
queue = deque([(obs['id'], [])]); routes=[]
while queue:
    node, route = queue.popleft()
    if node == ref['claim_id']:
        routes.append(route); continue
    visited={obs['id']} | {e['target_id'] for _,e in route}
    for i,e in enumerate(path['edges']):
        if e['source_id']==node and e['target_id'] not in visited:
            queue.append((e['target_id'],route+[(i,e)]))
assert len(routes)==1, 'Do not silently choose between ambiguous routes'
route=routes[0]
# Exact RDF IRI plus physical JSON-LD locator and decoded JSONPath.
def evidence(s, pred, inner=None):
    i=next(i for i,n in enumerate(record['@graph']) if n['@id']==str(s))
    out={'subject_iri':str(s),'predicate_iri':str(DS[pred]),'input_jsonpath':f"$['@graph'][{i}]['ds:{pred}']['@value']"}
    if inner: out['after_json_decode_jsonpath']=inner
    return out
questions=[
 {'id':1,'answer':{k:prepared[k] for k in ['value','unit']},'layer':'operator-prepared entry','evidence':[evidence(subject,'preparedValue'),evidence(subject,'preparedUnit')]},
 {'id':2,'answer':{k:obs['value'][k] for k in ['numeric','raw','unit']},'layer':'recorded selected observation','evidence':[evidence(obs_subject,'json','$.value.numeric'),evidence(obs_subject,'json','$.value.raw'),evidence(obs_subject,'json','$.value.unit')]},
 {'id':3,'answer':{k:prepared[k] for k in ['quantity','conditions']},'layer':'operator-prepared entry','evidence':[evidence(subject,'preparedQuantity'),evidence(subject,'preparedConditions')]},
 {'id':4,'answer':{'selected_observation_id':obs['id'],'claim_id':ref['claim_id'],'directed_route':[{'source':e['source_id'],'relation':e['relation'],'target':e['target_id'],'edge_id':e['id']} for _,e in route],'terminal_relation':route[-1][1]['relation']},'evidence':[evidence(obs_subject,'recordedNodeId'),evidence(subject,'reference','$.claim_id')]+[evidence(subject,'selectedInspectionPath',f'$.edges[{i}]') for i,_ in route]},
 {'id':5,'answer':{'source_version_sha256':literal(source_subject,'sha256'),'recorded_locator':anchor['selector'],'operator_corrected_locator':prepared['locator'],'corrected_region_coordinates':'unavailable'},'qualification':'The correction is retained operator text; no source PDF was opened and no corrected rectangle is supplied. Null locator fields remain unavailable.','evidence':[evidence(source_subject,'sha256'),evidence(anchor_subject,'json','$.selector'),evidence(subject,'preparedLocator'),evidence(subject,'preparedNotes')]},
 {'id':6,'answer':{'conclusion':reading['conclusion'],'authorship':reading['authorship'],'purpose':prepared['purpose'],'qualification':prepared['uncertainty'],'notes':reading['notes']},'qualification':'Reconstructed operator entry, not independent scientific validation.','evidence':[evidence(subject,'sourceReading','$.conclusion'),evidence(subject,'sourceReading','$.authorship'),evidence(subject,'preparedPurpose'),evidence(subject,'preparedUncertainty'),evidence(subject,'sourceReading','$.notes')]}
]
# Verify explicit prepared triples agree with their embedded copies.
for key in ['value','unit','quantity','conditions','locator','uncertainty']:
    assert prepared[key] == literal(subject,'prepared'+key.title())
answer={'input':'input-record.jsonld','method':'RDFLib graph lookup followed by JSON decoding of xsd:string literals; directed traversal of embedded selectedInspectionPath edges','rdflib_version':rdflib.__version__,'triples':len(g),'questions':questions,'scope':'One purposively selected record; retention illustration only. CSV omissions are by construction; no competitor, human-benefit, or scientific-truth claim.'}
save('answer-record.json',answer)
print('SAVED answer-record.json; six questions with exact RDF and JSON locators')
print('prepared:',prepared['value'],prepared['unit'],'original:',obs['value'])
print('directed route:', ' -> '.join([obs['id']]+[e['target_id'] for _,e in route]))
print('terminal relation:',route[-1][1]['relation'])
print('source sha256:',literal(source_subject,'sha256'))
print('operator conclusion:',reading['conclusion'])
