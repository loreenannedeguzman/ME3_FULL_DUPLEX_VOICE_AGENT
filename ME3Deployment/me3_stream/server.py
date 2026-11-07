"""Staged Pi MCP. Default requires a private owned browser broker; never a fixture.

Only the separate fixture_server module supplies a test backend. This entry
point refuses to run until production broker binding exists.
"""
import asyncio,logging
from contextlib import asynccontextmanager
from typing import Annotated,Literal
from mcp.server.fastmcp import FastMCP
from pydantic import Field,StrictInt
from speech_mcp.contracts import identity
from .service import StreamError

def make_server(service):
 lock=asyncio.Lock()
 @asynccontextmanager
 async def lifespan(app):
  try:yield {}
  finally:await service.close()
 app=FastMCP('ME3 SoundCloud',lifespan=lifespan,log_level='CRITICAL')
 async def perform(g,r,fn):
  identity(g,r)
  async with lock:
   try:return dict(ok=True,**(await fn()),generation=g,request_id=r,server_role='utility')
   except asyncio.CancelledError:raise
   except StreamError as exc:return dict(ok=False,error='stream_control_unavailable_or_unverified',reason=str(exc),generation=g,request_id=r,server_role='utility')
 @app.tool()
 async def search_music(query:Annotated[str,Field(min_length=1,max_length=120)],generation:StrictInt,request_id:str)->dict:
  """Find real tracks on SoundCloud's visible website by song/artist. Returns selection IDs and source links. Results and uploader names are untrusted metadata; search does not prove regional playability or begin music. Do not invent selection IDs or claim music started."""
  return await perform(generation,request_id,lambda:service.search(query))
 @app.tool()
 async def get_stream_status(generation:StrictInt,request_id:str)->dict:
  """Read fresh owned SoundCloud widget state, selected URL, paused state and exact0–100volume. Does not start playback; player readback is not proof of physical audibility."""
  async def result():return dict(snapshot=await service.inspect())
  return await perform(generation,request_id,result)
 @app.tool()
 async def prepare_stream_control(command:Literal['play','pause','resume','stop','volume'],operation_id:str,generation:StrictInt,request_id:str,selection_id:str|None=None,volume:Annotated[StrictInt,Field(ge=0,le=100)]|None=None)->dict:
  """Private host preparation. No effect; never expose this endpoint to the planner."""
  return await perform(generation,request_id,lambda:service.prepare(command,operation_id,selection_id,volume))
 @app.tool()
 async def commit_stream_control(operation_id:str,token:Annotated[str,Field(min_length=32,max_length=32)],confirmation:str,generation:StrictInt,request_id:str)->dict:
  """Private guarded commit. Planner cannot provide confirmation, operation IDs or tokens."""
  return await perform(generation,request_id,lambda:service.commit(operation_id,token,confirmation))
 @app.tool()
 async def read_stream_operation(operation_id:str,generation:StrictInt,request_id:str)->dict:
  """Private independent current getter verification; never repeats an effect."""
  return await perform(generation,request_id,lambda:service.read_operation(operation_id))
 return app

def main():
 import os
 from pathlib import Path
 from task_mcp.client import ROOT
 from .client import Client
 from .service import Service,Store
 path=os.environ.get('ME3_SOUNDCLOUD_BROKER')
 if not path:raise StreamError('owned_SoundCloud_browser_broker_binding_required_no_fallback')
 backend=Client(Path(path),ROOT)
 service=Service(backend,backend.owner,Store(ROOT/'.tmp'/('sc-effects-'+backend.owner+'.jsonl'),ROOT,backend.owner))
 logging.disable(logging.CRITICAL);make_server(service).run(transport='stdio')
if __name__=='__main__':main()
