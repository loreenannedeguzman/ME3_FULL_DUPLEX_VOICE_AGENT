"""Authenticated owner-click reads of the fixed artifact workspace.

No model, execution, service lifecycle, account, audio or budget reservation.
Generated HTML has a separate response policy and an opaque iframe origin.
"""
import json
from urllib.parse import parse_qs, urlsplit

from me3_assistant.artifact_delivery import ArtifactDelivery
from me3_assistant.workspace import Workspace, WorkspaceError

ROUTES={'/api/artifacts','/api/artifacts/download','/api/artifacts/project.zip','/api/artifacts/preview','/api/artifacts/confirmed-preview'}
PARENT_CSP="default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'self'"

def authorized_delivery(console):
    from task_mcp.client import ROOT
    from me3_assistant.guard import verify_sources, authorize
    from onit_candidate.identity import verify_candidate
    source=verify_sources(ROOT)
    if source['revision']!=console.source['revision'] or verify_candidate(ROOT)!=console._candidate_hashes:
        raise PermissionError('artifact_source_changed')
    grant,_=authorize(console.config,source,mode='normal')
    if grant.get('workspace_access') is not True:raise PermissionError('artifact_workspace_not_authorized')
    return ArtifactDelivery(Workspace(ROOT/'data/agent-workspace'))

def raw_reply(handler,status,body,content_type,*,csp=PARENT_CSP,filename=None):
    if isinstance(body,str):body=body.encode('utf-8')
    if filename and any(c in filename for c in '\r\n"\\'):raise ValueError('invalid_artifact_filename')
    handler.send_response(status)
    for name,value in {'Content-Type':content_type,'Content-Length':str(len(body)),
        'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
        'Referrer-Policy':'no-referrer','Content-Security-Policy':csp,
        'Cross-Origin-Resource-Policy':'same-origin',
        'Permissions-Policy':'camera=(), microphone=(), geolocation=(), autoplay=(), payment=(), usb=()'}.items():
        handler.send_header(name,value)
    if filename:
        # Backend paths are already strict; never permit response splitting.
        handler.send_header('Content-Disposition','attachment; filename="'+filename+'"')
    handler.end_headers();handler.wfile.write(body)

def handle_get(handler,delivery_factory=authorized_delivery):
    if handle_runtime_get(handler):return True
    parsed=urlsplit(handler.path)
    if parsed.path not in ROUTES:return False
    allowed={f'http://127.0.0.1:{handler.server.server_port}',f'http://localhost:{handler.server.server_port}'}
    if (not handler.host_ok() or not handler.session_ok()
        or handler.headers.get('Sec-Fetch-Site') not in {None,'same-origin','none'}
        or handler.headers.get('Origin') not in {None,*allowed}):
        handler.reply(403,{'error':'artifact_session_required'});return True
    try:
        params=parse_qs(parsed.query,keep_blank_values=True,strict_parsing=True,max_num_fields=4)
        expected={'/api/artifacts':set(),'/api/artifacts/download':{'path','sha256'},
            '/api/artifacts/project.zip':{'workspace_sha256'},'/api/artifacts/preview':{'path','workspace_sha256'},'/api/artifacts/confirmed-preview':{'open_id'}}[parsed.path]
        actual=set(params)
        if (actual!=expected and not(parsed.path=='/api/artifacts/project.zip' and actual==expected|{'directory'})) or any(len(v)!=1 for v in params.values()):raise ValueError('invalid_artifact_request')
        params={k:v[0] for k,v in params.items()}
        delivery=delivery_factory(handler.server.console)
        if parsed.path=='/api/artifacts':handler.reply(200,delivery.listing())
        elif parsed.path in {'/api/artifacts/preview','/api/artifacts/confirmed-preview'}:
            if parsed.path=='/api/artifacts/confirmed-preview':
                console=handler.server.console
                result=console.shared.artifact_preview.consume(params['open_id'],delivery,console.source['revision'])
            else:result=delivery.preview(params['path'],params['workspace_sha256'])
            raw_reply(handler,200,result['html'],'text/html; charset=utf-8',
                csp=result['csp']+"; sandbox allow-scripts; frame-ancestors 'self'")
        else:
            result=(delivery.download(params['path'],params['sha256']) if parsed.path.endswith('/download')
                else delivery.project_zip(params['workspace_sha256'],params.get('directory','')))
            raw_reply(handler,200,result['body'],result['content_type'],filename=result['filename'])
    except PermissionError:handler.reply(403,{'error':'artifact_workspace_not_authorized'})
    except (WorkspaceError,ValueError,UnicodeError):handler.reply(409,{'error':'artifact_changed_or_unavailable_refresh_list'})
    except Exception:handler.reply(503,{'error':'artifact_read_unavailable'})
    return True

