#!/usr/bin/env python3
"""Reproduce the paper's retrieval, argument, and research results in one environment."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import venv

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True, help='New output directory')
    parser.add_argument('--live', action='store_true', help='Also run fresh model comparisons and public-site queries; requires GPT_API_KEY')
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        parser.error('Run with Python 3.12')
    if args.live and not os.environ.get('GPT_API_KEY'):
        parser.error('--live requires GPT_API_KEY in the environment')
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=False)
    environment = output / 'environment'
    report = {'passed': False, 'live': args.live, 'steps': []}
    env = os.environ.copy()
    env.update(PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1', DEEPSHOVEL_REPO=str(ROOT))
    env.pop('PYTHONPATH', None)
    def run(name, command):
        start = time.monotonic()
        with (output / f'{name}.log').open('w') as log:
            proc = subprocess.run([str(x) for x in command], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        report['steps'].append({'name': name, 'exit_code': proc.returncode, 'seconds': time.monotonic()-start})
        print(('PASS ' if proc.returncode == 0 else 'FAIL ') + name, flush=True)
        if proc.returncode:
            raise RuntimeError(f'{name}: see {output / (name + ".log")}')
    try:
        run('release-files', [sys.executable, ROOT / 'scripts/verify-release.py'])
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        run('dependencies', [python, '-m', 'pip', 'install', '-r', ROOT / 'repro/requirements-paper.lock'])
        run('retrieval-metrics', [python, ROOT / 'artifact/evaluation/evaluate.py'])
        run('argument-expansion', ['node', '--experimental-strip-types', '--no-warnings', ROOT / 'artifact/arguments/verify.mjs', '--out', output / 'arguments'])
        run('semantic-metrics', [python, ROOT / 'artifact/semantic-evaluation/verify_results.py'])
        run('semantic-inputs', [python, ROOT / 'artifact/semantic-evaluation/run_fixed_candidate_ablation.py', '--prepare-only', '--out', output / 'prepared-model-inputs'])
        run('research-sources', [python, ROOT / 'artifact/gp15/fetch_external_sources.py'])
        run('research-claims', [python, ROOT / 'artifact/gp15/verify_demo.py'])
        run('research-trace', [python, ROOT / 'artifact/research-trace/verify_trace.py'])
        run('research-computation', [python, ROOT / 'artifact/gp15/run_portable.py', '--out', output / 'gp15'])
        if args.live:
            run('fresh-model-comparisons', [python, ROOT / 'artifact/semantic-evaluation/run_fixed_candidate_ablation.py', '--out', output / 'fresh-model-comparisons'])
            run('fresh-source-passages', [python, ROOT / 'artifact/semantic-evaluation/run_source_passages_baseline.py', '--out', output / 'fresh-source-passages'])
            run('fresh-public-queries', [python, ROOT / 'artifact/semantic-evaluation/run_semantic_live.py', '--cases', ROOT / 'artifact/semantic-evaluation/semantic-cases.frozen.json', '--out', output / 'fresh-public-queries'])
            calls = list((output / 'fresh-model-comparisons').glob('q*.json'))
            if len(calls) != 60 or any(json.loads(p.read_text())['status'] != 'ok' for p in calls):
                raise RuntimeError('Fresh comparison calls did not all complete; inspect their records')
            passages = list((output / 'fresh-source-passages').glob('q*.json'))
            if len(passages) != 30 or any(json.loads(p.read_text())['status'] != 'ok' for p in passages):
                raise RuntimeError('Fresh source-passage calls did not all complete; inspect their records')
            live = json.loads((output / 'fresh-public-queries/results.json').read_text())
            if len(live) != 30 or any(r['status'] != 'complete' for r in live):
                raise RuntimeError('Public queries did not all complete; inspect their records')
        report['passed'] = True
    finally:
        (output / 'report.json').write_text(json.dumps(report, indent=2)+'\n')
        print('Report: ' + str(output / 'report.json'), flush=True)


if __name__ == '__main__':
    main()
