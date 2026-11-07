import asyncio,base64,unittest
from copy import deepcopy
from speech_mcp.controller import TurnController

class Events:
    def __init__(self):self.rows=[]
    def emit(self,event,**fields):self.rows.append({'event':event,**fields})
class Clients:
    text='Count slowly from one to ten.'
    async def call(self,role,generation,**kwargs):
        if role=='asr':return {'text':self.text}
        return {'pcm_b64':base64.b64encode(bytes(960)).decode()}
class Provider:
    def __init__(self,fail=False):self.inputs=[];self.fail=fail
    async def answer(self,history):
        self.inputs.append(deepcopy(history))
        if self.fail:raise ValueError('synthetic reasoning failure')
        return 'One, two, three.' if len(self.inputs)==1 else 'Four.'

class ContextRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_canceled_reply_is_explicitly_closed_before_latest_question(self):
        started=asyncio.Event();never=asyncio.Event();clients=Clients();provider=Provider();events=Events();played=[]
        async def play(pcm,generation):
            if generation==1:started.set();await never.wait()
            played.append(generation)
        controller=TurnController(clients,provider,events,play)
        first=controller.interrupt();controller.submit(bytes(6400),first);old=controller.task
        await started.wait();new=controller.interrupt()
        with self.assertRaises(asyncio.CancelledError):await old
        clients.text='Stop and tell me what two plus two is.'
        controller.submit(bytes(6400),new);await controller.task
        supplied=provider.inputs[-1]
        self.assertEqual(supplied[-1]['content'][0]['text'],clients.text)
        self.assertEqual(supplied[-2]['control_state'],'cancelled')
        self.assertIn('UNSPOKEN CONTROL',supplied[-2]['content'][0]['text'])
        self.assertEqual(supplied[0]['type'],'user_input') # Facts are preserved.
        self.assertEqual(played,[new]);self.assertIsNone(controller.pending_history_generation)
        self.assertNotIn('control_state',controller.history[-1])
        self.assertEqual(controller.history[-1]['content'][0]['text'],'Four.')
        controller.close_pending_history(first,'failed')
        self.assertNotIn('control_state',controller.history[-1]) # Late old task cannot change new history.
        await controller.close()
    async def test_failed_reasoning_is_not_left_as_an_outstanding_request(self):
        async def play(*args):pass
        controller=TurnController(Clients(),Provider(fail=True),Events(),play)
        generation=controller.interrupt();controller.submit(bytes(6400),generation);await controller.task
        self.assertEqual(controller.history[-1]['control_state'],'failed')
        self.assertIsNone(controller.pending_history_generation)
        self.assertEqual(len(controller.history),2)
        await controller.close()
