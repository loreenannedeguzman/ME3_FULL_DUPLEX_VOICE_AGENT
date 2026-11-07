"""Reusable stdlib build/dependency/HTTP functional checks in the existing jail.

No dependency installer or arbitrary command string. Runtime output/build files
exist only inside the namespace scratch directory, never the host workspace.
"""
import inspect

def main(root='/workspace',scratch='/tmp',argv=None,port=8765):
    import hashlib,http.client,json,os,py_compile,re,runpy,socket,subprocess,sys,time
    from pathlib import Path
    root=Path(root).resolve();scratch=Path(scratch).resolve();kind,path,*args=list(sys.argv[1:] if argv is None else argv)
    process=None;logs=None;report={};code=0
    def fail(reason):raise ValueError(reason)
    def relative(value):
        if not isinstance(value,str) or len(value)>240 or value.startswith('/') or '\\' in value or ':' in value or any(not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}',p) or '..' in p for p in value.split('/')):fail('relative_project_path_required')
        candidate=root/value
        if candidate.is_symlink() or any(p.is_symlink() for p in candidate.parents if p.is_relative_to(root)):fail('direct_project_path_required')
        resolved=candidate.resolve()
        if not resolved.is_relative_to(root):fail('relative_project_path_required')
        return resolved
    target=root if path=='' else relative(path)
    try:
        if kind=='python_compile':
            if args or not target.is_dir():fail('build_directory_without_arguments_required')
            if any(p.is_symlink() for p in target.rglob('*')):fail('direct_project_files_required')
            paths=sorted(target.rglob('*.py'))
            if not 1<=len(paths)<=128:fail('bounded_python_sources_required')
            output=scratch/'python-build';output.mkdir(mode=0o700)
            hashes={}
            for index,source in enumerate(paths):
                if source.is_symlink() or not source.resolve().is_relative_to(root) or source.stat().st_size>65536:fail('bounded_direct_python_source_required')
                compiled=output/(str(index)+'.pyc');py_compile.compile(str(source),cfile=str(compiled),doraise=True)
                hashes[source.relative_to(root).as_posix()]=hashlib.sha256(compiled.read_bytes()).hexdigest()
            report=dict(status='compiled',compiled_files=len(hashes),build_manifest_sha256=hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest(),build_outputs_persisted=False,build_kind='Python bytecode compilation only; no wheel/package build or behavior claim')
        elif kind=='python_dependencies':
            if args or not target.is_dir():fail('dependency_directory_without_arguments_required')
            declarations=[];sources=[];unsupported=False
            req=target/'requirements.txt'
            if req.is_file():
                if req.is_symlink() or not req.resolve().is_relative_to(root) or req.stat().st_size>8192:fail('dependency_manifest_bound')
                sources.append('requirements.txt')
                for line in req.read_text(encoding='utf-8').splitlines():
                    line=line.strip()
                    if not line or line.startswith('#'):continue
                    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}(?:(?:==|>=|<=|~=|>|<)[A-Za-z0-9_.+-]{1,40})?',line):unsupported=True
                    else:declarations.append(line)
            project=target/'pyproject.toml'
            if project.is_file():
                import tomllib
                if project.is_symlink() or not project.resolve().is_relative_to(root) or project.stat().st_size>8192:fail('dependency_manifest_bound')
                parsed=tomllib.loads(project.read_text(encoding='utf-8'));sources.append('pyproject.toml')
                for section,key in [('project','dependencies'),('build-system','requires')]:
                    rows=parsed.get(section,{}).get(key,[])
                    if not isinstance(rows,list) or len(rows)>32 or any(not isinstance(v,str) or len(v)>120 for v in rows):fail('bounded_dependency_list_required')
                    for row in rows:
                        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}(?:(?:==|>=|<=|~=|>|<)[A-Za-z0-9_.+-]{1,40})?',row):unsupported=True
                        else:declarations.append(row)
                if parsed.get('project',{}).get('dynamic'):unsupported=True
            if (target/'package.json').exists():sources.append('package.json');unsupported=True
            if len(declarations)>32:fail('dependency_manifest_bound')
            import importlib.metadata
            installed={re.sub('[-_.]+','-',d.metadata['Name']).lower():d.version for d in importlib.metadata.distributions(path=['/packages'])}
            missing=[];available=[]
            for requirement in declarations:
                match=re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9_.-]{0,79})(?:(==)([A-Za-z0-9_.+-]{1,40}))?',requirement)
                if match is None:unsupported=True;missing.append(requirement);continue
                name,operator,version=match.groups();actual=installed.get(re.sub('[-_.]+','-',name).lower())
                if actual is not None and (operator is None or version==actual):available.append(name+'=='+actual)
                else:missing.append(requirement)
            code=2 if missing or unsupported else 0
            report=dict(status='external_dependencies_not_installed' if code else 'declared_dependencies_available' if declarations else 'no_external_dependencies_declared',declared=declarations[:5],declared_total=len(declarations),available=available[:5],missing=missing[:5],unsupported_declaration=unsupported,manifest_files=sources,network_install_performed=False,dependency_install_available=True,scope='Only administrator-approved, hash-verified offline Python packages in the read-only /packages mount are inspected. No install is performed by this command; dependency metadata is not proof of application behavior.')
        elif kind=='python_http_check':
            if not target.is_file() or target.suffix!='.py' or not args:fail('service_entrypoint_and_check_manifest_required')
            check_path=relative(args[0])
            if check_path.suffix!='.json' or check_path.stat().st_size>8192:fail('bounded_service_check_manifest_required')
            checks=json.loads(check_path.read_bytes())
            if not isinstance(checks,list) or not 1<=len(checks)<=8:fail('bounded_service_checks_required')
            for check in checks:
                if not isinstance(check,dict) or set(check)-{'method','path','status','contains','body'} or not {'path','status','contains'}<=set(check):fail('exact_service_check_fields_required')
                request_path=check['path'];body=check.get('body','')
                if check.get('method','GET') not in {'GET','POST'} or type(check['status']) is not int or not 100<=check['status']<=599 or not isinstance(request_path,str) or not request_path.startswith('/') or request_path.startswith('//') or len(request_path)>240 or any(ord(c)<33 or ord(c)>126 for c in request_path) or not isinstance(check['contains'],str) or len(check['contains'])>1000 or not isinstance(body,str) or len(body.encode())>1024:fail('bounded_local_HTTP_check_required')
            if type(port) is not int or not 1024<=port<=65535:fail('private_service_port_required')
            child='import runpy,sys;sys.path[:0]=['+repr(str(root))+',"/packages"];sys.argv=sys.argv[1:];runpy.run_path(sys.argv[0],run_name="__main__")'
            env={'PATH':os.defpath,'HOME':str(scratch),'LANG':'C.UTF-8','ME3_SERVICE_PORT':str(port)}
            if os.name=='nt':env['SYSTEMROOT']=os.environ.get('SYSTEMROOT','C:/Windows')
            logs=(scratch/'service-output.txt').open('w+b')
            process=subprocess.Popen([sys.executable,'-I','-B','-c',child,str(target),*args[1:]],cwd=root,env=env,stdin=subprocess.DEVNULL,stdout=logs,stderr=logs)
            ready=False
            for _ in range(40):
                if process.poll() is not None:break
                try:
                    with socket.create_connection(('127.0.0.1',port),timeout=.1):ready=True;break
                except OSError:time.sleep(.05)
            if not ready:fail('owned_service_did_not_listen')
            observations=[]
            for index,check in enumerate(checks):
                conn=http.client.HTTPConnection('127.0.0.1',port,timeout=1)
                try:
                    conn.request(check.get('method','GET'),check['path'],body=check.get('body','').encode(),headers={'Content-Type':'application/json','Connection':'close'})
                    response=conn.getresponse();raw=response.read(4097)
                    if len(raw)>4096:fail('service_response_bound')
                    match=response.status==check['status'] and check['contains'] in raw.decode('utf-8',errors='replace')
                    observations.append(dict(check=index,status=response.status,matched=match,response_sha256=hashlib.sha256(raw).hexdigest()))
                    if not match:code=1;break
                finally:conn.close()
            report=dict(status='http_checks_passed' if not code else 'http_check_failed',checks_run=len(observations),checks_requested=len(checks),observations=observations,service_is_namespace_local=True,host_network_access=False)
        else:fail('unapproved_project_command')
    except Exception as exc:
        code=1;report=dict(status='failed',error='project_input_or_execution_failed',error_type=type(exc).__name__)
        if isinstance(exc,py_compile.PyCompileError):report.update(error='python_compile_failed',diagnostic=str(exc)[:200])
        if isinstance(exc,ValueError) and re.fullmatch('[a-z_]{1,80}',str(exc)):report['error']=str(exc)
    finally:
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=.5)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=1)
            report['owned_service_exit_code']=process.returncode
            report['owned_service_main_closed']=process.poll() is not None
        if logs is not None:logs.close()
    print(json.dumps(report,ensure_ascii=False,separators=(',',':')))
    return code

# Inject only this stdlib function into the existing read-only Bubblewrap
# command, without mounting/importing the host project or its credentials.
BOOTSTRAP=inspect.getsource(main)+'\nraise SystemExit(main())\n'
