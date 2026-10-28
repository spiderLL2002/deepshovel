#!/usr/bin/env python3
"""Export a verified schema-1.5 reuse record as a minimal PROV-O JSON-LD view.

Standard library only; verify_reuse.py must be adjacent. No scientific validation,
actor attribution, signature, or PDF-byte recheck is performed by this exporter.
"""
import argparse
import json
from pathlib import Path

from verify_reuse import parse_json, verify

VERSION = '0.2.0'
DOMAIN = 'urn:deepshovel:reuse:v1:'


def literal(value):
    """Keep strings literal even if they resemble JSON-LD keywords or IRIs."""
    return {'@value': value, '@type': 'xsd:string'}


def json_literal(value):
    # String encoding deliberately preserves JSON nulls, array order, and types.
    return literal(json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False))


def link(identifier):
    return {'@id': identifier}


def project_route(payload, base, ids, graph, result):
    """Project the unique directed selected-observation-to-claim route, if unique.

    No best-route choice is inferred. Ambiguous/absent routes retain their JSON
    and get an explicit status without a selectedRoute link.
    """
    path = payload['selected_inspection_path']
    nodes = {node['id']: node for node in path['nodes']}
    start = payload['reference']['observation_id']
    end = payload['reference']['claim_id']
    routes = []
    def visit(current, edges, seen):
        if len(routes) > 1:
            return
        if current == end:
            routes.append(edges)
            return
        for edge in path['edges']:
            target = edge['target_id']
            if edge['source_id'] == current and target not in seen:
                visit(target, edges + [edge], seen | {target})
    visit(start, [], {start})
    status = 'unique' if len(routes) == 1 else ('absent' if not routes else 'ambiguous')
    result['ds:routeProjectionStatus'] = literal(status)
    if status != 'unique':
        return
    route = routes[0]
    node_ids = [start] + [edge['target_id'] for edge in route]
    anchor_id = payload['reference']['anchor_id']
    selected = list(dict.fromkeys([anchor_id] + node_ids))
    iri = {node: base + ':route-node:' + str(i) for i, node in enumerate(selected)}
    iri[start], iri[anchor_id] = ids['observation'], ids['anchor']
    existing = {node['@id']: node for node in graph}
    for node_id in selected:
        node = nodes[node_id]
        projected = existing.get(iri[node_id])
        if projected is None:
            projected = {'@id': iri[node_id], '@type': 'ds:RecordedNode',
                         'ds:json': json_literal(node)}
            graph.append(projected)
        projected['ds:recordedNodeId'] = literal(node_id)
        projected['ds:nodeKind'] = literal(node['kind'])
    route_id = base + ':selected-route'
    result['ds:selectedRoute'] = link(route_id)
    result['ds:selectedClaim'] = link(iri[end])
    route_node = {'@id': route_id, '@type': 'ds:SelectedRoute',
                  'ds:orderedNodes': {'@list': [link(iri[n]) for n in node_ids]},
                  'ds:step': []}
    graph.append(route_node)
    grounds = [edge for edge in path['edges'] if edge['source_id'] == anchor_id
               and edge['target_id'] == start and edge['relation'] == 'grounds']
    for i, edge in enumerate(route + grounds):
        edge_id = base + ':route-edge:' + str(i)
        item = {'@id': edge_id, '@type': 'ds:RecordedEdge',
                'ds:recordedEdgeId': literal(edge['id']),
                'ds:source': link(iri[edge['source_id']]),
                'ds:target': link(iri[edge['target_id']]),
                'ds:relation': literal(edge['relation']), 'ds:json': json_literal(edge)}
        graph.append(item)
        if i < len(route):
            item['ds:position'] = {'@value': i, '@type': 'xsd:integer'}
            route_node['ds:step'].append(link(edge_id))
        else:
            result.setdefault('ds:selectedGroundsEdge', []).append(link(edge_id))


