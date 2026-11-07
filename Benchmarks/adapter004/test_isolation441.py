"""Actual Conversation/Engine/Gateway/Ledger: no private-owner outcomes imported."""
import ast,asyncio,hashlib,importlib.util,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
P=Path(__file__).resolve().parent
class Isolation(unittest.TestCase):
 def test_exact_constructor_private_ledger_and_missing_binding_fail_closed(self):
  from onit_candidate.gateway import Ledger
  from me3_assistant.actions import Journal
  from normal_session_path import allocate_intent_ledger
  from test_changed441 import Events,run
  path=P/'overlay/onit_candidate/conversation.py'
  before=(P/'baseline/onit_candidate/conversation.py').read_text();after=path.read_text()
  old="ledger_path=self.root/'data/onit-action-intents.jsonl'";new='ledger_path=registry._benchmark_intent_ledger_path'
  self.assertEqual(after.count(new),1);self.assertEqual(ast.dump(ast.parse(after.replace(new,old))),ast.dump(ast.parse(before)))
  spec=importlib.util.spec_from_file_location('benchmark_conversation441_isolation',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  async def case():
   with tempfile.TemporaryDirectory() as d:
    root=Path(d);(root/'data').mkdir();(root/'.tmp').mkdir()
    owner=root/'data/onit-action-intents.jsonl';raw=(json.dumps(dict(sequence=1,goal_id='1'*32,intent_key='2'*64,action='propose_contact_message',operation_id='3'*32,state='UNCERTAIN'))+'\n').encode();owner.write_bytes(raw)
    self.assertEqual(len(Ledger(owner,root).rows),1)
    private=allocate_intent_ledger(root,'a'*64,1);second=allocate_intent_ledger(root,'a'*64,2);self.assertNotEqual(private,second)
    with self.assertRaises(FileExistsError):allocate_intent_ledger(root,'a'*64,1)
    registry=NS(config={'actions':[]},session_grant={'allowed_actions':[]},allowed_actions=[],journal=Journal(private.parent/'actions.jsonl'),_benchmark_intent_ledger_path=private)
    ordinary=NS(model='gpt-6.1-sol',provider='openai_chatgpt_plan');events=Events();budget=NS(exhausted=asyncio.Event(),remaining=lambda role:16)
    loads=[];original=Ledger.load
    def observed(self):loads.append(self.path);return original(self)
    with patch('onit_candidate.engine.admit_model',return_value='openai_chatgpt_plan'),patch.object(Ledger,'load',observed):
     c=module.Conversation(root,registry,ordinary,budget,events,module.Context(),voice=True)
     self.assertEqual(c.context.metadata(c.engine.gateway)['action_outcomes'],[])
     self.assertFalse(c.engine.gateway.blockers());self.assertEqual(c.engine.gateway.actions.declarations(),[])
     self.assertTrue(loads and all(p==private for p in loads))
     del registry._benchmark_intent_ledger_path;loads.clear()
     with self.assertRaises(AttributeError):module.Conversation(root,registry,ordinary,budget,events,module.Context(),voice=True)
     self.assertEqual(loads,[])
    self.assertEqual(owner.read_bytes(),raw);self.assertFalse(private.exists())
  run(case())
