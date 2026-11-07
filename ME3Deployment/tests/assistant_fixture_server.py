"""Isolated MCP extension fixture; never connected to user task state or providers."""
import asyncio,os
from mcp.server.fastmcp import FastMCP
app=FastMCP('assistant-extension-fixture',log_level='CRITICAL')
@app.tool()
async def count_words(text:str)->dict:
    """Count words in supplied text; read-only, no network or files."""
    return {'words':len(text.split()),'provider_key_present':bool(os.getenv('GOOGLE_API_KEY'))}
@app.tool()
async def delayed_label(label:str)->dict:
    """Return a label after a bounded fixture delay; cancellation test only."""
    await asyncio.sleep(.3);return {'label':label}
@app.tool()
async def hidden_action()->dict:raise AssertionError('hidden_action_must_not_be_called')
if __name__=='__main__':app.run(transport='stdio')
