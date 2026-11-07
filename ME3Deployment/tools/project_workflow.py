"""Render/check the ME3 management board. No runtime, network or provider imports."""
import argparse,json,re
from pathlib import Path
from datetime import datetime,timezone,timedelta

def load(root):return json.loads((root/'PROJECT_BOARD.json').read_text(encoding='utf-8'))
def validate(board,root,check_evidence=True):
    blocks=board['work_blocks'];parents={b['id']:b for b in blocks};nodes={}
    allowed=set(board['status_definitions'])
    for b in blocks:
        assert re.fullmatch(r'WB-\d{2}',b['id']) and b['acceptance'] and b['required_evidence']
        for n in [b,*b['subblocks']]:
            assert n['id'] not in nodes,n['id'];nodes[n['id']]=n
            assert n['status'] in allowed,(n['id'],n['status'])
            if n['status']=='WAITING_PREREQUISITE':assert n.get('prerequisites'),n['id']
            if n['status']=='DEFERRED_BY_USER':assert any('User' in x or 'user' in x for x in n.get('prerequisites',[])),n['id']
        assert all(s['id'].startswith(b['id']+'.') for s in b['subblocks'])
        assert all(d in parents or d=='BASELINE' for d in b['depends_on'])
        if b['status']=='ACCEPTED':assert b['closure_evidence'],b['id']
    seen=set();visiting=set()
    def visit(id):
        if id in seen:return
        assert id not in visiting,'dependency cycle: '+id
        visiting.add(id)
        for d in parents[id]['depends_on']:
            if d!='BASELINE':visit(d)
        visiting.remove(id);seen.add(id)
    for id in parents:visit(id)
    assert board['next_work_block'] in nodes
    assert all(id in nodes for id in board['execution_order'])
    if board['management_status']=='ENGINEERING_PAUSED':assert board['active_work_block'] is None and board['resume_requires_user_instruction']
    for g in board['gates']:
        assert all(id in parents for id in g['blocks'])
        if g['status']=='ACCEPTED':assert all(parents[id]['status']=='ACCEPTED' for id in g['blocks'])
    for group in board['budget_snapshot'].values():
        if isinstance(group,dict) and 'usage' in group:
            assert all(type(n) is int and n>=0 and ((group.get('profile')=='standing' and k!='commit_item' and group['limits'][k] is None) or (type(group['limits'][k]) is int and n<=group['limits'][k])) for k,n in group['usage'].items())
    refs=board['evidence_index']
    for n in nodes.values():
        for ref in n.get('evidence',[])+n.get('closure_evidence',[]):assert ref in refs,(n['id'],ref)
    if check_evidence:
        for ref,e in refs.items():
            p=(root/e['path']).resolve();assert p.is_relative_to(root.resolve()) and p.is_file(),ref
    return {'parent_blocks':len(parents),'subblocks':len(nodes)-len(parents),'gates':len(board['gates']),'evidence_references':len(refs),'management_status':board['management_status'],'next_work_block':board['next_work_block']}

