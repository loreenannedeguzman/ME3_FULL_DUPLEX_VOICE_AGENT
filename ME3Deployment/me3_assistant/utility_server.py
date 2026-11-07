"""Project-owned pure utilities; no shopping-store creation or model credentials."""
import logging,os
from mcp.server.fastmcp import FastMCP
from pydantic import StrictInt
from me3_agent.tools import calculate,current_time
from speech_mcp.contracts import identity

def server():
    app=FastMCP('me3-assistant-utilities',log_level='CRITICAL')
    def perform(g,r,operation):
        identity(g,r)
        try:return {'ok':True,**operation(),'generation':g,'request_id':r,'server_role':'utility','server_pid':os.getpid()}
        except Exception:return {'ok':False,'error':'invalid_utility_input','generation':g,'request_id':r,'server_role':'utility'}
    @app.tool()
    async def calculate_expression(expression:str,generation:StrictInt,request_id:str)->dict:
        """Calculate bounded decimal arithmetic using +,-,*,/ and parentheses. No code execution."""
        return perform(generation,request_id,lambda:calculate(expression))
    @app.tool()
    async def get_current_time(utc_offset_minutes:StrictInt,generation:StrictInt,request_id:str)->dict:
        """Read the Pi clock at an explicit UTC offset in minutes; Manila is480. Does not change the clock."""
        return perform(generation,request_id,lambda:current_time(utc_offset_minutes))
    return app

if __name__=='__main__':logging.disable(logging.CRITICAL);server().run(transport='stdio')
