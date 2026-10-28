"""Run both bundled offline contract replays; use Python with vendor requirements."""
import os,subprocess,sys,argparse
from pathlib import Path
parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--out",type=Path);args=parser.parse_args()
root=Path(__file__).resolve().parents[1]
env=os.environ.copy();env.pop('DEEPSHOVEL_REPO',None);env['PYTHONPATH']=str(root/'vendor/compiler');env['PYTHONDONTWRITEBYTECODE']='1'
for name in ['scope_contract','admission_contract']:
 command=[sys.executable,str(root/'replays'/name/'run.py'),'--repo',str(root/'vendor')]
 if args.out: command += ['--out',str(args.out.resolve()/name)]
 subprocess.run(command,check=True,env=env)
