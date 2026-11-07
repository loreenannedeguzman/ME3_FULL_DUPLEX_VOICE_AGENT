"""Corrected provider-free M6 reduction; accepts original Pi run or metadata-only Windows copy."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from benchmark_m6.analyze_v2 import analyze
from benchmark_m6.storage import write_new
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run-folder',required=True);p.add_argument('--output',required=True);args=p.parse_args()
folder=Path(args.run_folder).resolve();target=(folder/args.output).resolve()
if target.parent!=folder or target.suffix!='.json':raise SystemExit('output must be a fresh JSON inside the run folder')
data=analyze(folder);write_new(target,data);print(json.dumps({key:data[key] for key in ('analysis_provenance','trial_accounting','steady_normal_all_available_including_semantic_failures','split_recovery','interruption_summary','request_inventory')},indent=2))
