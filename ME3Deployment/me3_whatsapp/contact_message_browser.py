"""Send only within the current visibly verified contact, with exact UI readback."""
import asyncio,json,re,time
from .guard import Rejected
from .backend import FIND
from .contact_browser import ContactBrowser,PHONE,PROFILE,script,VISIBLE

CHECK=r'''const v=window.__me3VerifiedContact,m=document.querySelector('#main'),h=m?.querySelector('header');const valid=location.origin==='https://web.whatsapp.com'&&!!document.querySelector('#pane-side')&&!!v&&v.binding===BINDING&&v.label===LABEL&&v.phone===PHONE_NUMBER&&v.header===h&&h?.isConnected&&Array.from(h.querySelectorAll('span[title],span[dir="auto"]')).some(e=>(e.textContent||'').trim()===LABEL&&(!e.hasAttribute('title')||e.getAttribute('title').trim()===LABEL))&&!h.querySelector('[data-icon="default-group"],[data-icon="community"]');'''
# None means only an empty editor's visibility/focus may settle. False is unsafe.
PREPARE_EDITOR='(()=>{'+CHECK+VISIBLE+r'''if(!valid||Array.from(document.querySelectorAll('[aria-label="End call"],[aria-label="Hang up"],[role="dialog"]')).some(visible))return false;const all=Array.from(m.querySelectorAll('footer [contenteditable="true"]'));if(all.some(e=>e.innerText.trim()!==''))return false;const a=all.filter(visible);if(a.length===0)return null;if(a.length!==1)return false;const e=a[0];e.focus();if(document.activeElement!==e)return null;const beforeIds=Array.from(m.querySelectorAll('[data-id]')).map(n=>n.getAttribute('data-id')).filter(Boolean);window.__me3ContactDraft={owner:OWNER,editor:e,header:h,phone:PHONE_NUMBER,revision:0};new MutationObserver(()=>window.__me3ContactDraft.revision++).observe(e,{childList:true,subtree:true,characterData:true});return {before_ids:beforeIds}})()'''
OWNED_EMPTY_EDITOR='(()=>{'+CHECK+VISIBLE+r'''const d=window.__me3ContactDraft;if(!valid||!d||d.owner!==OWNER||d.header!==h||d.phone!==PHONE_NUMBER||!d.editor.isConnected||d.editor.innerText.trim()!==''||document.activeElement!==d.editor||Array.from(document.querySelectorAll('[aria-label="End call"],[aria-label="Hang up"],[role="dialog"]')).some(visible))return false;const all=Array.from(m.querySelectorAll('footer [contenteditable="true"]'));if(all.some(e=>e.innerText.trim()!==''))return false;const a=all.filter(visible);return a.length===1&&a[0]===d.editor})()'''

def fill(source,**values):return re.sub(r'\b(?:BINDING|LABEL|PHONE_NUMBER|OWNER|TEXT|REVISION|MESSAGE_ID|BEFORE_IDS)\b',lambda m:json.dumps(values[m[0]],ensure_ascii=False),source)

