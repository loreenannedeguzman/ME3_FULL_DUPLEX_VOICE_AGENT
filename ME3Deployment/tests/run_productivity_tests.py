"""Focused provider/account/device-free WB07 tests with staged package first."""
import argparse,os,sys,unittest,zoneinfo
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=Path(os.environ.get('ME3_TEST_ROOT','/opt/ME3Deployment' if os.name=='nt' else '/home/pi/me3-duplex'))
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(HERE.parent))
parser=argparse.ArgumentParser();parser.add_argument('--tzdata',type=Path);parser.add_argument('--test',action='append');args=parser.parse_args()
if args.tzdata is not None:
 if not (args.tzdata/'Asia/Manila').is_file() or not (args.tzdata/'America/New_York').is_file():raise ValueError('real_IANA_tzfiles_required')
 zoneinfo.reset_tzpath([str(args.tzdata.resolve())])
sys.path.insert(0,str(HERE))
suite=unittest.defaultTestLoader.loadTestsFromNames(args.test) if args.test else unittest.defaultTestLoader.discover(str(HERE),pattern='test_productivity.py')
result=unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(not result.wasSuccessful())
