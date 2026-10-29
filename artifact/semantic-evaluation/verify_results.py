"""Verify preserved model records and recompute the paired diagnostic score."""
import hashlib
import json
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent
import argparse
parser = argparse.ArgumentParser()
parser.add_argument('--run', default='fixed-candidate-ablation-v2')
RUN = ROOT / parser.parse_args().run
sha = lambda value: hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()
cases = json.loads((ROOT / 'semantic-cases.frozen.json').read_text())['cases']
manifest = json.loads((RUN / 'manifest.json').read_text())
assert sha((ROOT / 'semantic-cases.frozen.json').read_bytes()) == manifest['cases_sha256']
for name, expected in manifest['public_graph_sha256'].items():
    assert sha((ROOT / 'sources' / (name + '-graph.json')).read_bytes()) == expected
prompt = json.loads((RUN / 'prompt-schema.json').read_text())['system_prompt']
assert sha(prompt) == manifest['prompt_sha256']
scores = {key: [] for key in ('claim_scope', 'full_argument')}
latencies = {key: [] for key in scores}
relation_scores = {key: [] for key in scores}
for case in cases:
    pair = {}
    for condition in scores:
        record = json.loads((RUN / f"{case['id']}.{condition}.json").read_text())
        assert sha(record['user_content']) == record['user_content_sha256']
        assert record['system_prompt_sha256'] == manifest['prompt_sha256']
        pair[condition] = json.loads(record['user_content'])
        assert pair[condition]['query'] == case['query']
        accepted = False
        relation_accepted = False
        if record['status'] == 'ok':
            winner = next(x for x in record['raw_attempt_outputs']
                          if sha(x['text']) == record['output_content_sha256'])
            assert json.loads(winner['text']) == record['output']
            decision = record['output']['decisions'][0]
            relation_accepted = decision['relation'] in case['accepted_relations']
            accepted = relation_accepted and decision['direction'] == case['direction']
        scores[condition].append(accepted)
        relation_scores[condition].append(relation_accepted)
        latencies[condition].append(record['elapsed_seconds'])
    argument = pair['full_argument']['candidates'][0].pop('argument')
    assert argument['claim_id'] == case['claim_id']
    assert pair['full_argument'] == pair['claim_scope']
print(json.dumps({key: {'accepted_relation': sum(relation_scores[key]), 'accepted_relation_and_direction': sum(scores[key]), 'total': len(scores[key]),
                       'mean_seconds': mean(latencies[key])} for key in scores}, indent=2))

passages_run = ROOT / 'source-passages-baseline'
passages_manifest = json.loads((passages_run / 'manifest.json').read_text())
assert passages_manifest['cases_sha256'] == manifest['cases_sha256']
passages_correct = 0
for case in cases:
    record = json.loads((passages_run / f"{case['id']}.source_passages.json").read_text())
    assert sha(record['user_content']) == record['user_content_sha256']
    assert record['system_prompt_sha256'] == passages_manifest['prompt_sha256']
    content = json.loads(record['user_content'])
    assert content['query'] == case['query']
    full = json.loads((ROOT / 'fixed-candidate-ablation-v2' / f"{case['id']}.full_argument.json").read_text())
    nodes = json.loads(full['user_content'])['candidates'][0]['argument']['nodes']
    anchors = {n['id']: n['statement'] for n in nodes if n['kind'] == 'source_anchor'}
    for passage in content['candidates'][0]['argument']['source_passages']:
        assert anchors[passage['source_anchor_id']] == passage['text']
    if record['status'] == 'ok':
        winner = next(x for x in record['raw_attempt_outputs'] if sha(x['text']) == record['output_content_sha256'])
        assert json.loads(winner['text']) == record['output']
        passages_correct += record['output']['decisions'][0]['relation'] in case['accepted_relations']
print(json.dumps({'source_passages': {'accepted_relation': passages_correct, 'total': len(cases)}}))