def render(b):
    out=[]
    def add(s=''):out.append(s)
    def bullets(rows):
        for row in rows:add('- '+row)
        add()
    def table(headers,rows):
        add('| '+' | '.join(headers)+' |');add('| '+' | '.join('---' for _ in headers)+' |')
        for row in rows:add('| '+' | '.join(str(x).replace('|','\\|').replace('\n',' ') for x in row)+' |')
        add()
    def evidence(refs):return ', '.join('['+ref+']('+b['evidence_index'][ref]['path']+')' for ref in refs) or 'Pending closure evidence'
    add('# ME3 project work plan and management workflow');add()
    add('Updated: '+b['updated_manila']+' (Asia/Manila).');add()
    add('**Engineering: '+b['management_status']+'. Overall: '+b['overall_status']+'. Next work: '+b['next_work_block']+'.**');add()
    add('This plan controls execution of the existing assignment and the user-defined full capability scope. A management update does not authorize resuming a user pause, create a new architecture audit, or replace the actual source/resource/evidence ledgers.');add()
    add('`PROJECT_BOARD.json` is the editable management state. This file is its generated human-readable plan. Use stable work IDs throughout code changes, tests, live scenarios, evidence and project records.');add()
    add('## Goals and completion boundary');add()
    bullets([
      'Run a general-purpose voice agent on Raspberry Pi 5 using Pipecat and MCP, with student-built ASR/TTS MCP services hosted on the Pi and on-device VAD. Hosted commercial models are permitted.',
      'Converse naturally and preserve user participation while listening, reasoning and speaking: context, pauses, hesitation, acknowledgments, corrections, interruptions and replacement intent.',
      'Discover real tools from their descriptions/schemas, execute authorized goals across applications/services, verify important outcomes and report partial or failed results honestly.',
      'Qualify real functionality before the accepted freeze and final standardized evaluation. Preserve reproducibility, model-suitability evidence and student handover material.'
    ])
    table(['User capability area','Work blocks'],[[str(a['order'])+'. '+a['name'], ', '.join(a['blocks'])] for a in b['capability_areas']])
    add('All eight areas remain in the intended scope. An unavailable prerequisite or user deferral stays visible and is not counted as a pass. Only a recorded user-approved scope amendment changes the completion obligations. A scoped available-capability demonstration is labelled separately from full project completion.');add()
    add('## Verified starting point');add()
    c=b['candidate'];add('- Current source-manifest revision: `'+c['source_revision']+'` ('+str(c['source_files'])+' source files). This is a manifest digest, not an invented Git commit.')
    r=c['full_regressions'];add('- Retained full automated regression evidence: Pi **'+str(r['pi']['passed'])+' passed, '+str(r['pi']['skipped'])+' skipped**; Windows **'+str(r['windows']['passed'])+' passed, '+str(r['windows']['skipped'])+' skipped**. Recorded source: `'+r.get('source_revision',c['source_revision'])+'`. '+r.get('reuse_basis','Exact-candidate results.'))
    add('- Latest live result: **'+c['latest_live_scope']+'**.')
    for gap in b['current_gaps']:add('- '+gap)
    add('- Earlier conversation, speaker, task, document and evaluation evidence remains revision-specific. Full-scope qualification recorded for this candidate: **'+str(c['qualified_full_scope'])+'**.');add()
    add('### Resource snapshot');add()
    snap=b['budget_snapshot'];add(snap['authority']);add()
    rows=[]
    for name in ('development','benchmark'):
        g=snap[name]
        for key,label in [('asr','ASR'),('reasoning','Reasoning'),('tts','Piper'),('commit_item','Shopping writes')]:rows.append([name,label,g['usage'][key],g['limits'][key] if g['limits'][key] is not None else 'Standing policy',g['limits'][key]-g['usage'][key] if g['limits'][key] is not None else 'Rolling/provider controls'])
    table(['Ledger','Resource','Used','Ceiling','Remaining'],rows)
    add('Finite historical test ceilings are retained in their original authority file. The active standing profile keeps cumulative counters, uses the existing14 hosted requests/minute guard and actual provider admission, and has no lifetime Piper request ceiling. Actual model/account quotas, audio-seconds and token limits must also be checked before hosted calls. Local music/reminder controls have separate grant-bound counters; they are not permission for more shopping writes. Compare each proposed session, including startup, against the actual remaining ledgers before launch.');add()
    add('## Master work board');add()
    table(['ID','Work block','Status','Priority','Dependencies'],[[x['id'],x['title'],x['status'],x['priority'],', '.join(x['depends_on'])] for x in b['work_blocks']])
    add('P0 = core correctness/acceptance/completion gate; P1 = required capability breadth and handover. P1 does not mean optional. WB-14 documentation progresses alongside engineering rather than blocking it.');add()
    add('```mermaid\nflowchart LR\n  A[WB-00 Management] --> B[WB-01 to 03 Core agent]\n  B --> C[WB-04 to 10 Capability coverage]\n  C --> D[WB-11 Integrated qualification]\n  D --> E[WB-12 Qualified freeze]\n  E --> F[WB-13 Evaluation]\n  A --> H[WB-14 Documentation alongside work]\n  F --> G[WB-15 Completion review]\n  H --> G\n```');add()
    add('### Execution queue on resumption');add()
    add(' → '.join(b['execution_order']));add();add(b['sequence_note']);add()
    add('Dependency links describe integration/acceptance ordering. They do not prohibit useful provider-free preparation of an independent subblock while an external prerequisite or operator is unavailable. Review the subblock prerequisites before switching work and log the switch.');add()
    add('## Work-block definitions');add()
    for x in b['work_blocks']:
        add('### '+x['id']+' — '+x['title']);add()
        add('**Status:** '+x['status']+' · **Priority:** '+x['priority']+' · **Dependencies:** '+', '.join(x['depends_on']));add()
        add('**Goal:** '+x['goal']);add();add('**Starting evidence:** '+x['existing_evidence_summary']);add()
        table(['Subblock','Work','Status','Prerequisite / evidence'],[[s['id'],s['title'],s['status'],'; '.join(s['prerequisites']) or (evidence(s['evidence']) if s['evidence'] else 'To be established in this block')] for s in x['subblocks']])
        if x['prerequisites']:add('**External/operational prerequisites:**');add();bullets(x['prerequisites'])
        add('**Exit criteria:**');add();bullets(x['acceptance'])
        add('**Closure evidence:**');add();bullets(x['required_evidence'])
        if x['closure_evidence']:add('Recorded closure: '+evidence(x['closure_evidence']));add()
    add('## Gates and status rules');add()
    table(['Gate','Meaning','Required blocks','Current status'],[[g['id'],g['name'],', '.join(g['blocks']),g['status']] for g in b['gates']])
    table(['Status','Meaning'],[[k,v] for k,v in b['status_definitions'].items()])
    add('An ACCEPTED management block does not establish engineering functionality. VERIFIED_SCOPED does not close its entire parent. Failed acceptance is retained while a new attempt is linked; it is never overwritten by a pass. Do not assign a project completion percentage from the number of blocks, because their scope and evidence differ.');add()
    add('## Operating workflow for every work packet');add()
    for i,rule in enumerate(b['control_rules'],1):add(str(i)+'. '+rule)
    add();add('Use this minimum packet before changing implementation or launching a live check:');add()
    add('```text\nwork_block_id: WB-01.1\nsupporting_work_blocks: []\nrequirement_or_observed_failure:\nbaseline_revision_and_configuration:\nhypothesis_and_smallest_change:\nprerequisites_and_authorization_scope:\nprovider_free_checks:\nlive_expected_result_and_pass_fail_if_needed:\nresource_and_effect_ceilings_including_startup:\nactual_results_and_usage:\nevidence_paths_and_hashes:\naffected_requirements_and_revalidation:\nnext_work_block_or_specific_blocker:\n```');add()
    add('### Closing a block and updating progress');add()
    bullets([
      'Archive the previous board/plan and changed project records before editing them. Keep old evidence IDs and trial identities unchanged.',
      'Update the relevant board subblock/parent, evidence links, candidate/budget snapshot and next work ID. Set ACCEPTED only when its full exit criterion passes.',
      'Append an event to WORK_BLOCK_LOG.jsonl with work ID, timestamp, old/new status, source, result, actual usage and evidence references. This log is append-only.',
      'Update the relevant existing records: docs/requirements.md, PROJECT_STATUS.md, EXPERIMENT_LOG.md, DECISIONS.md, AGENT_LOG.md and EXAM_NOTES.md. Keep entries concise and relevant; documentation is not substitute execution evidence.',
      'Run `python -B tools/project_workflow.py render` then `python -B tools/project_workflow.py check` with the project interpreter. The tool performs management validation only; it never contacts models, starts voice or dispatches actions.',
      'Report completed work by ID, concrete result, meaningful remaining gap and next ID. Ask only for a real prerequisite/security/scope boundary or required physical participation; standing routine project authorization remains in effect.'
    ])
    add('## Prerequisites to collect when the relevant block is reached');add()
    table(['Block','Needed input','Work that can still proceed'],[
      ['WB-05','Available devices, bridge/API and authorized access','Adapter/schema/confirmation tests using clearly labelled fixtures'],
      ['WB-07','Supported service/account and user-designated test recipient; calling route where supported','Draft/review/contact ambiguity and failure/cancel safeguards'],
      ['WB-06.4','User resumption of the Spotify deferral and actual supported service access','Complete the local media scope'],
      ['WB-08.3','Choose real local calendar facility or supported calendar account','Local reminder/persistence/document work'],
      ['WB-13.4','Genuine human reference, consented acoustic measurement and authorized evaluator','Software timing/TOR/missingness analysis and other completed engineering'],
      ['WB-03.5 / WB-15','Owner-approved normal operating profile and resource policy','Provider-free GUI/policy implementation and bounded qualification']])
    add('Do not request or store credentials in chat. Do not install or purchase hardware/services merely to satisfy an assumed platform choice. Verify actual compatibility and permissions within the relevant implementation block.');add()
    add('## Evidence index');add()
    table(['Reference','Record','Scope'],[[ref,'['+x['path']+']('+x['path']+')',x['scope']] for ref,x in b['evidence_index'].items()])
    add('The current benchmark is on hold. Historical results, failures and supplements remain distinct. Missing physical/reference/evaluator measurements stay NOT_MEASURED with their prerequisites; resolving that reporting disposition never waives a required functional capability.');add()
    return '\n'.join(out)+'\n'

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['validate','render','check']);p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);a=p.parse_args();root=a.root.resolve();b=load(root)
    summary=validate(b,root);expected=render(b);path=root/'PROJECT_WORK_PLAN.md'
    if a.command=='check':
        from project_acceptance import validate as acceptance_validate
        acceptance=acceptance_validate(root)
        assert acceptance['integrity_valid'],acceptance['errors']
        summary['acceptance_status']=acceptance['status']
    if a.command=='render' and (not path.exists() or path.read_text(encoding='utf-8')!=expected):
        if path.exists():
            stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');backup=root/'evidence/workflow/versions'/stamp;backup.mkdir(parents=True);(backup/'PROJECT_WORK_PLAN.md').write_bytes(path.read_bytes())
        path.write_text(expected,encoding='utf-8')
    if a.command=='check':assert path.read_text(encoding='utf-8')==expected,'Plan is stale; render it from the board.'
    print(json.dumps({'command':a.command,**summary,'provider_calls':0,'voice_started':False}))
if __name__=='__main__':main()
