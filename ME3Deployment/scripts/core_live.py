import argparse,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
p=argparse.ArgumentParser();p.add_argument('mode',choices=['console']);p.add_argument('--run',required=True);p.add_argument('--new',action='store_true');a=p.parse_args()
from me3_core.live_console import console
console(a.run,a.new)
