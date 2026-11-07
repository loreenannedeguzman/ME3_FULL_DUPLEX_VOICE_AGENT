"""Read-only recovery of bounded artifact execution receipts, never re-execution."""
import hashlib,json,math,os,re,stat
from pathlib import Path
from me3_assistant.workspace import Workspace,WorkspaceError,digest,relative

STATES={'starting','completed','failed','timed_out','output_limit','canceled'}
FIELDS={'ok','run_id','command_id','path','status','exit_code','stdout','stderr','output_truncated',
        'elapsed_seconds','input_manifest_sha256','isolation','host_workspace_modified_by_command','content_is_untrusted_data'}

def manifest(files):
    return digest(json.dumps({n:digest(raw) for n,raw in sorted(files.items())},sort_keys=True).encode())

def run_id(value):
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{32}',value):raise WorkspaceError('invalid_execution_run_id')
    return value

def project_test_summary(text):
    """Validate the new runner's terminal report; exit0 alone proves no tests."""
    def unique(pairs):
        value={}
        for key,item in pairs:
            if key in value:raise ValueError('duplicate summary field')
            value[key]=item
        return value
    try:
        if not isinstance(text,str) or len(text.encode())>1401:raise ValueError('summary bound')
        value=json.loads(text,object_pairs_hook=unique,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('nonfinite')))
        counts={'discovered_tests','tests_run','successes','failure_events','error_events','skipped','expected_failures','unexpected_successes','captured_output_bytes'}
        if not isinstance(value,dict) or set(value)!=counts|{'framework','complete','success','diagnostics','diagnostics_truncated'} or value['framework']!='unittest':raise ValueError('summary fields')
        if any(type(value[k]) is not int or not 0<=value[k]<=10**15 for k in counts):raise ValueError('summary counts')
        if any(type(value[k]) is not bool for k in ('complete','success','diagnostics_truncated')):raise ValueError('summary flags')
        if value['complete'] and value['tests_run']!=value['discovered_tests']:raise ValueError('incomplete suite')
        if any(value[k]>value['tests_run'] for k in ('successes','expected_failures','unexpected_successes')):raise ValueError('case count')
        success=bool(value['tests_run']) and value['complete'] and not any(value[k] for k in ('failure_events','error_events','skipped','unexpected_successes')) and value['successes']+value['expected_failures']==value['tests_run']
        if value['success'] is not success:raise ValueError('contradictory summary')
        diagnostics=value['diagnostics']
        if not isinstance(diagnostics,list) or len(diagnostics)>2:raise ValueError('diagnostic count')
        total=value['failure_events']+value['error_events']+value['unexpected_successes']
        if len(diagnostics)>total or total>len(diagnostics) and not value['diagnostics_truncated']:raise ValueError('diagnostic truncation')
        for row in diagnostics:
            if not isinstance(row,dict) or set(row)!={'test','kind','exception','message'}:raise ValueError('diagnostic fields')
            if any(not isinstance(row[k],str) or len(row[k])>n for k,n in [('test',100),('exception',64),('message',200)]):raise ValueError('diagnostic text')
            field={'error':'error_events','failure':'failure_events','unexpected_success':'unexpected_successes'}.get(row['kind'])
            if field is None or not value[field]:raise ValueError('diagnostic kind')
        return value
    except (ValueError,TypeError,KeyError,UnicodeError):raise WorkspaceError('invalid_project_test_summary') from None

