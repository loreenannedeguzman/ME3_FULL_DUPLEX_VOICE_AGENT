"""Synthetic429 server; no Google import/call and no speech generation."""
import argparse,logging,os
from mcp.server.fastmcp import FastMCP
p=argparse.ArgumentParser();p.add_argument('role');role=p.parse_args().role
server=FastMCP('synthetic-quota-'+role,log_level='CRITICAL')
@server.tool()
async def server_info()->dict:
    return {'ok':True,'server_pid':os.getpid(),'server_role':role,'model':'synthetic-quota-only','host':'test','machine':'test'}
@server.tool()
async def transcribe(pcm_b64:str,generation:int,request_id:str,sample_rate:int=16000)->dict:
    return {'ok':False,'error':'unexpected_fixture_asr_call'}
@server.tool()
async def synthesize(text:str,generation:int,request_id:str,style:str='normal')->dict:
    return {'ok':False,'error':'provider_error','error_type':'SyntheticQuota','code':429}
logging.disable(logging.CRITICAL);server.run(transport='stdio')
