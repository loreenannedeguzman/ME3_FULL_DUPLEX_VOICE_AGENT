"""Preserve deterministic confirmation and independent task read-back."""
import re
from me3_core.task import CoreTaskReasoner
from task_mcp.reasoner import WORDS
from task_mcp.reasoner import normalized,latest_text
class ConversationReasoner(CoreTaskReasoner):
    async def answer(self,history):
        text=normalized(latest_text(history))
        if text in {'stop','stop talking','be quiet'}:
            self.pending=None
            self.events.emit('completion_explicit_stop',generation=self.generation(),task_write=False)
            return 'Stopped.'
        # A new explicit quantity/item during confirmation is a revised proposal,
        # never an authorization. The original completed-prompt rule still applies.
        quantity='(?:'+ '|'.join(WORDS[1:])+r'|[0-9]{1,2})'
        if self.pending and self.pending.get('phase')=='confirm' and re.fullmatch(quantity+r' [a-z][a-z -]{0,59}',text):
            self.events.emit('completion_task_proposal_revised',generation=self.generation(),write_executed=False)
            self.pending=None
            return await self.prepare(text,self.generation())
        return await super().answer(history)