def decode(raw,identifier):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('duplicate field')
            result[key]=value
        return result
    try:
        if len(raw)>8192:raise ValueError('oversized')
        value=json.loads(raw,object_pairs_hook=unique)
        if not isinstance(value,dict) or set(value)-{'environment_sha256'}!=FIELDS:raise ValueError('fields')
        if 'environment_sha256' in value and (not isinstance(value['environment_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',value['environment_sha256'])):raise ValueError('environment')
        if value['ok'] is not True or value['run_id']!=identifier or value['status'] not in STATES:raise ValueError('identity/state')
        if value['command_id'] not in {'python','python_tests','python_project_tests','python_compile','python_dependencies','python_http_check'}:raise ValueError('command')
        relative(value['path'],directory=value['command_id'] in {'python_tests','python_project_tests','python_compile','python_dependencies'})
        if value['command_id'] in {'python','python_http_check'} and not value['path'].endswith('.py'):raise ValueError('target')
        if value['exit_code'] is not None and type(value['exit_code']) is not int:raise ValueError('exit')
        if value['status']=='completed' and (value['exit_code']!=0 or value['output_truncated'] is not False):raise ValueError('contradictory completion')
        if value['status']=='failed' and (type(value['exit_code']) is not int or value['exit_code']==0):raise ValueError('contradictory failure')
        if value['status'] in {'canceled','timed_out','output_limit'} and type(value['exit_code']) is not int:raise ValueError('cleanup unverified')
        if value['status']=='starting' and value['exit_code'] is not None:raise ValueError('contradictory start')
        if any(not isinstance(value[k],str) for k in ['stdout','stderr']):raise ValueError('output')
        if len(value['stdout'])+len(value['stderr'])>1500:raise ValueError('output bound')
        if type(value['output_truncated']) is not bool:raise ValueError('truncation')
        if value['command_id']=='python_project_tests' and value['status'] in {'completed','failed'}:
            summary=project_test_summary(value['stdout'])
            if (value['status']=='completed') is not summary['success'] or value['exit_code']!=(0 if summary['success'] else 1):raise ValueError('project test outcome')
        elapsed=value['elapsed_seconds']
        if type(elapsed) not in {int,float} or not math.isfinite(elapsed) or elapsed<0:raise ValueError('elapsed')
        if not isinstance(value['input_manifest_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',value['input_manifest_sha256']):raise ValueError('manifest')
        if value['isolation']!='bubblewrap_no_network_readonly_snapshot' or value['host_workspace_modified_by_command'] is not False or value['content_is_untrusted_data'] is not True:raise ValueError('scope')
        return value
    except (ValueError,TypeError,KeyError,UnicodeError,WorkspaceError):raise WorkspaceError('invalid_execution_receipt') from None

def validate_execution_result(value,arguments,expected_manifest=None,expected_environment_sha256=None):
    """Validate a fresh RPC receipt before Onit sees any success/status claim."""
    if not isinstance(value,dict) or not isinstance(arguments,dict):raise WorkspaceError('invalid_execution_receipt')
    # Host-added identity has already been checked by Registry; every other
    # returned field must match the exact pinned receipt schema.
    raw={k:v for k,v in value.items() if k not in {'generation','request_id','server_role','server_pid'}}
    receipt=decode(json.dumps(raw,allow_nan=False).encode(),run_id(raw.get('run_id')))
    if receipt['command_id']!=arguments.get('command_id') or receipt['path']!=arguments.get('path'):raise WorkspaceError('execution_receipt_request_mismatch')
    if expected_manifest is not None and receipt['input_manifest_sha256']!=expected_manifest:raise WorkspaceError('execution_receipt_input_mismatch')
    if receipt.get('environment_sha256')!=expected_environment_sha256:raise WorkspaceError('execution_receipt_environment_mismatch')
    return receipt

class History:
    def __init__(self,workspace,runs,runtime_root=None):self.workspace=workspace;self.runs=Workspace(runs);self.runtime_root=Path(runtime_root) if runtime_root is not None else workspace.root.parent/'agent-runtime'
    def identifiers(self):
        if not self.runs.supported():raise WorkspaceError('workspace_requires_posix_directory_handles')
        if not self.runs.root.exists() and not self.runs.root.is_symlink():return []
        with self.runs.folder() as fd:
            names=os.listdir(fd)
            if len(names)>32:raise WorkspaceError('execution_archive_limit')
            for name in names:
                run_id(name)
                if not stat.S_ISDIR(os.stat(name,dir_fd=fd,follow_symlinks=False).st_mode):raise WorkspaceError('invalid_execution_run_directory')
        return sorted(names)
    def read(self,identifier):
        identifier=run_id(identifier)
        if identifier not in self.identifiers():raise WorkspaceError('execution_run_not_found')
        with self.runs.folder([identifier]) as fd:
            try:os.stat('receipt.json',dir_fd=fd,follow_symlinks=False)
            except FileNotFoundError:
                return {'ok':True,'run_id':identifier,'status':'not_finalized','receipt_available':False,
                        'execution_repeated':False,'outcome_verified':False,'content_is_untrusted_data':True}
            raw=self.runs._read(fd,'receipt.json')
        value=decode(raw,identifier)
        snapshot=Workspace(self.runs.root/identifier/'input')
        if manifest(snapshot.files())!=value['input_manifest_sha256']:raise WorkspaceError('execution_snapshot_changed')
        current=manifest(self.workspace.files())
        from me3_assistant.workspace_runtime import active_environment_sha256
        environment_matches=value.get('environment_sha256')==active_environment_sha256(self.runtime_root)
        return {'ok':True,'run_id':identifier,'receipt_available':True,'receipt':value,'receipt_sha256':digest(raw),
                'current_workspace_matches':current==value['input_manifest_sha256'] and environment_matches,'current_environment_matches':environment_matches,'execution_repeated':False,
                'scope':'Historical isolated execution only. Completed files/actions were not undone. A changed workspace needs fresh tests.',
                'content_is_untrusted_data':True}
    def listing(self,offset=0):
        if type(offset) is not int or not 0<=offset<=32:raise WorkspaceError('invalid_execution_offset')
        names=self.identifiers();rows=[]
        for name in names[offset:offset+8]:
            value=self.read(name)
            if not value['receipt_available']:rows.append({'run_id':name,'status':'not_finalized','receipt_available':False})
            else:
                receipt=value['receipt'];rows.append({k:receipt[k] for k in ['run_id','command_id','path','status','exit_code','input_manifest_sha256']})
                rows[-1].update(receipt_available=True,receipt_sha256=value['receipt_sha256'],current_workspace_matches=value['current_workspace_matches'])
        return {'ok':True,'runs':rows,'total_runs':len(names),'next_offset':offset+8 if offset+8<len(names) else None,
                'ordering':'run_id, not chronological','execution_repeated':False,'content_is_untrusted_data':True}
