"""Host-only natural confirmation recognition; never model-created authority."""
from task_mcp.reasoner import CONFIRM,normalized
NATURAL=frozenset({'confirmed','proceed','please proceed','proceed please','yes proceed','proceed with it','go ahead','go ahead please','please go ahead','yes go ahead','go ahead and send it','send it','send it please','please send it','yes send it','yes please send it','do it','please do it','yes do that'})
def confirmation_intent(raw):
 if not isinstance(raw,str) or not 1<=len(raw)<=128:return 'none'
 text=' '.join(normalized(raw.replace('¿','').replace('？','?')).replace(',',' ').split())
 if text not in CONFIRM|NATURAL:return 'none'
 return 'question' if any(p in raw for p in ('?','¿','？')) else 'affirmative'
