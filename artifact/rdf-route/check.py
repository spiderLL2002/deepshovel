"""One real-record candidate QA; no PDF or network access."""
import argparse
import sys
import hashlib
import json
from pathlib import Path
import rdflib
from rdflib import Graph, Namespace, Literal, RDF

HERE = Path(__file__).resolve().parent
ARTIFACT = HERE.parent
sys.path.insert(0, str(ARTIFACT))
from export_prov import convert

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--out-dir', required=True, type=Path)
args = parser.parse_args()
DS = Namespace('urn:deepshovel:reuse:v1:')
PROV = Namespace('http://www.w3.org/ns/prov#')
old = Graph().parse(ARTIFACT / 'recipient-check/input-record.jsonld', format='json-ld')
raw = str(next(old.objects(None, DS.originalExport)))
original = json.loads(raw)
p = original['payload']
assert original['record_id'].endswith('54ac83a1ddad95f7e179d64b636ce9e97da1c37fc4dd34173da3c29b79fa6463')
document, verification = convert(raw)
output = args.out_dir
output.mkdir(parents=True, exist_ok=False)
(output / 'record.jsonld').write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n')
g = Graph().parse(output / 'record.jsonld', format='json-ld')
query = (HERE / 'route.rq').read_text()
rows = [list(row) for row in g.query(query)]
# Independently follow JSON edges; this fixture must have one outgoing step each time.
expected = []
current = p['reference']['observation_id']
while current != p['reference']['claim_id']:
    choices = [e for e in p['selected_inspection_path']['edges'] if e['source_id'] == current]
    assert len(choices) == 1
    expected.append(choices[0])
    current = choices[0]['target_id']
nodes = {n['id']: n for n in p['selected_inspection_path']['nodes']}
expected_rows = [[i, e['source_id'], nodes[e['source_id']]['kind'], e['relation'], e['target_id'], nodes[e['target_id']]['kind']] for i,e in enumerate(expected)]
def check_rows(graph):
    actual = [[v.toPython() for v in row] for row in graph.query(query)]
    assert actual == expected_rows
    return actual
actual = check_rows(g)
record = next(g.subjects(DS.originalExport, None))
route = g.value(record, DS.selectedRoute)
ordered_nodes = list(g.items(g.value(route, DS.orderedNodes)))
assert [str(g.value(n, DS.recordedNodeId)) for n in ordered_nodes] == [expected[0]['source_id']] + [e['target_id'] for e in expected]
# Every projected node and edge compared to its complete original JSON object.
node_count = edge_count = 0
for node, node_id in g.subject_objects(DS.recordedNodeId):
    assert json.loads(str(g.value(node, DS.json))) == nodes[str(node_id)]
    assert str(g.value(node, DS.nodeKind)) == nodes[str(node_id)]['kind']
    node_count += 1
source_edges = {e['id']:e for e in p['selected_inspection_path']['edges']}
for edge, edge_id in g.subject_objects(DS.recordedEdgeId):
    obj = source_edges[str(edge_id)]
    assert json.loads(str(g.value(edge, DS.json))) == obj
    assert str(g.value(edge, DS.relation)) == obj['relation']
    for field in ['source', 'target']:
        assert str(g.value(g.value(edge, DS[field]), DS.recordedNodeId)) == obj[field + '_id']
    edge_count += 1
grounds = list(g.query('''PREFIX ds: <urn:deepshovel:reuse:v1:>
SELECT ?anchor ?relation ?observation WHERE {
 ?record ds:selectedGroundsEdge ?edge .
 ?edge ds:source/ds:recordedNodeId ?anchor; ds:relation ?relation; ds:target/ds:recordedNodeId ?observation . }'''))
assert [[str(v) for v in r] for r in grounds] == [[p['reference']['anchor_id'], 'grounds', p['reference']['observation_id']]]
recovered = str(g.value(record, DS.originalExport)).encode('utf-8')
assert recovered == raw.encode('utf-8')
(output / 'recovered-record.json').write_bytes(recovered)
assert json.loads(str(g.value(record, DS.sourceReading))) == p['source_reading']
assert json.loads(str(g.value(g.value(record, DS.selectedObservation), DS.json)))['value'] == p['selected_observation']['value']
# Exactly the same standard PROV facts, including all prov:Entity typing.
def prov_facts(graph):
    return {(s,p,o) for s,p,o in graph if str(p).startswith(str(PROV)) or (p == RDF.type and str(o).startswith(str(PROV)))}
assert prov_facts(g) == prov_facts(old)
# RDF-only terminal-edge tampering is detected against the retained original.
terminal = next(e for e in g.objects(route, DS.step) if str(g.value(e, DS.relation)) == 'infers')
g.set((terminal, DS.relation, Literal('supports', datatype=rdflib.XSD.string)))
try:
    check_rows(g)
except AssertionError:
    negative = 'RDF-only infers-to-supports substitution detected'
else:
    raise AssertionError('tampering went undetected')
result = {'passed': True, 'rdflib_version': rdflib.__version__, 'record_id': original['record_id'],
          'rdf_triples': len(g), 'node_objects_compared': node_count, 'edge_objects_compared': edge_count,
          'route': actual, 'grounds': [[str(v) for v in r] for r in grounds],
          'original_bytes': len(recovered), 'original_sha256': hashlib.sha256(recovered).hexdigest(),
          'source_value': p['selected_observation']['value'], 'source_reading': p['source_reading'],
          'prov_facts_unchanged': True, 'negative_control': negative, 'verification': verification}
(output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({k:v for k,v in result.items() if k not in ['verification', 'source_reading']},ensure_ascii=False,indent=2))