def convert(raw):
    record = parse_json(raw)
    report = verify(record)
    if not report['passed']:
        raise ValueError('verify_reuse rejected record: ' + json.dumps(report, ensure_ascii=False))
    p = record['payload']
    if p['schema_version'] != 'generic-observation-reuse/1.5':
        raise ValueError('export_prov requires schema generic-observation-reuse/1.5')
    if not isinstance(p.get('prepared_observation'), dict) or not isinstance(p.get('source_reading'), dict):
        raise ValueError('prepared_observation and source_reading must be objects')
    base = 'urn:deepshovel:reuse:sha256:' + record['record_id'].rsplit(':', 1)[1]
    ids = {key: base + ':' + key for key in ['record', 'imported', 'observation', 'anchor', 'preparation']}
    def entity(key, role, data):
        return {'@id': ids[key], '@type': ['prov:Entity', 'ds:' + role], 'ds:json': json_literal(data)}
    result = entity('record', 'PreparedRecord', p['prepared_observation'])
    result.update({
        'prov:wasGeneratedBy': link(ids['preparation']),
        # Data preparation lineage only; none of the reasoning edges become PROV derivations.
        'prov:wasDerivedFrom': link(ids['imported']),
        'ds:recordId': literal(record['record_id']),
        'ds:schemaVersion': literal(p['schema_version']),
        'ds:hashAlgorithm': literal(record['hash_algorithm']),
        'ds:canonicalization': literal(record.get('canonicalization', '')),
        'ds:canonicalPayload': literal(record['canonical_payload']),
        'ds:originalExport': literal(raw),
        'ds:selectedObservation': link(ids['observation']),
        'ds:selectedAnchor': link(ids['anchor']),
        'ds:selectedInspectionPath': json_literal(p['selected_inspection_path']),
        'ds:pathOrigin': json_literal(p['path_origin']),
        'ds:reference': json_literal(p['reference']),
        'ds:preparedFields': json_literal(p['prepared_observation']),
        'ds:sourceReading': json_literal(p['source_reading']),
        'ds:sourceIdentityCheck': json_literal(p['source_identity_check']),
        'ds:verificationReport': json_literal(report),
        'ds:currentFileCheck': literal(report['source_identity']['current_file_check']),
        'ds:recordedFileCheck': literal(p['source_identity_check']['check_state']),
        'ds:structuralLinkState': literal(p['structural_link_state']),
        'ds:observationReachesClaim': {'@value': p['observation_reaches_claim'], '@type': 'xsd:boolean'},
        'ds:directAnchorObservationEdgeRecorded': {'@value': p['direct_anchor_observation_edge_recorded'], '@type': 'xsd:boolean'},
    })
    for key in ['quantity', 'value', 'unit', 'uncertainty', 'conditions', 'conclusion', 'purpose', 'locator', 'notes']:
        value = p['prepared_observation'].get(key)
        if isinstance(value, str):
            result['ds:prepared' + key[0].upper() + key[1:]] = literal(value)
    imported = entity('imported', 'ImportedResult', p['original_result'])
    observation = entity('observation', 'SelectedObservation', p['selected_observation'])
    observation['ds:recordedNodeId'] = literal(p['reference']['observation_id'])
    anchor = entity('anchor', 'SelectedAnchor', p['selected_source_anchor'])
    anchor['ds:recordedNodeId'] = literal(p['reference']['anchor_id'])
    anchor['ds:groundsObservation'] = link(ids['observation'])
    graph = [result, imported, observation, anchor]
    project_route(p, base, ids, graph, result)
    # These hashes are resolved by verify_reuse from the selected anchor or locates edge.
    for digest in report['source_identity']['expected_hashes']:
        source_id = 'urn:deepshovel:artifact:sha256:' + digest
        anchor.setdefault('ds:sourceVersion', []).append(link(source_id))
        graph.append({'@id': source_id, '@type': ['prov:Entity', 'ds:SourceVersion'],
                      'ds:artifactVersionId': literal('artifact-version:sha256:' + digest),
                      'ds:sha256': literal(digest)})
    graph.append({'@id': ids['preparation'], '@type': ['prov:Activity', 'ds:RecordPreparation'],
                  'prov:used': [link(ids[key]) for key in ['imported', 'observation', 'anchor']],
                  'ds:description': literal('Preparation represented by the saved record; no actor identity or execution timing inferred.')})
    return {'@context': {'prov': 'http://www.w3.org/ns/prov#', 'ds': DOMAIN,
                         'xsd': 'http://www.w3.org/2001/XMLSchema#'}, '@graph': graph}, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version=VERSION)
    parser.add_argument('--record', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    try:
        # read_bytes avoids universal-newline normalization of the original literal.
        document, report = convert(args.record.read_bytes().decode('utf-8'))
        rendered = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
        # A new file only: never replace the input, or an earlier export.
        with args.out.open('x', encoding='utf-8', newline='\n') as output:
            output.write(rendered)
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({'exported': False, 'error': str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({'exported': True, 'exporter_version': VERSION, 'out': str(args.out),
                      'verification': report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