def runtime_delivery(console):
    from task_mcp.client import ROOT
    from me3_assistant.workspace_runtime import Runtime
    delivery=authorized_delivery(console)
    return Runtime(delivery.workspace,ROOT/'data/agent-runtime',ROOT/'data/approved-wheelhouse'),console.source['revision']

def handle_runtime_get(handler,runtime_factory=runtime_delivery):
    parsed=urlsplit(handler.path)
    if parsed.path!='/api/artifacts/runtime-document':return False
    allowed={f'http://127.0.0.1:{handler.server.server_port}',f'http://localhost:{handler.server.server_port}'}
    if (not handler.host_ok() or not handler.session_ok() or handler.headers.get('Sec-Fetch-Site') not in {None,'same-origin','none'} or handler.headers.get('Origin') not in {None,*allowed}):
        handler.reply(403,{'error':'runtime_view_session_required'});return True
    try:
        params=parse_qs(parsed.query,keep_blank_values=True,strict_parsing=True,max_num_fields=1)
        if set(params)!={'view_id'} or len(params['view_id'])!=1:raise ValueError('runtime_view_document_fields')
        console=handler.server.console;runtime,source=runtime_factory(console)
        html=handler.server.dispatch(console.shared.runtime_preview.document(params['view_id'][0],runtime,source))
        from me3_assistant.artifact_delivery import PREVIEW_CSP
        raw_reply(handler,200,html,'text/html; charset=utf-8',csp=PREVIEW_CSP+"; sandbox allow-scripts; frame-ancestors 'self'")
    except PermissionError:handler.reply(403,{'error':'runtime_view_not_authorized'})
    except Exception:handler.reply(409,{'error':'runtime_view_document_unavailable_no_retry'})
    return True

def handle_runtime_post(handler,runtime_factory=None):
    if handler.path!='/api/artifacts/runtime-view':return False
    import base64,secrets
    allowed={f'http://127.0.0.1:{handler.server.server_port}',f'http://localhost:{handler.server.server_port}'}
    if (not handler.host_ok() or not handler.session_ok() or handler.headers.get('Origin') not in allowed
        or handler.headers.get('Sec-Fetch-Site') not in {None,'same-origin'}
        or not secrets.compare_digest(handler.headers.get('X-ME3-CSRF',''),handler.server.token)):
        handler.reply(403,{'error':'runtime_view_session_required'});return True
    try:
        size=int(handler.headers.get('Content-Length','0'))
        if not 0<size<=25000 or handler.headers.get('Content-Type')!='application/json':raise ValueError('runtime_view_request_bound')
        data=json.loads(handler.rfile.read(size));console=handler.server.console
        if not isinstance(data,dict):raise ValueError('runtime_view_request_required')
        operation=data.get('operation');expected={'open':{'operation','open_id'},'relay':{'operation','view_id','request_id','request'},'status':{'operation','view_id'}}
        if operation not in expected or set(data)!=expected[operation]:raise ValueError('runtime_view_exact_operation_required')
        if runtime_factory is None:
            runtime,source=runtime_delivery(console)
        else:runtime,source=runtime_factory(console)
        owner=console.shared.runtime_preview
        coroutine=owner.open(data['open_id'],runtime,source) if operation=='open' else owner.relay(data['view_id'],data['request_id'],data['request'],runtime,source) if operation=='relay' else owner.poll(data['view_id'],runtime,source)
        handler.reply(200,handler.server.dispatch(coroutine))
    except PermissionError:handler.reply(403,{'error':'runtime_view_not_authorized'})
    except Exception:handler.reply(409,{'error':'runtime_view_unavailable_no_retry'})
    return True
