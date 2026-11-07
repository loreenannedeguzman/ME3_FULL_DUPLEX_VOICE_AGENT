"""M7 validation/release barriers preserved; core server selected explicitly."""
from me3_m7.client import M7SpeechClients
class CoreSpeechClients(M7SpeechClients):
    def __init__(self,events,module='me3_core.server',profile=None):
        super().__init__(events,module,profile);self.deadline_once=None
    def arm_controlled_timeout(self):
        if self.deadline_once is not None:raise ValueError('timeout_already_armed')
        self.deadline_once=100
    async def call(self,role,generation,**arguments):
        if role=='asr' and self.deadline_once is not None:
            deadline=self.deadline_once;self.deadline_once=None
            self.events.emit('core_controlled_deadline',generation=generation,deadline_ms=deadline,scope='one actual microphone ASR request; controlled local deadline, not natural provider failure')
            arguments['deadline_ms']=deadline
        return await super().call(role,generation,**arguments)
