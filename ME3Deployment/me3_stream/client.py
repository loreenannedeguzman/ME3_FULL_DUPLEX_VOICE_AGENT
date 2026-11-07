"""Private capability client for the session-owned browser, never model supplied."""
import hashlib,json,os,re,stat
from pathlib import Path
import aiohttp
from .service import StreamError
class Client:
 def __init__(self,path,root):
  self.path=Path(path);root=Path(root).resolve()
  if not self.path.resolve().is_relative_to(root/'.tmp') or self.path.is_symlink() or not self.path.is_file() or self.path.stat().st_size>2048:raise StreamError('owned_stream_broker_required')
  info=self.path.stat()
  if os.name!='nt' and (info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)&0o077):raise StreamError('stream_broker_permissions')
  raw=self.path.read_bytes();self.binding=hashlib.sha256(raw).hexdigest();config=json.loads(raw)
  if set(config)!={'version','owner','token','socket'} or config['version']!=1 or not re.fullmatch('[0-9a-f]{32}',config['owner']) or not re.fullmatch('[0-9a-f]{64}',config['token']):raise StreamError('stream_broker_config_invalid')
  sock=Path(config['socket'])
  if not sock.resolve().is_relative_to(root/'.tmp') or sock.is_symlink() or not sock.is_socket():raise StreamError('owned_stream_socket_required')
  self.owner=config['owner'];self.token=config['token'];self.socket=str(sock);self.touched=False
 async def call(self,action,**payload):
  if self.path.is_symlink() or hashlib.sha256(self.path.read_bytes()).hexdigest()!=self.binding:raise StreamError('stream_broker_binding_changed')
  async with aiohttp.ClientSession(connector=aiohttp.UnixConnector(path=self.socket),timeout=aiohttp.ClientTimeout(total=28),trust_env=False) as session:
   async with session.post('http://localhost/control',headers={'Authorization':'Bearer '+self.token},json=dict(action=action,owner=self.owner,payload=payload),allow_redirects=False) as response:
    raw=await response.content.read(20001)
    if response.status!=200 or len(raw)>20000:raise StreamError('stream_broker_request_failed')
    result=json.loads(raw)
    if not isinstance(result,dict) or result.get('ok') is not True:raise StreamError('stream_browser_operation_failed')
    return result['result']
 async def search(self,query):return await self.call('search',query=query)
 async def inspect(self):return await self.call('inspect')
 async def execute(self,intent,operation_id):self.touched=True;return await self.call('execute',intent=intent,operation_id=operation_id)
 async def close(self):
  if self.touched:await self.call('close_owner');self.touched=False
