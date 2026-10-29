"""Run frozen comparisons through the public two-stage search API."""
import argparse
import concurrent.futures
import hashlib
import json
import subprocess
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--base-url', default='https://deepshovel.iiot-shxt.xyz')
    parser.add_argument('--workers', type=int, default=2)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    cases = json.loads(args.cases.read_text())['cases']
    (args.out / 'protocol.json').write_text(json.dumps({
        'cases_sha256': hashlib.sha256(args.cases.read_bytes()).hexdigest(),
        'base_url': args.base_url, 'workers': args.workers,
        'candidate_policy': 'All preview candidates, in preview order; no forced target insertion',
    }, indent=2))

    def run(case):
        dest = args.out / case['id']
        dest.mkdir()
        base = dict(search_protocol='evidence-retrieval/2.3', query=case['query'],
                    intent='claim_search', corpus='cross_paper', limit=50)
        def call(stage, **extra):
            body = dict(base, stage=stage, **extra)
            (dest / (stage + '-request.json')).write_text(json.dumps(body, ensure_ascii=False, indent=2))
            start = time.monotonic()
            proc = subprocess.run(['curl', '--silent', '--show-error', '--fail-with-body',
                args.base_url + '/api/evidence-search', '-H', 'Content-Type: application/json',
                '-H', 'Origin: ' + args.base_url, '-H', 'Accept: application/x-ndjson',
                '--data-binary', '@-'], input=json.dumps(body), capture_output=True, text=True)
            (dest / (stage + '-raw.txt')).write_text(proc.stdout)
            (dest / (stage + '-timing.json')).write_text(json.dumps({
                'elapsed_seconds': time.monotonic() - start, 'curl_exit_code': proc.returncode,
                'response_sha256': hashlib.sha256(proc.stdout.encode()).hexdigest(),
            }, indent=2))
            proc.check_returncode()
            lines = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
            envelope = next((x for x in reversed(lines) if x.get('type') == 'result'), None)
            data = envelope['body'] if envelope else lines[0]
            (dest / (stage + '-response.json')).write_text(json.dumps(data, ensure_ascii=False, indent=2))
            if envelope and envelope['status'] >= 400:
                raise RuntimeError(str(data))
            return data
        result = {'id': case['id'], 'accepted_relations': case['accepted_relations']}
        try:
            preview = call('preview')
            refs = [{'snapshot_id': x['snapshotId'], 'claim_id': x['claim']['id']}
                    for x in preview.get('previewResults', [])]
            result['target_in_preview'] = any(x['claim_id'] == case['claim_id'] for x in refs)
            final = call('ai_rerank', query_plan_handoff=preview['queryPlanHandoff'], candidate_refs=refs) if refs else {}
            members = []
            for cluster in final.get('clusters', []):
                members.extend([cluster.get('representative', {}), *cluster.get('members', [])])
            target = next((x for x in members if x.get('claim', {}).get('id') == case['claim_id']), None)
            result['target_in_final'] = target is not None
            result['relation'] = target.get('queryRelation', {}).get('relation') if target else None
            result['accepted'] = result['relation'] in case['accepted_relations']
            result['status'] = 'complete'
        except Exception as error:
            result.update(status='error', error=str(error))
        (dest / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(run, cases))
    (args.out / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
