import argparse,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from me3_completion.live_console import console
p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--new',action='store_true');a=p.parse_args();console(a.run,a.new)
