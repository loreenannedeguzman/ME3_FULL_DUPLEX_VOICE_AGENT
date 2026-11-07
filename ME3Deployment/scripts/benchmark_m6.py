"""M6 fixed-script Pi benchmark controller / cross-platform pure event reducer."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from benchmark_m6.storage import run_path,write_new
from benchmark_m6.analyze import analyze
p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='mode',required=True)
c=sub.add_parser('console');c.add_argument('--run',required=True);c.add_argument('--new',action='store_true')
a=sub.add_parser('analyze');a.add_argument('--run',required=True);a.add_argument('--output',required=True)
args=p.parse_args()
if args.mode=='console':
 from benchmark_m6.console import console
 console(args.run,args.new)
else:
 folder=run_path(args.run);target=(folder/args.output).resolve()
 if target.parent!=folder.resolve() or target.suffix!='.json':raise SystemExit('output must be a fresh JSON in run directory')
 data=analyze(folder);write_new(target,data);print(json.dumps({'accounting':data['trial_accounting'],'normal':data['steady_normal_all_available_including_semantic_failures'],'split':data['split_recovery'],'interruptions':data['interruption_summary']},indent=2))
