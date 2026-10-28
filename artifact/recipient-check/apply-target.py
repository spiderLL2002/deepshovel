#!/usr/bin/env python3
"""Explicit single-record simulated handoff policy; no scientific validation."""
import csv
import json
from pathlib import Path
BASE = Path(__file__).resolve().parent
source = json.loads((BASE/'input-record.jsonld').read_text())
i, node = next((i,n) for i,n in enumerate(source['@graph']) if 'ds:PreparedRecord' in n.get('@type', []))
prepared = json.loads(node['ds:json']['@value'])
reading = json.loads(node['ds:sourceReading']['@value'])
# Policy for this explicit simulated target: qualified operator reuse or retained
# model/conditions restrictions cannot fill an unrestricted universal threshold.
restricted = (reading.get('conclusion') == 'qualified_reuse'
              or bool(prepared.get('conditions'))
              or 'modeled' in prepared.get('quantity','').lower())
assert restricted, 'This demonstration expects the supplied qualified record; otherwise manual review is needed.'
def ev(pred, path=None):
    d={'subject_iri':node['@id'], 'predicate_iri':'urn:deepshovel:reuse:v1:'+pred,
       'input_jsonpath':f"$['@graph'][{i}]['ds:{pred}']['@value']"}
    if path: d['after_json_decode_jsonpath']=path
    return d
with (BASE/'target-row.csv').open('w', newline='') as f:
    writer=csv.DictWriter(f,fieldnames=['universal_threshold_MPa'])
    writer.writeheader(); writer.writerow({'universal_threshold_MPa':''})
decision={
 'action':'do_not_reuse',
 'target_field':'universal_threshold_MPa',
 'target_definition':'A universal threshold without reservoir, model, or conditions restrictions.',
 'target_value':None,
 'policy':'For this simulated target only, do not populate an unrestricted universal threshold from this record when operator conclusion is qualified_reuse or retained quantity/conditions restrict its scope.',
 'reason':'The retained operator conclusion is qualified_reuse and the prepared quantity and conditions describe a modeled F2/Asmari reservoir result. These retained restrictions do not satisfy the explicitly unrestricted target definition.',
 'retained_original':{k:prepared[k] for k in ['value','unit','quantity','conditions','uncertainty','notes']},
 'operator_conclusion':reading['conclusion'],
 'operator_authorship':reading['authorship'],
 'evidence':[ev('preparedValue'),ev('preparedUnit'),ev('preparedQuantity'),ev('preparedConditions'),ev('preparedUncertainty'),ev('preparedNotes'),ev('sourceReading','$.conclusion'),ev('sourceReading','$.authorship')],
 'scope':'Functional action on one fixed record and a simulated target schema, continuing the same recipient context. Not a second independent trial, human-benefit result, external application adoption, scientific safety certification, or cross-corpus scientific rule. No source truth was independently checked.'
}
(BASE/'decision-for-target.json').write_text(json.dumps(decision,ensure_ascii=False,indent=2)+'\n')
print('action: do_not_reuse')
print('target-row.csv: universal_threshold_MPa is blank')
print('retained original: '+prepared['value']+' '+prepared['unit'])
print('saved: decision-for-target.json')
