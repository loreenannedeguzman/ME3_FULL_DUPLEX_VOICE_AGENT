"""Preregistered final-candidate benchmark; historical M6 scripts retained, new task isolated."""
import copy,re
from pathlib import Path
from benchmark_m6.protocol import SESSIONS as BASE,METHODS as BASE_METHODS
ROOT=Path(__file__).resolve().parents[1]
PHASE=ROOT/'evidence/benchmark_final'
FROZEN=ROOT/'evidence/completion/final-20261020-001/frozen-candidate-001'
VERSION='final-benchmark-v1'
LIMITS={'asr':48,'reasoning':45,'tts':64,'commit_item':1}
SESSIONS=copy.deepcopy(BASE)
SESSIONS['F'][-1]['script']=['Add six rulers to my shopping list.','Listen to the exact proposal before saying Confirm.','After verified save, say Read my shopping list.']
SESSIONS['F'][-1]['expected']='correct six-ruler proposal, direct confirmation, real MCP commit, independent SQLite six rulers; original five rows unchanged; spoken read-back'
TRIALS={x['id']:{**x,'session':s} for s,items in SESSIONS.items() for x in items}
METHODS={**BASE_METHODS,'generation':'actual core_turn_started and request IDs; VAD onset alone does not advance a generation','primary_submission':'first nonzero callback attributed to a substantive response; listener cues are counted separately, never substituted for answer onset','task':'fresh six-ruler addition; no deletion/reset; differs from historical five-pencil task, so task timing is a separate case','thresholds':'No course quality/latency threshold invented; n>=20 empirical p95 is a project reporting convention','JSD':'NOT_MEASURED without genuine aligned human reference timing; zero is not a missing value','GPT4o_quality':'NOT_MEASURED without separately authorized evaluator access/results; operator correctness is separate','stop':'quota/auth/nonrecoverable service/protocol/cleanup error or budget exhaustion stops the session; no hidden retry/relaunch; recovered timeout remains a failed trial and next planned trial may proceed'}
def run_path(name):
    if not re.fullmatch(r'run-[0-9]{8}-[0-9]{3}',name):raise ValueError('invalid_benchmark_run_id')
    return PHASE/name
