"""Synthetic SDK cancellation fixture: never a speech provider or live evidence."""
import argparse,asyncio,logging,os
from mcp.server.fastmcp import FastMCP
p=argparse.ArgumentParser();p.add_argument('role');args=p.parse_args()
role=args.role;server=FastMCP('synthetic-'+role,log_level='CRITICAL');cancelled=0
@server.tool()
async def server_info()->dict:
    return {'ok':True,'server_pid':os.getpid(),'server_role':role,'model':'synthetic-only','host':'test','machine':'test','cancelled_calls':cancelled}
@server.tool()
async def transcribe(pcm_b64:str,generation:int,request_id:str,sample_rate:int=16000)->dict:
    global cancelled
    try:await asyncio.sleep(2)
    except asyncio.CancelledError:
        cancelled+=1;raise
    return {'ok':True,'server_pid':os.getpid(),'server_role':role,'generation':generation,'request_id':request_id,'text':'synthetic only'}
@server.tool()
async def synthesize(text:str,generation:int,request_id:str,style:str='normal')->dict:
    return {'ok':False,'error':'fixture_not_speech'}
logging.disable(logging.CRITICAL)
server.run(transport='stdio')