class ContactMessageBrowser:
 def __init__(self,backend,recipient,owner,text):self.backend=backend;self.recipient=recipient;self.owner=owner;self.text=text
 def expression(self,source,**extra):
  d=self.recipient['destination'];return fill(source,BINDING=self.recipient['binding'],LABEL=d['label'],PHONE_NUMBER=d['phone'],OWNER=self.owner,TEXT=self.text,**extra)
 async def evaluate(self,source,**extra):return await self.backend.evaluate(self.expression(source,**extra))
 async def verified(self):
  if await ContactBrowser(self.backend).target()!=self.recipient['target_id']:raise Rejected('contact_window_changed')
  if await self.evaluate('(()=>{'+CHECK+'return valid})()') is not True:raise Rejected('current_selected_contact_identity_required')
 async def fresh_phone(self):
  await self.verified();b=self.backend;contact=ContactBrowser(b);d=self.recipient['destination']
  value=await b.evaluate(script(PHONE,LABEL=d['label'],BINDING=self.recipient['binding']))
  if not value.get('verified'):
   point=await b.evaluate(script(PROFILE,LABEL=d['label']))
   if point.get('ready') is not True:raise Rejected('current_contact_profile_control_required')
   await contact.click(point)
   for _ in range(25):
    value=await b.evaluate(script(PHONE,LABEL=d['label'],BINDING=self.recipient['binding']))
    if value.get('verified'):break
    await asyncio.sleep(.1)
  if value.get('label')!=d['label'] or value.get('phone')!=d['phone'] or value.get('verified') is not True:raise Rejected('fresh_visible_contact_phone_mismatch')
  # Close only this contact-info panel so its overlay cannot hide the editor.
  point=await self.evaluate('(()=>{'+CHECK+VISIBLE+r'''if(!valid)return {};const heads=Array.from(document.querySelectorAll('h1,h2,h3,span,div')).filter(e=>e.children.length===0&&visible(e)&&e.textContent.trim()==='Contact info');if(heads.length!==1)return {};for(let p=heads[0].parentElement;p&&p!==document.body;p=p.parentElement){if(p.contains(m)||p.contains(document.querySelector('#pane-side')))break;const a=Array.from(p.querySelectorAll('[aria-label="Close"]')).filter(visible);if(a.length===1&&p.innerText.replace(/\D/g,'').includes(PHONE_NUMBER.slice(1))){const r=a[0].getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}}}return {}})()''')
  if point.get('x') is None:raise Rejected('verified_contact_panel_close_required')
  await contact.click(point);await self.verified()
 async def compose(self):
  await self.fresh_phone()
  # The verified contact-info panel may still cover the editor while closing.
  # Wait only for readiness; never clear a draft or repeat a message operation.
  deadline=time.monotonic()+1.5
  while True:
   await self.verified()
   result=await self.evaluate(PREPARE_EDITOR)
   if isinstance(result,dict) and 'before_ids' in result:break
   if result is not None or time.monotonic()>=deadline:raise Rejected('one_empty_contact_editor_required_existing_draft_preserved')
   await asyncio.sleep(min(.1,max(0,deadline-time.monotonic())))
  await self.verified()
  if await self.evaluate(OWNED_EMPTY_EDITOR) is not True:raise Rejected('exact_contact_editor_changed_before_insert')
  self.backend.check();await self.backend.page.call('Input.insertText',dict(text=self.text))
  # Chromium may acknowledge insertion before the contenteditable/observer
  # publishes it. Wait only for exact owned readback; never insert again.
  deadline=time.monotonic()+1.5;stable_revision=None;previous_revision=None
  try:
   async with asyncio.timeout(1.5):
    while True:
     await self.verified();self.backend.check()
     snapshot=await self.snapshot();self.backend.check()
     revision=snapshot.get('revision')
     if snapshot.get('valid') is not True or type(revision) is not int or revision<0 or (previous_revision is not None and revision<previous_revision):raise Rejected('exact_contact_draft_not_verified')
     previous_revision=revision
     if snapshot.get('text')==self.text:
      if stable_revision==revision:
       await self.verified();self.backend.check()
       return result
      stable_revision=revision
     else:stable_revision=None
     remaining=deadline-time.monotonic()
     if remaining<=0:raise Rejected('exact_contact_draft_not_verified')
     await asyncio.sleep(min(.05,remaining))
  except TimeoutError:
   raise Rejected('exact_contact_draft_not_verified') from None
 async def snapshot(self):return await self.evaluate('(()=>{'+CHECK+r'''const d=window.__me3ContactDraft;return {valid:valid&&!!d&&d.owner===OWNER&&d.header===h&&d.phone===PHONE_NUMBER&&d.editor.isConnected,text:d?.editor?.innerText.trim(),revision:d?.revision}})()''')
 async def click_send(self,snapshot):
  return await self.evaluate('(()=>{'+CHECK+VISIBLE+r'''const d=window.__me3ContactDraft;if(!valid||!d||d.owner!==OWNER||d.header!==h||d.phone!==PHONE_NUMBER||!d.editor.isConnected||d.editor.innerText.trim()!==TEXT||d.revision!==REVISION)return false;const footer=m.querySelector('footer');if(!footer)return false;const a=Array.from(footer.querySelectorAll('button[aria-label="Send"],[role="button"][aria-label="Send"]'));const icons=Array.from(footer.querySelectorAll('[data-icon="send"],[data-icon="wds-ic-send-filled"]')).map(e=>e.closest('button,[role="button"]')).filter(Boolean);const controls=[...new Set([...a,...icons])].filter(visible);if(controls.length!==1)return false;controls[0].click();return true})()''',REVISION=snapshot['revision'])
 async def find(self,message_id=None,before_ids=None):
  await self.verified()
  start=FIND.index(" const m=document.querySelector")
  end=FIND.index(" const bubble=",start)
  source=FIND[:start]+CHECK+'if(!valid)return {exact_message:false};\n'+FIND[end:]
  return await self.evaluate(source,MESSAGE_ID=message_id,BEFORE_IDS=before_ids or [])
 async def observe(self,before_ids):
  end=time.monotonic()+8
  while True:
   s=await self.snapshot()
   if s.get('valid') is not True or s.get('text')!='':return dict(exact_message=False)
   value=await self.find(before_ids=before_ids)
   if value.get('exact_message') and value.get('status')!='pending':return value
   if time.monotonic()>end:return value
   await asyncio.sleep(.2)
