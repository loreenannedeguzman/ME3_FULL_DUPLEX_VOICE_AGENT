"""Administrator-owned, project-bounded MCP configuration; never model supplied."""
import hashlib,json,re
from pathlib import Path
from task_mcp.client import ROOT

IDENT=re.compile(r'[a-z][a-z0-9_]{0,23}')
MODULE=re.compile(r'[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*')
RESERVED={'cancel_pending_action','check_last_action','run_goal_steps','load_operation_schemas'}
WORKSPACE_GRANTED={'write_workspace_file','run_workspace_command','list_workspace_runs','read_workspace_run','manage_workspace_runtime'}
DEFAULT=ROOT/'config/assistant.json'

def bounded_int(value,lo,hi):
    if type(value) is not int or not lo<=value<=hi:raise ValueError('invalid_assistant_bound')
    return value

def load(path=DEFAULT,root=ROOT):
    root=Path(root).resolve();path=Path(path).resolve()
    if not path.is_relative_to(root):raise ValueError('assistant_config_outside_project')
    raw=path.read_bytes()
    if len(raw)>65536:raise ValueError('assistant_config_too_large')
    cfg=json.loads(raw)
    if set(cfg)!={'version','runtime','servers','actions'} or cfg['version']!=1:raise ValueError('invalid_assistant_config')
    runtime=cfg['runtime'];bounds={'max_session_seconds':(10,3600),'max_turns':(1,128),'tool_rounds':(1,32),'batch_calls':(1,3),'rpc_calls':(1,128)}
    if set(runtime)!=set(bounds):raise ValueError('invalid_runtime_configuration')
    for key,(lo,hi) in bounds.items():bounded_int(runtime[key],lo,hi)
    if not isinstance(cfg['servers'],list) or not 1<=len(cfg['servers'])<=16:raise ValueError('invalid_server_count')
    names=set();aliases=set();approved=[]
    for spec in cfg['servers']:
        if set(spec)-{'workspace_tools','rpc_timeout_seconds'}!={'id','module','args','identity','role','read_tools'}:raise ValueError('invalid_server_configuration')
        bounded_int(spec.get('rpc_timeout_seconds',5),5,30)
        name=spec['id'];module=spec['module']
        if not isinstance(name,str) or not IDENT.fullmatch(name) or name in names:raise ValueError('invalid_server_id')
        names.add(name)
        if not isinstance(module,str) or not MODULE.fullmatch(module):raise ValueError('project_module_required')
        source=root.joinpath(*module.split('.')).with_suffix('.py').resolve()
        if not source.is_relative_to(root) or not source.is_file():raise ValueError('approved_project_module_missing')
        if not isinstance(spec['args'],list) or len(spec['args'])>8 or any(not isinstance(x,str) or len(x)>256 or '\x00' in x for x in spec['args']):raise ValueError('invalid_server_arguments')
        if spec['identity'] not in {'me3','plain'} or spec['role'] not in {'task','utility'}:raise ValueError('invalid_server_identity')
        if not isinstance(spec['read_tools'],list) or len(set(spec['read_tools']))!=len(spec['read_tools']):raise ValueError('invalid_read_tools')
        workspace_tools=spec.get('workspace_tools',[])
        if not isinstance(workspace_tools,list) or len(set(workspace_tools))!=len(workspace_tools):raise ValueError('invalid_workspace_tools')
        if workspace_tools and (module!='me3_assistant.workspace_server' or spec['identity']!='me3' or spec['role']!='utility' or not set(workspace_tools)<=WORKSPACE_GRANTED):raise ValueError('invalid_workspace_tools')
        if set(workspace_tools)&set(spec['read_tools']) or set(spec['read_tools'])&WORKSPACE_GRANTED:raise ValueError('workspace_operations_are_not_read_tools')
        for tool in [*spec['read_tools'],*workspace_tools]:
            if not isinstance(tool,str) or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,38}',tool):raise ValueError('invalid_tool_name')
            alias=name+'__'+tool
            if alias in RESERVED or alias in aliases or len(alias)>64:raise ValueError('duplicate_tool_alias')
            aliases.add(alias)
        approved.append({'module':module,'sha256':hashlib.sha256(source.read_bytes()).hexdigest()})
    if not isinstance(cfg['actions'],list) or len(cfg['actions'])>16:raise ValueError('invalid_actions')
    from me3_assistant.actions import ADAPTERS
    for action in cfg['actions']:
        if set(action)!={'name','server','adapter'} or action['server'] not in names or action['adapter'] not in ADAPTERS:raise ValueError('unsupported_action_adapter')
        if not isinstance(action['name'],str) or not IDENT.fullmatch(action['name']) or action['name'] in aliases|RESERVED:raise ValueError('invalid_action_alias')
        aliases.add(action['name'])
    return {**cfg,'path':str(path),'sha256':hashlib.sha256(raw).hexdigest(),'approved_modules':approved}
