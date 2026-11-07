"""Owned official visible WhatsApp UI. No private API, account extraction or model JS."""
import asyncio,hashlib,json,os,re,secrets,shlex,shutil,sqlite3,subprocess,time
from contextlib import asynccontextmanager
from pathlib import Path
import aiohttp
from me3_stream.browser import CDP
from me3_assistant.guard import lock
from .selection import pointer_target
from .guard import Rejected

CHECK=r"""const d=window.__me3SelfDraft,m=document.querySelector('#main'),header=m?.querySelector('header');const official=location.origin==='https://web.whatsapp.com';const self=!!d&&d.owner===OWNER&&!!m&&header===d.header&&d.header.isConnected&&d.header.innerText===d.headerText&&(/\(\s*You\s*\)/i.test(d.headerText)||/Message yourself/i.test(d.headerText));"""
SNAPSHOT='(()=>{'+CHECK+r"""return {official,linked:!!document.querySelector('#pane-side'),self_recipient:self,text:self&&d.editor.isConnected?d.editor.innerText.trim():null,recipient_key:self?d.recipientKey:null,editor_revision:self?d.owner+':'+d.revision:null}})()"""
CLICK='(()=>{'+CHECK+r"""if(!official||!self||!d.editor.isConnected||d.editor.innerText.trim()!==TEXT)return false;const footer=m.querySelector('footer');if(!footer)return false;const visible=e=>e.getBoundingClientRect().width>0&&e.getBoundingClientRect().height>0;const direct=Array.from(footer.querySelectorAll('button[aria-label="Send"],[role="button"][aria-label="Send"]'));const icons=Array.from(footer.querySelectorAll('[data-icon="send"],[data-icon="wds-ic-send-filled"]')).map(e=>e.closest('button,[role="button"]')).filter(Boolean);const targets=Array.from(new Set([...direct,...icons])).filter(visible);if(targets.length!==1)return false;const b=targets[0],r=b.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);if(!hit||!b.contains(hit))return false;b.click();return true})()"""
FIND='(()=>{\n const m=document.querySelector(\'#main\'),h=m?.querySelector(\'header\');\n if(location.origin!==\'https://web.whatsapp.com\'||!h||!(/\\(\\s*You\\s*\\)/i.test(h.innerText)||/Message yourself/i.test(h.innerText)))return {exact_message:false};\n const bubble=\'[data-testid="msg-container"]\';\n const roots=Array.from(m.querySelectorAll(\'[data-id]\')).filter(n=>n.querySelectorAll(bubble).length===1&&!n.querySelector(\'[data-id]\'));\n const incoming=n=>n.matches(\'.message-in\')||!!n.querySelector(\'.message-in,[data-icon="tail-in"]\');\n const markedOut=n=>!incoming(n)&&(n.matches(\'.message-out\')||!!n.querySelector(\'.message-out,[data-icon="tail-out"]\'));\n const outgoing=n=>{\n  if(incoming(n))return false;\n  if(markedOut(n))return true;\n  const panel=n.closest(\'[data-testid="conversation-panel-messages"]\');\n  if(!panel)return false;\n  // Consecutive outgoing messages share a tail on their first row. Accept\n  // only the smallest cluster of distinct one-message rows, below the list.\n  for(let p=n.parentElement,depth=0;p&&p!==panel&&depth<12;p=p.parentElement,depth++){\n   const ids=Array.from(p.querySelectorAll(\'[data-id]\'));\n   if(ids.length<=1)continue;\n   if(ids.length>64||p.querySelectorAll(bubble).length!==ids.length||ids.some(r=>!roots.includes(r)))return false;\n   const children=Array.from(p.children);\n   if(children.length!==ids.length||children.some(c=>c.matches(\'[data-id]\')?false:c.querySelectorAll(\'[data-id]\').length!==1))return false;\n   if(ids.some(incoming)||!ids.includes(n)||!markedOut(ids[0])||ids.slice(1).some(markedOut))return false;\n   return p.querySelectorAll(\'[data-icon="tail-out"],.message-out\').length===1;\n  }\n  return false;\n };\n const rows=[];\n for(const n of roots){\n  const b=n.querySelector(bubble),texts=Array.from(b.querySelectorAll(\'.selectable-text,[data-testid="selectable-text"]\'));\n  const primary=texts.filter(t=>!texts.some(o=>o!==t&&o.contains(t)));\n  if(primary.length!==1||primary[0].innerText.trim()!==TEXT||!outgoing(n))continue;\n  const id=n.getAttribute(\'data-id\');if(!id||(MESSAGE_ID?id!==MESSAGE_ID:BEFORE_IDS.includes(id)))continue;\n  // Receipt indicators belong to this message only, never its group/sibling.\n  const icons=Array.from(n.querySelectorAll(\'[data-icon]\')).map(e=>e.getAttribute(\'data-icon\'));\n  const read=!!n.querySelector(\'[aria-label="Read"],[data-icon="msg-dblcheck-ack"]\');\n  const status=read?\'read\':icons.includes(\'msg-dblcheck\')?\'delivered\':icons.includes(\'msg-check\')?\'sent\':icons.includes(\'msg-time\')?\'pending\':\'submitted\';\n  rows.push({message_id:id,status});\n }\n if(rows.length!==1)return {exact_message:false,matches:rows.length};\n return {exact_message:true,...rows[0],delivery_confirmed:[\'delivered\',\'read\'].includes(rows[0].status),read_confirmed:rows[0].status===\'read\'};\n})()'
SELF=r"""(()=>{if(location.origin!=='https://web.whatsapp.com'||!document.querySelector('#pane-side'))return false;const h=document.querySelector('#main header');return !!h&&(/\(\s*You\s*\)/i.test(h.innerText)||/Message yourself/i.test(h.innerText))})()"""
OWNED_PAGE=r"""(()=>{const d=window.__me3SelfDraft,m=document.querySelector('#main'),h=m?.querySelector('header');return location.origin==='https://web.whatsapp.com'&&!!document.querySelector('#pane-side')&&!!d&&OWNERS.includes(d.owner)&&!!h&&h===d.header&&h.isConnected&&h.innerText===d.headerText&&(/\(\s*You\s*\)/i.test(h.innerText)||/Message yourself/i.test(h.innerText))})()"""

