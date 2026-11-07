"""Synthetic recognition and quiet tones only; never a real ASR/TTS provider."""
import argparse,base64,logging,math,os,platform,struct
from mcp.server.fastmcp import FastMCP
p=argparse.ArgumentParser();p.add_argument('role',choices=['asr','tts']);role=p.parse_args().role
server=FastMCP('synthetic-tones-'+role,log_level='CRITICAL')
def info():return {'server_pid':os.getpid(),'server_role':role,'model':'synthetic-tone-fixture','host':platform.system(),'machine':platform.machine()}
@server.tool()
async def server_info()->dict:return {'ok':True,**info()}
@server.tool()
async def transcribe(pcm_b64:str,generation:int,request_id:str,sample_rate:int=16000)->dict:
    return {'ok':True,**info(),'generation':generation,'request_id':request_id,'text':'Synthetic diagnostic request.'}
@server.tool()
async def synthesize(text:str,generation:int,request_id:str,style:str='normal')->dict:
    seconds=3 if generation in {1,3} else .3
    pcm=b''.join(struct.pack('<h',int(400*math.sin(2*math.pi*(440+generation*30)*n/24000))) for n in range(int(24000*seconds)))
    return {'ok':True,**info(),'generation':generation,'request_id':request_id,'pcm_b64':base64.b64encode(pcm).decode(),'sample_rate':24000,'channels':1,'encoding':'pcm_s16le','byte_count':len(pcm),'provider_api':'synthetic-fixture'}
logging.disable(logging.CRITICAL);server.run(transport='stdio')
