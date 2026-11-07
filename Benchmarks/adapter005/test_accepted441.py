"""Actual Proxy/recognize/prefetch and segment policy; authored ASR only."""
import asyncio,tempfile,time,unittest
from types import SimpleNamespace as NS
from stage_observer import StageObserver
from test_changed441 import Events,run

class AcceptedTranscript(unittest.TestCase):
 def test_actual_accepted_prefix_adoption_empty_and_unadopted(self):
  from me3_completion.controller import ConversationController
  async def case():
   raw=[];notices=[];next_text='Please give me one short study tip.'
   async def asr(role,generation,**kw):
    raw.append((role,generation));return dict(text=next_text,request_id='fresh-'+str(len(raw)))
   async def play(*a,**kw):pass
   c=ConversationController(NS(call=asr),NS(pending=None),Events(),play)
   async def notice(*a,**kw):notices.append(a);return True
   c.speak=notice;c.clock.advance()
   # Actual submit_recognized prepares the slot; no model/turn execution.
   c.spawn=lambda coro:coro.close()
   def slot(candidate=None):
    c.slot=dict(generation=1,pcm=b'\0\0'*160,stage='asr',submitted=time.monotonic(),candidate=candidate)
   with tempfile.TemporaryDirectory() as directory:
    async def control(row):pass
    async def pcm(data):pass
    bridge=NS(core=NS(send_control=control,send_pcm=pcm),ready=asyncio.Event(),write_blocked=False)
    r=NS(bridge=bridge,processor=NS(controller=c),log_dir=directory,document={'source_revision':'f'*64},sha256='a'*64)
    o=StageObserver(r,c.events);bridge.ready.set()
    try:
     slot(dict(text='I have an exam tomorrow',submitted=time.monotonic(),pcm=None,generation=0))
     value=await c.clients.call('asr',1,pcm_b64='authored')
     self.assertEqual(value['text'],'I have an exam tomorrow Please give me one short study tip.')
     self.assertEqual(o.rows[-1]['text'],value['text'])
     self.assertNotEqual(o.rows[-1]['text'],next_text)
     # An unadopted actual prefetch/client result is not an accepted transcript.
     count=len(o.rows);await c.real_clients.call('asr',1,pcm_b64='unadopted');self.assertEqual(len(o.rows),count)
     prior=len(raw)
     c.submit_recognized(b'\0\0'*160,1,dict(text='What is two plus two?',request_id='adopted',generation=1))
     value=await c.clients.call('asr',1,pcm_b64='adopted')
     self.assertEqual(len(raw),prior);self.assertNotIn(1,c.prefetch.ready)
     self.assertEqual(o.rows[-1]['text'],value['text']);self.assertEqual(o.rows[-1]['request_id'],'adopted')
     count=len(o.rows);next_text='';slot()
     self.assertEqual((await c.clients.call('asr',1,pcm_b64='empty'))['text'],'')
     self.assertEqual(len(o.rows),count);self.assertTrue(notices)
     next_text='This stale result must not be admitted.';c.clock.advance();slot()
     await c.clients.call('asr',1,pcm_b64='stale');self.assertEqual(len(o.rows),count)
     await o.flush()
    finally:o.finish();await c.close()
  run(case())
