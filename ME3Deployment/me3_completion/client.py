"""Unchanged MCP validation/release barriers with explicit server selection."""
from me3_core.client import CoreSpeechClients
class ConversationClients(CoreSpeechClients):
    def __init__(self,events,module='me3_completion.server',profile=None):super().__init__(events,module,profile)
    async def call(self,role,generation,**arguments):
        data=await super().call(role,generation,**arguments)
        if role=='tts':
            metrics=data.get('tts_metrics',{})
            self.events.emit('completion_tts_cache_metadata',generation=generation,request_id=data.get('request_id'),cue_cache_hit=metrics.get('completion_cue_cache_hit',False))
        return data
