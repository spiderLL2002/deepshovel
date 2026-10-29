#!/usr/bin/env python3
"""Source-passages control; imports public production code without editing it.

DEEPSHOVEL_REPO=/checkout DEEPSHOVEL_ENV_FILE=/private/compiler.env \
  /checkout/compiler/.venv/bin/python run_source_passages_baseline.py

Credentials may instead be supplied as GPT_API_KEY / DEEPSEEK_API_KEY.
NODE_BINARY selects Node >=22.13. CONCURRENCY defaults to two logical calls;
each retains production's hedging policy. No external retries or time deadline.
"""
from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent


def sha(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, default=HERE / 'semantic-cases.frozen.json')
    parser.add_argument('--sources', type=Path, default=HERE / 'sources')
    parser.add_argument('--out', type=Path, default=HERE / 'source-passages-baseline')
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    repo = Path(os.environ['DEEPSHOVEL_REPO']).resolve()
    if env_file := os.environ.get('DEEPSHOVEL_ENV_FILE'):
        for line in Path(env_file).read_text().splitlines():
            line = line.strip().removeprefix('export ')
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            if key.strip() not in {'GPT_API_KEY', 'DEEPSEEK_API_KEY'}:
                continue
            parsed = shlex.split(value, comments=True)
            if parsed:
                os.environ.setdefault(key.strip(), parsed[0])
    secrets = [os.environ[k] for k in ('GPT_API_KEY', 'DEEPSEEK_API_KEY') if os.environ.get(k)]
    sys.path.insert(0, str(repo / 'compiler'))
    from compiler_service import config
    from compiler_service.graph_contract import canonical_json
    from compiler_service.model_client import ModelJsonClient, INTERACTIVE_COMPLETION_PROFILE
    from compiler_service.retrieval_providers import _INTERACTIVE_RELATION_PROMPT
    from compiler_service.retrieval_contract import InteractiveRelationRankingOutput, SearchScope
    from openai.lib._pydantic import to_strict_json_schema

    def write(path, obj):
        data = json.dumps(obj, ensure_ascii=False, indent=2, default=str) + '\n'
        for secret in secrets:
            assert secret not in data, 'Refusing to persist credential material'
        path.write_text(data)

    def diagnostics(exc):
        return [asdict(x) if is_dataclass(x) else x.model_dump(mode='json')
                for x in getattr(exc, 'diagnostics', ())]

    args.out.mkdir(parents=True, exist_ok=True)
    frozen_bytes = args.cases.read_bytes()
    cases = json.loads(frozen_bytes)['cases']
    projected = json.loads(subprocess.check_output([
        os.environ.get('NODE_BINARY', 'node'), '--experimental-strip-types', '--no-warnings',
        str(HERE / 'project_ablation_arguments.mjs'), str(args.cases), str(args.sources),
    ]))
    for candidate in projected.values():
        candidate['scope'] = SearchScope.model_validate(candidate['scope'], strict=True).model_dump(
            mode='json', exclude_none=True, exclude_defaults=True)
    passage_counts = {}
    for source, candidate in projected.items():
        passages, seen = [], set()
        anchor_nodes = [n for n in candidate['argument']['nodes'] if n['kind'] == 'source_anchor']
        for node in anchor_nodes:
            text = node['statement']
            if not text or text in seen:
                continue
            seen.add(text)
            passages.append({'source_anchor_id': node['id'], 'text': text})
        passage_counts[source] = {'source_anchor_nodes': len(anchor_nodes),
                                 'retained_passages': len(passages),
                                 'characters': sum(len(x['text']) for x in passages)}
        candidate['argument'] = {'source_passages': passages}
    source_files = ['compiler/compiler_service/retrieval_providers.py',
                    'compiler/compiler_service/retrieval_contract.py',
                    'compiler/compiler_service/model_client.py',
                    'compiler/compiler_service/gpt_responses.py',
                    'compiler/compiler_service/config.py',
                    'lib/evidence-retrieval/arguments.ts']
    manifest = {
        'experiment': 'fixed-candidate original-source-passages control', 'created_at': now(),
        'cases_sha256': sha(frozen_bytes), 'logical_concurrency': int(os.environ.get('CONCURRENCY', '2')),
        'model': config.GPT_MODEL, 'base_url': config.GPT_BASE_URL,
        'reasoning_effort': 'medium', 'interactive': True, 'timeout': None,
        'hedge_start_seconds': [0, 10, 20], 'fallback_model': config.DEEPSEEK_MODEL,
        'fallback_configured': bool(os.environ.get('DEEPSEEK_API_KEY')),
        'profile': asdict(INTERACTIVE_COMPLETION_PROFILE),
        'prompt_sha256': sha(_INTERACTIVE_RELATION_PROMPT),
        'schema_sha256': sha(canonical_json(to_strict_json_schema(InteractiveRelationRankingOutput))),
        'production_source_sha256': {s: sha((repo / s).read_bytes()) for s in source_files},
        'public_graph_sha256': {s: sha((args.sources / f'{s}-graph.json').read_bytes()) for s in projected},
        'design': 'one designated candidate per query; argument replaced by source_passages from source_anchor nodes of existing production projector; identical quote text deduplicated, first anchor ID and order retained; no inferred premises or additional sources',
        'not_token_matched': True, 'passage_counts': passage_counts,
        'limits': 'single production invocation per condition; production hedged attempts retained; no experiment retry; no application timeout; no retrieval/query-planning confound',
        'scoring': 'frozen accepted relation membership; direction scored separately; no label edits',
    }
    if (args.out / 'manifest.json').exists():
        old = json.loads((args.out / 'manifest.json').read_text())
        for key in ('cases_sha256', 'production_source_sha256', 'prompt_sha256', 'schema_sha256'):
            assert old[key] == manifest[key], f'Resume mismatch: {key}'
    else:
        write(args.out / 'manifest.json', manifest)
    write(args.out / 'projected-candidates.json', projected)
    write(args.out / 'prompt-schema.json', {'system_prompt': _INTERACTIVE_RELATION_PROMPT,
        'response_schema': to_strict_json_schema(InteractiveRelationRankingOutput)})
    tasks = []
    frozen_inputs = []
    for index, case in enumerate(cases):
        conditions = ['source_passages']
        for condition in conditions:
            candidate = {'id': 1, **deepcopy(projected[case['source']])}
            payload = {'query': case['query'], 'candidates': [candidate]}
            user_content = canonical_json(payload)
            frozen_inputs.append({'id': case['id'], 'condition': condition, 'user_content': user_content, 'user_content_sha256': sha(user_content)})
            target = args.out / f"{case['id']}.{condition}.json"
            if target.exists():
                assert json.loads(target.read_text())['user_content_sha256'] == sha(user_content)
                continue  # Never silently retry recorded failures.
            tasks.append((case, condition, user_content, target))
    # Freeze every model input before starting any executor/model call.
    frozen_path = args.out / 'frozen-inputs.json'
    frozen = {'cases_sha256': manifest['cases_sha256'], 'prompt_sha256': manifest['prompt_sha256'],
              'schema_sha256': manifest['schema_sha256'], 'inputs': frozen_inputs}
    if frozen_path.exists():
        assert json.loads(frozen_path.read_text()) == frozen, 'Frozen model inputs changed'
    else:
        write(frozen_path, frozen)
    freeze_hash = sha(frozen_path.read_bytes())
    hash_path = args.out / 'frozen-inputs.sha256'
    if hash_path.exists():
        assert hash_path.read_text().strip() == freeze_hash
    else:
        hash_path.write_text(freeze_hash + '\n')
    if args.prepare_only:
        print(json.dumps({'prepared': len(cases), 'pending_calls': len(tasks)}), flush=True)
        return
    assert os.environ.get('GPT_API_KEY'), 'GPT_API_KEY is required'

    def run(task):
        case, condition, content, target = task
        record = {'id': case['id'], 'condition': condition, 'started_at': now(),
                  'query': case['query'], 'claim_id': case['claim_id'], 'snapshot_id': case['snapshot_id'],
                  'user_content': content, 'user_content_sha256': sha(content),
                  'system_prompt_sha256': manifest['prompt_sha256'],
                  'raw_attempt_outputs': [], 'status': 'running'}
        write(target, record)
        client = ModelJsonClient.from_api_key(os.environ['GPT_API_KEY'],
                    deepseek_api_key=os.environ.get('DEEPSEEK_API_KEY'))
        def observe(metadata, text):
            record['raw_attempt_outputs'].append({'metadata': metadata, 'text': text})
        client._client.observer = observe
        if client._fallback_client is not None:
            original = client._fallback_client.acreate
            async def traced_fallback(**request):
                result = await original(**request)
                observe({'requested_model': config.DEEPSEEK_MODEL}, result.choices[0].message.content)
                return result
            client._fallback_client.acreate = traced_fallback
        start = time.monotonic()
        try:
            def coverage(wire):
                if [d.id for d in wire.decisions] != [1]:
                    from compiler_service.errors import ContractValidationError, ContractIssue
                    raise ContractValidationError(ContractIssue.RETRIEVAL_RELATION_CANDIDATE_COVERAGE, loc=('decisions',))
            result = client.complete(system_prompt=_INTERACTIVE_RELATION_PROMPT,
                user_content=content, schema=InteractiveRelationRankingOutput,
                validate=coverage, profile=INTERACTIVE_COMPLETION_PROFILE)
            record.update(status='ok', output=result.value.model_dump(mode='json'),
                model=result.model, attempts=result.attempts, output_content_sha256=result.content_sha256,
                diagnostics=diagnostics(result))
        except Exception as exc:
            record.update(status='failed', error_type=type(exc).__name__, diagnostics=diagnostics(exc))
        finally:
            record.update(elapsed_seconds=time.monotonic()-start, finished_at=now())
            write(target, record)
        print(json.dumps({'id': case['id'], 'condition': condition, 'status': record['status'],
                          'elapsed_seconds': round(record['elapsed_seconds'], 2)}), flush=True)
        return record

    with ThreadPoolExecutor(max_workers=manifest['logical_concurrency']) as executor:
        for future in as_completed([executor.submit(run, task) for task in tasks]):
            future.result()
    assert sha(args.cases.read_bytes()) == manifest['cases_sha256'], 'Frozen cases changed during run'
    assert sha(frozen_path.read_bytes()) == freeze_hash, 'Frozen inputs changed during calls'
    summary = {'cases_sha256': manifest['cases_sha256'], 'conditions': {}, 'pairs': []}
    records = {}
    for case in cases:
        for condition in ('source_passages',):
            r = json.loads((args.out / f"{case['id']}.{condition}.json").read_text())
            records[(case['id'], condition)] = r
            s = summary['conditions'].setdefault(condition, {'total': 0, 'success': 0, 'failed': 0,
                         'accepted_relation': 0, 'accepted_relation_and_direction': 0})
            s['total'] += 1
            if r['status'] != 'ok':
                s['failed'] += 1
                continue
            s['success'] += 1
            decision = r['output']['decisions'][0]
            correct = decision['relation'] in case['accepted_relations']
            s['accepted_relation'] += correct
            s['accepted_relation_and_direction'] += correct and decision['direction'] == case['direction']
        summary['pairs'].append({'id': case['id'], 'accepted_relations': case['accepted_relations'],
            **{condition: records[(case['id'], condition)].get('output') for condition in ('source_passages',)}})
    write(args.out / 'summary.json', summary)
    print(json.dumps(summary['conditions']), flush=True)


if __name__ == '__main__':
    main()
