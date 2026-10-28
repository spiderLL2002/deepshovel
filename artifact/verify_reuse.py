#!/usr/bin/env python3
"""Verify portable generic-observation-reuse/1.4 and 1.5 records; no scientific adjudication."""
import argparse
import hashlib
import json
import re
from pathlib import Path


def strict_equal(a, b):
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(strict_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(strict_equal(x, y) for x, y in zip(a, b))
    return a == b


def parse_json(text):
    def pairs(items):
        out = {}
        for k, v in items:
            if k in out:
                raise ValueError('Duplicate JSON key: ' + k)
            out[k] = v
        return out
    def invalid(value):
        raise ValueError('Invalid JSON constant: ' + value)
    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)


def edges(path):
    return path.get('links') or path.get('edges') or []


def ends(edge):
    return (edge.get('fromId', edge.get('from_id', edge.get('source_id'))),
            edge.get('toId', edge.get('to_id', edge.get('target_id'))))


def version_hash(value):
    match = re.fullmatch(r'artifact-version:sha256:([0-9a-fA-F]{64})', value or '')
    return match.group(1).lower() if match else None


def verify(record, source_pdf=None):
    report = {'integrity': {'status': 'passed', 'errors': []},
              'structure': {'status': 'passed', 'errors': []},
              'source_identity': {'status': 'passed', 'errors': [], 'current_file_check': 'not_rechecked'},
              'scientific_reading': {'verification': 'operator_entry_only; not scientifically certified'}}
    def require(group, condition, message):
        if not condition:
            report[group]['errors'].append(message)
            report[group]['status'] = 'failed'
    try:
        canonical = record['canonical_payload']
        payload = record['payload']
        require('integrity', isinstance(canonical, str), 'canonical_payload must be a string')
        if isinstance(canonical, str):
            digest = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
            require('integrity', record.get('record_id') == 'derived-observation:sha256:' + digest, 'record_id does not hash exact canonical_payload UTF-8 bytes')
            require('integrity', strict_equal(parse_json(canonical), payload), 'parsed canonical_payload differs from payload (strict JSON types)')
        require('integrity', record.get('hash_algorithm') == 'SHA-256', 'hash_algorithm must be SHA-256')
        require('structure', payload.get('schema_version') in ['generic-observation-reuse/1.4', 'generic-observation-reuse/1.5'], 'expected supported schema 1.4 or 1.5')
        ref, original = payload['reference'], payload['original_result']
        for field in ['paper_id', 'snapshot_id', 'claim_id', 'observation_id', 'anchor_id']:
            require('structure', isinstance(ref.get(field), str) and bool(ref[field]), 'missing reference: ' + field)
        for field in ['paper_id', 'snapshot_id', 'claim_id']:
            require('structure', ref.get(field) == original.get(field), 'reference differs from original result: ' + field)
        field = payload['path_origin']['field']
        require('structure', field in ['shortest_evidence_path', 'observation_evidence_path'], 'unsupported inspection path field')
        selected = payload['selected_inspection_path']
        require('structure', strict_equal(selected, original.get(field)), 'selected inspection path differs from original_result path')
        nodes = selected['nodes']
        ids = [n['id'] for n in nodes]
        require('structure', len(ids) == len(set(ids)), 'duplicate inspection node IDs')
        by_id = {n['id']: n for n in nodes}
        anchor, observation = payload['selected_source_anchor'], payload['selected_observation']
        for item, key, kind in [(anchor, 'anchor_id', 'source_anchor'), (observation, 'observation_id', 'observation')]:
            require('structure', item.get('id') == ref.get(key) and item.get('kind') == kind and strict_equal(item, by_id.get(ref.get(key))), 'selected node is not exact referenced path object: ' + key)
        grounded = any(e.get('relation') == 'grounds' and ends(e) == (ref['anchor_id'], ref['observation_id']) for e in edges(selected))
        require('structure', grounded, 'selected anchor lacks direct grounds edge to observation')
        modern = payload.get('schema_version') == 'generic-observation-reuse/1.5'
        report['structure']['relation_policy'] = 'adapter reasoning relations (1.5)' if modern else 'historical unrestricted directed reachability (1.4)'
        def allowed(edge):
            if not modern:
                return True
            source_id, target_id = ends(edge)
            source, target = by_id.get(source_id, {}), by_id.get(target_id, {})
            return (target_id == ref['claim_id'] and target.get('kind') == 'atomic_claim' and source.get('kind') == 'reasoning' and edge.get('relation') in {'infers', 'supports', 'contradicts', 'qualifies'}) or (target.get('kind') == 'reasoning' and source.get('kind') in {'observation', 'reasoning'} and edge.get('relation') == 'premise_for')
        reached, queue = set(), [ref['observation_id']]
        while queue:
            current = queue.pop()
            if current in reached:
                continue
            reached.add(current)
            queue.extend(t for e in edges(selected) for s, t in [ends(e)] if s == current and t in by_id and t not in reached and allowed(e))
        reaches = ref['claim_id'] in reached
        require('structure', reaches, 'observation has no directed recorded route to current claim')
        require('structure', payload.get('structural_link_state') == 'linked' and payload.get('observation_reaches_claim') is True and payload.get('direct_anchor_observation_edge_recorded') is True, 'declared structural states must be linked/true')
        require('structure', strict_equal(payload.get('original_scope'), original.get('scope')), 'original_scope differs from original result')
        direct = version_hash(anchor.get('artifact_version_id'))
        if direct:
            expected, resolution = [direct], 'direct_anchor'
        else:
            locations = {version_hash(ends(e)[0]) for p in [original.get('shortest_evidence_path', {}), selected] for e in edges(p) if e.get('relation') == 'locates' and ends(e)[1] == ref['anchor_id']}
            locations.discard(None)
            expected, resolution = (sorted(locations), 'recorded_locates') if len(locations) == 1 else ([], 'unresolved')
        identity = payload['source_identity_check']
        report['source_identity'].update({'resolution': resolution, 'expected_hashes': expected, 'recorded_check_state': identity.get('check_state')})
        require('source_identity', identity.get('resolution') == resolution, 'recorded resolution differs from selected-anchor binding')
        require('source_identity', strict_equal(identity.get('expected_artifact_hashes'), expected), 'recorded expected hashes differ from selected-anchor binding')
        observed, state = identity.get('observed_source_hash'), identity.get('check_state')
        if observed is not None:
            require('source_identity', isinstance(observed, str) and bool(re.fullmatch('[a-f0-9]{64}', observed)), 'invalid observed_source_hash')
            correct = 'missing_expected_hash' if not expected else ('verified' if observed in expected else 'mismatch')
            require('source_identity', state == correct, 'recorded file identity state contradicts its hashes')
        else:
            require('source_identity', state in ['pending', 'error'], 'unhashed source must be pending or error')
        if source_pdf is not None:
            actual = hashlib.sha256(Path(source_pdf).read_bytes()).hexdigest()
            current_state = 'missing_expected_hash' if not expected else ('verified' if actual in expected else 'mismatch')
            report['source_identity'].update({'current_file_check': current_state, 'current_source_hash': actual})
            require('source_identity', current_state == 'verified', 'supplied PDF cannot be verified against selected-anchor version')
        report['scientific_reading']['recorded_entry'] = payload.get('source_reading')
    except (KeyError, TypeError, ValueError, OSError, AttributeError) as exc:
        require('structure', False, 'Malformed or unreadable record: ' + str(exc))
    report['passed'] = all(report[k]['status'] == 'passed' for k in ['integrity', 'structure', 'source_identity'])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record', required=True, type=Path)
    parser.add_argument('--source-pdf', type=Path)
    args = parser.parse_args()
    try:
        report = verify(parse_json(args.record.read_text(encoding='utf-8')), args.source_pdf)
    except (OSError, ValueError) as exc:
        report = {'passed': False, 'input_error': str(exc)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
