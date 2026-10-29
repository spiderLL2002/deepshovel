"""Adapter for the existing deterministic research runtime; no network or refit."""
import argparse
import json
from pathlib import Path
from reviewer_diagnostics_v2 import run

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--plan',type=Path,required=True)
    args=parser.parse_args()
    config=json.loads(args.plan.read_text())['config']
    task=Path(__file__).resolve().parents[2]
    print(json.dumps(run(task/config['protocol'],task/config['out_dir']),ensure_ascii=False))
