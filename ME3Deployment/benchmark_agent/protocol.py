import copy,re
from pathlib import Path
from benchmark_m6.protocol import SESSIONS as BASE,METHODS as BASE_METHODS
ROOT=Path(__file__).resolve().parents[1]
PHASE=ROOT/'evidence/benchmark_agent'
FROZEN=ROOT/'evidence/completion/autonomous-20261020-001/frozen-candidate-001'
VERSION='agent-final-v1'
LIMITS={'asr':48,'reasoning':60,'tts':64,'commit_item':0}
SESSIONS=copy.deepcopy(BASE)
SESSIONS['F'][-1]['script']=['Tell me everything currently saved on my shopping list.']
SESSIONS['F'][-1]['expected']='Actual list_items MCP, correct spoken six-row list, standalone read-only SQLite verification, all six existing rows unchanged. No new write.'
TRIALS={t['id']:{**t,'session':s} for s,items in SESSIONS.items() for t in items}
METHODS={**BASE_METHODS,'generation':'Actual controller generation/request identity, no inferred onset generation','primary_submission':'First nonzero substantive-response callback; excludes listener cues','reasoning':'Whole AgentRouter answer interval, including model steps and MCP tools. Separate LLM-step and task-RPC durations are reported. Not comparable to historical pure text-call duration.','LLM_TTFT':'NOT_MEASURED: nonstreaming generateContent returns a complete response; request duration is not first-token latency.','task':'Read-only existing six-row list after functional six-ruler commit; differs from historical writes, no task speedup comparison.','controlled_faults':'All local delay/timeout hooks off; earlier controlled trials excluded from benchmark.','JSD':'NOT_MEASURED without genuine aligned human timing reference; never zero-filled','GPT4o_quality':'NOT_MEASURED without authorized evaluator/results; operator correctness is separate','p95':'Nearest rank only at n>=20 available observations; project reporting rule, not professor threshold','stop':'Quota/auth/nonrecoverable service/protocol/cleanup error or exact budget exhaustion stops; no hidden retry. Preserve failures/unstarted.'}
def run_path(name):
    if not re.fullmatch(r'run-[0-9]{8}-[0-9]{3}',name):raise ValueError('invalid_benchmark_run')
    return PHASE/name