def process_arguments(raw):
 """Chromium may rewrite argv into one title string in /proc/cmdline."""
 fields=[value for value in raw.decode('utf-8').split('\0') if value]
 return shlex.split(fields[0]) if len(fields)==1 else fields

class Backend:
 def __init__(self,root,current=lambda:True):self.root=Path(root);self.current=current;self.page=None;self.owner=None;self.text=None;self.binding=None;self.last_observation=None
 def check(self):
  if not self.current():raise asyncio.CancelledError()
 def expression(self,source):return re.sub(r'\b(?:OWNER|TEXT)\b',lambda m:json.dumps({'OWNER':self.owner,'TEXT':self.text}[m.group(0)]),source)
 async def evaluate(self,source):self.check();return await self.page.evaluate(source)
 def profile(self):
  p=Path.home()/'.local/share/me3-whatsapp-profile'
  if p.is_symlink() or not p.is_dir() or p.stat().st_uid!=os.getuid() or p.stat().st_mode&0o777!=0o700:raise Rejected('private_linked_WhatsApp_profile_required')
  return p
 def browser_running(self,p):
  for f in Path('/proc').glob('[0-9]*/cmdline'):
   try:
    if f.parent.stat().st_uid!=os.getuid() or Path(os.readlink(f.parent/'exe')).name!='chromium':continue
    args=process_arguments(f.read_bytes())
   except (OSError,UnicodeError,ValueError):continue
   if '--user-data-dir='+str(p) in args and not any(a.startswith('--type=') for a in args):return True
  return False
 def known_page_owners(self):
  p=self.root/'data/whatsapp-actions.sqlite3'
  if not p.exists():return []
  if p.is_symlink() or not p.is_file():raise Rejected('untrusted_WhatsApp_ownership_ledger')
  with sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True) as db:rows=db.execute('SELECT DISTINCT owner FROM operations LIMIT 1025').fetchall()
  if len(rows)>1024 or any(not isinstance(r[0],str) or not re.fullmatch('[0-9a-f]{32}',r[0]) for r in rows):raise Rejected('invalid_WhatsApp_page_ownership')
  return [r[0] for r in rows]
 async def select_owned_page(self,session,pages,port):
  if not 1<=len(pages)<=8:raise Rejected('bounded_owned_WhatsApp_pages_required')
  endpoints=[p.get('webSocketDebuggerUrl','') for p in pages]
  if len(set(endpoints))!=len(endpoints) or any(not (u.startswith('ws://127.0.0.1:'+str(port)+'/devtools/page/') or u.startswith('ws://localhost:'+str(port)+'/devtools/page/')) for u in endpoints):raise Rejected('owned_loopback_page_required')
  if len(pages)==1:return CDP(await session.ws_connect(endpoints[0],max_msg_size=100000))
  owners=self.known_page_owners()
  if not owners:raise Rejected('multiple_WhatsApp_pages_without_host_ownership')
  source=re.sub(r'\bOWNERS\b',lambda _:json.dumps(owners),OWNED_PAGE);probes=[];selected=None
  try:
   matches=[]
   for endpoint in endpoints:
    self.check();page=CDP(await session.ws_connect(endpoint,max_msg_size=100000));probes.append(page)
    if await page.evaluate(source) is True:matches.append(page)
   if len(matches)!=1:raise Rejected('unique_host_owned_WhatsApp_page_required')
   selected=matches[0];return selected
  finally:
   for page in probes:
    if page is not selected:await page.ws.close()
 async def connect(self,allow_start):
  p=self.profile()
  if not self.browser_running(p):
   if not allow_start:raise Rejected('WhatsApp_app_not_open')
   binary=shutil.which('chromium');assert binary
   runtime=Path('/run/user')/str(os.getuid());env={k:v for k,v in os.environ.items() if k in {'HOME','USER','LOGNAME','PATH','LANG','LC_ALL','LC_CTYPE','DISPLAY','WAYLAND_DISPLAY','XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS','XAUTHORITY','XDG_SESSION_TYPE'}};env.update(XDG_RUNTIME_DIR=str(runtime),DBUS_SESSION_BUS_ADDRESS='unix:path='+str(runtime/'bus'));env.setdefault('DISPLAY',':0')
   if (runtime/'wayland-0').is_socket():env['WAYLAND_DISPLAY']='wayland-0'
   if (Path.home()/'.Xauthority').is_file():env['XAUTHORITY']=str(Path.home()/'.Xauthority')
   pf=p/'DevToolsActivePort';stamp=pf.stat().st_mtime_ns if pf.exists() else 0
   child=subprocess.Popen([binary,'--no-first-run','--no-default-browser-check','--disable-sync','--disable-features=WebRtcPipeWireCamera,WebRtcAllowInputVolumeAdjustment','--remote-debugging-address=127.0.0.1','--remote-debugging-port=0','--user-data-dir='+str(p),'--window-size=900,700','--app=https://web.whatsapp.com/'],env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True);deadline=time.monotonic()+12
   while not pf.is_file() or pf.stat().st_mtime_ns==stamp:
    self.check()
    if child.poll() is not None or time.monotonic()>deadline:raise Rejected('WhatsApp_browser_start_failed')
    await asyncio.sleep(.1)
  port=int((p/'DevToolsActivePort').read_text().splitlines()[0]);assert 1024<=port<=65535
  session=aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12),trust_env=False)
  try:
   deadline=time.monotonic()+20
   while True:
    self.check()
    async with session.get('http://127.0.0.1:'+str(port)+'/json/list') as response:tabs=await response.json()
    pages=[t for t in tabs if t.get('type')=='page' and t.get('url','').startswith('https://web.whatsapp.com/')]
    if pages:
     page=await self.select_owned_page(session,pages,port);break
    if time.monotonic()>deadline:raise Rejected('official_WhatsApp_page_not_ready')
    await asyncio.sleep(.2)
   while not await page.evaluate("location.origin==='https://web.whatsapp.com'&&!!document.querySelector('#pane-side')"):
    self.check()
    if time.monotonic()>deadline:raise Rejected('WhatsApp_private_phone_link_required')
    await asyncio.sleep(.2)
   return session,page
  except BaseException:await session.close();raise
 @asynccontextmanager
 async def transaction(self,allow_start=True):
  with lock(self.root/'data/whatsapp-browser.lock'):
   session,self.page=await self.connect(allow_start)
   try:yield self
   finally:await session.close();self.page=None
 async def session_status(self):
  try:
   async with self.transaction(False):return dict(available=True,linked=True,recipient_scope=('verified_contacts' if json.loads((self.root/'config/operating_policy.json').read_bytes()).get('routine_whatsapp_contact_messages') is True else 'self'),send_requires_direct_confirmation=True,inbox_access=False,calling=json.loads((self.root/'config/operating_policy.json').read_bytes()).get('routine_whatsapp_calls') is True,call_audio_mode='native_browser_exclusive_voice_handoff',call_requires_exact_review_and_separate_confirmation=True)
  except (Rejected,OSError,ValueError,aiohttp.ClientError):return dict(available=False,linked=False,recipient_scope=('verified_contacts' if json.loads((self.root/'config/operating_policy.json').read_bytes()).get('routine_whatsapp_contact_messages') is True else 'self'),send_requires_direct_confirmation=True,inbox_access=False,calling=json.loads((self.root/'config/operating_policy.json').read_bytes()).get('routine_whatsapp_calls') is True,call_audio_mode='native_browser_exclusive_voice_handoff',call_requires_exact_review_and_separate_confirmation=True)
 async def open_self(self):
  if await self.evaluate(SELF):return
  clicked=await self.evaluate(r"""(()=>{const v=e=>e.getBoundingClientRect().width>0&&e.getBoundingClientRect().height>0;const markers=Array.from(document.querySelectorAll('span,div')).filter(e=>e.children.length===0&&v(e)&&e.textContent.trim()==='Message yourself');if(markers.length===1)return true;const buttons=Array.from(document.querySelectorAll('[aria-label="New chat"]')).filter(v);if(buttons.length!==1)return false;buttons[0].click();return true})()""")
  if clicked is not True:raise Rejected('unique_new_chat_control_required')
  deadline=time.monotonic()+5
  while True:
   snapshot=await self.evaluate(r"""(()=>{const v=e=>e.getBoundingClientRect().width>0&&e.getBoundingClientRect().height>0;const a=Array.from(document.querySelectorAll('span,div')).filter(e=>e.children.length===0&&v(e)&&e.textContent.trim()==='Message yourself');if(a.length!==1)return {official:location.origin==='https://web.whatsapp.com',self_marker_count:a.length};const e=a[0],row=e.closest('[role="listitem"]')||e.closest('[role="button"]')||e.closest('[tabindex="0"]');if(!row)return {};const r=e.getBoundingClientRect(),b=row.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return {official:location.origin==='https://web.whatsapp.com',self_marker_count:1,marker_rect:{x:r.x,y:r.y,w:r.width,h:r.height},row_rect:{x:b.x,y:b.y,w:b.width,h:b.height},viewport:{w:innerWidth,h:innerHeight},hit_inside_exact_self_row:!!hit&&row.contains(hit)}})()""")
   if snapshot.get('self_marker_count')==1:break
   if time.monotonic()>deadline:raise Rejected('unique_self_chat_required')
   await asyncio.sleep(.15)
  target=pointer_target(snapshot);self.check();await self.page.call('Input.dispatchMouseEvent',dict(type='mousePressed',button='left',clickCount=1,**target));self.check();await self.page.call('Input.dispatchMouseEvent',dict(type='mouseReleased',button='left',clickCount=1,**target))
  deadline=time.monotonic()+5
  while not await self.evaluate(SELF):
   if time.monotonic()>deadline:raise Rejected('self_chat_header_not_verified')
   await asyncio.sleep(.15)
 async def compose(self,text,owner):
  await self.open_self();self.owner=owner;self.text=text
  script='(()=>{'+CHECK+r"""const h=m?.querySelector('header');if(!official||!h||!(/\(\s*You\s*\)/i.test(h.innerText)||/Message yourself/i.test(h.innerText)))return false;const editors=Array.from(m.querySelectorAll('footer [contenteditable="true"],[data-tab="10"][contenteditable="true"]')).filter(e=>e.getBoundingClientRect().width>0&&e.getBoundingClientRect().height>0);if(editors.length!==1||editors[0].innerText.trim()!=='')return false;const e=editors[0];const beforeIds=Array.from(m.querySelectorAll('[data-testid="msg-container"],.message-out')).filter(n=>Array.from(n.querySelectorAll('.selectable-text')).some(t=>t.innerText.trim()===TEXT)).map(n=>n.closest('[data-id]')?.getAttribute('data-id')||n.querySelector('[data-id]')?.getAttribute('data-id')).filter(Boolean);window.__me3SelfDraft={owner:OWNER,editor:e,header:h,headerText:h.innerText,recipientKey:OWNER,revision:0,baselineVersion:2,beforeIds};const observer=new MutationObserver(()=>window.__me3SelfDraft.revision++);observer.observe(e,{childList:true,subtree:true,characterData:true});window.__me3SelfDraft.observer=observer;e.focus();return document.activeElement===e})()"""
  if await self.evaluate(self.expression(script)) is not True:raise Rejected('one_empty_editor_required_existing_draft_preserved')
  self.check();await self.page.call('Input.insertText',dict(text=text));snapshot=await self.snapshot()
  if snapshot.get('text')!=text or snapshot.get('self_recipient') is not True:raise Rejected('exact_owned_draft_not_verified')
  self.binding=await self.evaluate(self.expression('(()=>{const d=window.__me3SelfDraft;return {owner:d.owner,before_ids:d.beforeIds,baseline_version:2}})()'));return dict(self.binding)
 async def snapshot(self):return await self.evaluate(self.expression(SNAPSHOT))
 async def click_send(self):return await self.evaluate(self.expression(CLICK))
 async def find(self,text,message_id=None,before_ids=None):
  values=dict(TEXT=text,MESSAGE_ID=message_id,BEFORE_IDS=before_ids or []);source=re.sub(r'\b(?:TEXT|MESSAGE_ID|BEFORE_IDS)\b',lambda m:json.dumps(values[m.group(0)]),FIND);return await self.evaluate(source)
 async def observe(self,text,recipient):
  if text!=self.text or recipient!=self.owner:raise Rejected('owned_message_binding_changed')
  deadline=time.monotonic()+8
  while True:
   snapshot=await self.snapshot()
   if snapshot.get('self_recipient') is not True or snapshot.get('text')!='':return dict(exact_message=False)
   value=await self.find(text,before_ids=self.binding['before_ids']);value['recipient_key']=recipient;self.last_observation=value
   if value.get('exact_message') and value.get('status')!='pending':return value
   if time.monotonic()>deadline:return value
   await asyncio.sleep(.2)
