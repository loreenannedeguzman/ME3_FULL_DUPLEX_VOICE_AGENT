"""Explicit isolated MCP fixture entry; never selected by the production server."""
import argparse,copy,json,logging,uuid
from pathlib import Path
from .server import make_server
from .service import Service,Store
class Backend:
 def __init__(self,owner):
  self.s=dict(owner=owner,sequence=0,ready=True,track_url=None,paused=True,position_ms=0,duration_ms=0,volume=40,visible=True,error=None,last_operation_id=None,voice_active=False,reference_ready=False);self.calls=[];self.failure=None
 async def search(self,query):
  self.calls.append(('search',query));return [dict(title='Flickermood',uploader='Forss',url='https://soundcloud.com/forss/flickermood')]
 async def inspect(self):self.s['sequence']+=1;return copy.deepcopy(self.s)
 async def execute(self,c,operation_id):
  self.calls.append(('execute',copy.deepcopy(c),operation_id))
  if self.failure:raise self.failure
  self.s['last_operation_id']=operation_id
  if c['command']=='play':self.s.update(track_url=c['track_url'],paused=False,position_ms=200,duration_ms=213890,volume=c['volume'])
  if c['command']=='resume':self.s['paused']=False
  if c['command'] in {'pause','stop'}:self.s['paused']=True
  if c['command']=='stop':self.s['position_ms']=0
  if c['command']=='volume':self.s['volume']=c['volume']
  return await self.inspect()
 async def close(self):self.s['paused']=True
def main():
 from task_mcp.client import ROOT
 parser=argparse.ArgumentParser();parser.add_argument('--fixture-root',type=Path,required=True);args=parser.parse_args();root=args.fixture_root.resolve()
 if not root.is_relative_to((ROOT/'.tmp').resolve()):raise ValueError('fixture_root_required')
 marker=json.loads((root/'fixture-authorized.json').read_bytes())
 if marker!=dict(fixture_only=True,actual_external_effects=False):raise ValueError('explicit_fixture_marker_required')
 owner=uuid.uuid4().hex;backend=Backend(owner);service=Service(backend,owner,Store(root/'service.jsonl',root,owner))
 logging.disable(logging.CRITICAL);make_server(service).run(transport='stdio')
if __name__=='__main__':main()
