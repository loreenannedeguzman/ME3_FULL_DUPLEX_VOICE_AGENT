"""Explicit inert MCP entry point for bounded operator tests, never a fallback."""
import argparse,asyncio,logging
from contextlib import asynccontextmanager
from pathlib import Path
from task_mcp.client import ROOT
from .server import make_server
from .service import Service,Store
class FixtureBackend:
 def __init__(self):self.current=lambda:True;self.text=None;self.owner=None;self.sent={};self.effects=0;self.last_observation=None;self.fail=None
 @asynccontextmanager
 async def transaction(self,allow_start=True):yield self
 async def session_status(self):return dict(available=True,linked=True,recipient_scope='self',fixture=True,inbox_access=False,calling=False)
 async def open_self(self):pass
 async def compose(self,text,owner):
  self.text=text;self.owner=owner;return dict(owner=owner,before_ids=list(self.sent),baseline_version=2)
 async def snapshot(self):return dict(official=True,linked=True,self_recipient=True,text=self.text,recipient_key=self.owner,editor_revision=self.owner+':1')
 async def click_send(self):
  self.effects+=1
  if self.fail:raise self.fail
  self.sent['fixture-'+str(self.effects)]=self.text;self.text='';return True
 async def observe(self,text,recipient):
  value=await self.find(text);value['recipient_key']=recipient;self.last_observation=value;return value
 async def find(self,text,message_id=None,before_ids=None):
  matches=[key for key,value in self.sent.items() if value==text and (key==message_id if message_id else key not in (before_ids or []))]
  return dict(exact_message=True,message_id=matches[0],status='submitted',delivery_confirmed=False,read_confirmed=False) if len(matches)==1 else dict(exact_message=False,status=None)
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);args=parser.parse_args();path=Path(args.root)
 if path.is_symlink() or not path.is_absolute() or not path.resolve().is_relative_to((ROOT/'.tmp').resolve()) or not path.is_dir():raise SystemExit('Explicit existing project fixture root required')
 service=Service(path,Store(path/'message-fixture.sqlite3'),FixtureBackend());logging.disable(logging.CRITICAL);make_server(service).run(transport='stdio')
if __name__=='__main__':main()
