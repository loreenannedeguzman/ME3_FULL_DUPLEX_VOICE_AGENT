"""Run an accepted CLI build with logs redirected into this phase only."""
import importlib,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
name,*extra=sys.argv[1:];module=importlib.import_module(name)
for logger_name in ('StatusEventLog','EventLog','CoreEvents'):
    if hasattr(module,logger_name):
        original=getattr(module,logger_name)
        setattr(module,logger_name,lambda _path,cls=original:cls(ROOT/'evidence/core'/('entry-'+name.replace('.','-')+'-'+str(time.monotonic_ns()))))
sys.argv=[name,'--build-check',*extra];raise SystemExit(module.main())
