"""Normal-mode acknowledgment punctuation adapter; keep the shared overlap rules."""
import re,unicodedata
from me3_agent.overlap import attach as attach_overlap
from me3_completion.policy import ACKNOWLEDGMENTS,annotation

def acknowledgment_surface(text):
    # ASR may wrap an English acknowledgment in inverted punctuation or quotes.
    # Keep the word, duration policy, pending-action checks and all commands intact.
    if not isinstance(text,str) or annotation(text,0)['word_count'] not in {1,2}:return text
    surface=text.strip()
    while surface and unicodedata.category(surface[0]).startswith('P'):
        surface=surface[1:].lstrip()
    while surface and unicodedata.category(surface[-1]).startswith('P'):
        surface=surface[:-1].rstrip()
    # ASR may separate the two syllables of the same non-command sound.
    # Only whitespace variants are equivalent; extra words/inner punctuation
    # remain a takeover, and the pending-action guard runs before this adapter.
    split={'uh huh':'uh-huh','mm hmm':'mm-hmm','mm hm':'mm-hm'}
    normalized=re.sub(r'\s+',' ',surface.casefold())
    if normalized in split:return split[normalized]
    return surface if surface.casefold() in ACKNOWLEDGMENTS else text

def attach(processor,clients,provider,events):
    class AcknowledgmentClient:
        async def call(self,role,generation,**arguments):
            data=await clients.call(role,generation,**arguments)
            if role=='asr' and not getattr(provider,'pending',None):
                text=data['text'];surface=acknowledgment_surface(text)
                if surface!=text:
                    events.emit('assistant_ack_surface_normalized',generation=generation,word_count=1,boundary_punctuation_only=True,text_retained=False)
                    return {**data,'text':surface}
            return data
    attach_overlap(processor,AcknowledgmentClient(),provider,events)
