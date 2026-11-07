import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
p=argparse.ArgumentParser();sub=p.add_subparsers(dest='mode',required=True);c=sub.add_parser('console');c.add_argument('--run',required=True);c.add_argument('--new',action='store_true');c.add_argument('--source',default='source-live-001.json');c.add_argument('--inherit-budget');a=sub.add_parser('analyze');a.add_argument('--run',required=True);a.add_argument('--output',required=True);args=p.parse_args()
if args.mode=='console':
    from me3_m7.benchmark_console import console
    console(args.run,args.new,args.source,args.inherit_budget)
else:
    from me3_m7.benchmark_runner import run_path
    from me3_m7.analyze import analyze
    from benchmark_m6.storage import write_new
    folder=run_path(args.run);target=(folder/args.output).resolve()
    if target.parent!=folder.resolve() or target.suffix!='.json':raise ValueError('fresh_output_in_run_required')
    data=analyze(folder);write_new(target,data);print(json.dumps({'accounting':data['trial_accounting'],'normal':data['steady_normal_all_available_including_semantic_failures'],'split':data['split_recovery'],'matched':data['m6_matched_comparison']},indent=2))
