"""Command-scoped official visible directory. No inbox, export, send or dial API."""
import asyncio,json,re,secrets,time
from .guard import Rejected
from .ui_controls import navigate

VISIBLE=r'''const visible=e=>{const r=e.getBoundingClientRect();if(r.width<=0||r.height<=0)return false;const h=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return !!h&&(e===h||e.contains(h))};'''
FIELD=r'''(()=>{if(location.origin!=='https://web.whatsapp.com'||!document.querySelector('#pane-side'))return {};'''+VISIBLE+r'''const a=Array.from(document.querySelectorAll('input[role="textbox"],input[type="text"],[contenteditable="true"][role="textbox"]')).filter(e=>visible(e)&&!e.closest('#main')&&!e.closest('#pane-side'));const backs=Array.from(document.querySelectorAll('[aria-label="Back"]')).filter(visible);if(a.length!==1||backs.length!==1)return {fields:a.length,backs:backs.length};const e=a[0];let panel=e.parentElement;while(panel&&panel!==document.body&&!panel.contains(backs[0]))panel=panel.parentElement;if(!panel||panel===document.body||panel.contains(document.querySelector('#main'))||panel.contains(document.querySelector('#pane-side')))return {};if((e.value||e.innerText||'').trim()!=='')return {existing_query:true};window.__me3ContactSearch={nonce:NONCE,field:e,panel,rows:new Map()};e.focus();return {ready:document.activeElement===e}})()'''
RESULTS=r'''(()=>{const d=window.__me3ContactSearch;if(location.origin!=='https://web.whatsapp.com'||!d||d.nonce!==NONCE||!d.field.isConnected||!d.panel.isConnected||(d.field.value||d.field.innerText||'')!==QUERY)return {};'''+VISIBLE+r'''const norm=s=>s.normalize('NFKC').toLocaleLowerCase().replace(/\s+/g,' ').trim();const q=norm(QUERY),rows=Array.from(d.panel.querySelectorAll('[role="listitem"]')).filter(visible);const matches=[];let total=0;for(const row of rows){const labels=Array.from(row.querySelectorAll('span[title],span[dir="auto"]')).filter(e=>e.children.length===0&&visible(e)).map(e=>(e.getAttribute('title')||e.textContent||'').trim()).filter(s=>s&&s.length<=200&&norm(s).includes(q));const names=[...new Set(labels)];if(names.length!==1)continue;total++;if(matches.length>=8)continue;const label=names[0];const group=!!row.querySelector('[data-icon="default-group"],[data-icon="community"]');if(group)continue;let key;for(const [k,v] of d.rows)if(v.row===row&&v.label===label)key=k;if(!key){key=d.nonce+'-'+String(d.rows.size);d.rows.set(key,{row,label});}matches.push({kind:'person',label,phone:null,binding:key,identity_verified:false});}return {ready:!d.panel.querySelector('[role="progressbar"]')&&(matches.length>0||/no (?:contacts|results) found/i.test(d.panel.innerText)),matches,truncated:total>8,query:QUERY}})()'''
CLICK_ROW=r'''(()=>{const d=window.__me3ContactSearch,v=d?.rows.get(BINDING);if(location.origin!=='https://web.whatsapp.com'||!v||!v.row.isConnected)return false;'''+VISIBLE+r'''if(!visible(v.row))return false;const r=v.row.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2,label:v.label}})()'''
PROFILE=r'''(()=>{if(location.origin!=='https://web.whatsapp.com')return {};'''+VISIBLE+r'''const main=document.querySelector('#main'),header=main?.querySelector('header');if(!header||!header.isConnected)return {};const names=Array.from(header.querySelectorAll('span[title],span[dir="auto"]')).map(e=>(e.getAttribute('title')||e.textContent||'').trim());if(!names.includes(LABEL))return {changed:true};if(header.querySelector('[data-icon="default-group"],[data-icon="community"]'))return {group:true};const editors=Array.from(main.querySelectorAll('footer [contenteditable="true"]')).filter(visible);if(editors.some(e=>e.innerText.trim()!==''))return {draft:true};const controls=Array.from(header.querySelectorAll('[aria-label="Profile details"]')).filter(visible);if(controls.length!==1)return {controls:controls.length};const r=controls[0].getBoundingClientRect();return {ready:true,x:r.x+r.width/2,y:r.y+r.height/2}})()'''
PHONE=r'''(()=>{if(location.origin!=='https://web.whatsapp.com')return {};'''+VISIBLE+r'''const headings=Array.from(document.querySelectorAll('h1,h2,h3,span,div')).filter(e=>e.children.length===0&&visible(e)&&e.textContent.trim()==='Contact info');if(headings.length!==1)return {};let p=headings[0].parentElement;while(p&&p!==document.body){if(p.contains(document.querySelector('#main'))||p.contains(document.querySelector('#pane-side')))break;const close=Array.from(p.querySelectorAll('[aria-label="Close"]')).filter(visible);if(close.length===1&&p.querySelector('span,div')){const text=p.innerText;const phones=[...new Set((text.match(/\+[1-9][0-9 ()\-]{6,24}[0-9]/g)||[]).map(s=>'+'+s.replace(/\D/g,'')))].filter(s=>/^\+[1-9][0-9]{6,14}$/.test(s));if(phones.length===1&&text.includes(LABEL)&&!p.querySelector('[data-icon="default-group"]')){const header=document.querySelector('#main header');if(!header||!Array.from(header.querySelectorAll('span[title],span[dir="auto"]')).some(e=>(e.getAttribute('title')||e.textContent||'').trim()===LABEL))return {};window.__me3VerifiedContact={binding:BINDING,label:LABEL,phone:phones[0],header,headerText:header?.innerText,at:Date.now()};return {verified:true,label:LABEL,phone:phones[0],kind:'person'};}}p=p.parentElement;}return {verified:false}})()'''

def script(source,**values):
 return re.sub(r'\b(?:NONCE|QUERY|BINDING|LABEL)\b',lambda m:json.dumps(values[m.group()],ensure_ascii=False),source)

class ContactBrowser:
 def __init__(self,backend):self.backend=backend
 async def target(self):
  value=await self.backend.page.call('Target.getTargetInfo');self.backend.check();target=value.get('targetInfo',{}).get('targetId','')
  if not re.fullmatch('[A-Za-z0-9_-]{1,128}',target):raise Rejected('owned_contact_target_required')
  return target
 async def click(self,point):
  if not isinstance(point,dict) or any(type(point.get(k)) not in (int,float) for k in ['x','y']):raise Rejected('verified_contact_control_required')
  b=self.backend;b.check()
  await b.page.call('Input.dispatchMouseEvent',dict(type='mousePressed',button='left',clickCount=1,x=point['x'],y=point['y']));b.check()
  await b.page.call('Input.dispatchMouseEvent',dict(type='mouseReleased',button='left',clickCount=1,x=point['x'],y=point['y']))
 async def search_contacts(self,query):
  b=self.backend
  # Opening/searching happens only after host Command validation and durable
  # lookup reservation in ContactService. Never overwrite a manual search.
  safe=await b.evaluate("(()=>{const m=document.querySelector('#main');return !Array.from(m?.querySelectorAll('footer [contenteditable=\"true\"]')||[]).some(e=>e.innerText.trim()!=='')&&!Array.from(document.querySelectorAll('[aria-label=\"End call\"],[aria-label=\"Hang up\"],[role=\"dialog\"]')).some(e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0})})()")
  if safe is not True:raise Rejected('draft_call_or_dialog_preserved_before_lookup')
  nonce=secrets.token_hex(16);value=await b.evaluate(script(FIELD,NONCE=nonce))
  if value.get('existing_query'):raise Rejected('existing_contact_query_preserved')
  if not value.get('ready'):
   await navigate(b,'Chats');await navigate(b,'New chat')
  deadline=time.monotonic()+5
  while time.monotonic()<deadline:
   value=await b.evaluate(script(FIELD,NONCE=nonce))
   if value.get('existing_query'):raise Rejected('existing_contact_query_preserved')
   if value.get('ready'):break
   await asyncio.sleep(.1)
  if not value.get('ready'):raise Rejected('one_foreground_contact_search_required')
  b.check();await b.page.call('Input.insertText',dict(text=query))
  deadline=time.monotonic()+7;old=None;stable=0
  while time.monotonic()<deadline:
   value=await b.evaluate(script(RESULTS,NONCE=nonce,QUERY=query));b.check()
   if value.get('ready'):
    stable=stable+1 if value==old else 0
    # Debounce even empty observations so startup emptiness is not no-match.
    if stable>=3:return dict(official=True,linked=True,target_id=await self.target(),query=query,matches=value['matches'],truncated=value['truncated'])
    old=value
   await asyncio.sleep(.35)
  raise Rejected('contact_search_not_stable_no_retry')
 async def inspect_contact(self,row):
  b=self.backend
  if await self.target()!=row['target_id']:raise Rejected('contact_window_changed')
  binding=row['binding'];label=row['destination']['label']
  point=await b.evaluate(script(CLICK_ROW,BINDING=binding));b.check()
  if point.get('label')!=label:raise Rejected('contact_result_changed')
  await self.click(point);deadline=time.monotonic()+5
  while True:
   point=await b.evaluate(script(PROFILE,LABEL=label))
   if point.get('changed') or point.get('group') or point.get('draft'):raise Rejected('person_header_or_draft_not_safe')
   if point.get('ready'):break
   if time.monotonic()>deadline:raise Rejected('contact_header_not_verified')
   await asyncio.sleep(.15)
  await self.click(point);deadline=time.monotonic()+5
  while time.monotonic()<deadline:
   value=await b.evaluate(script(PHONE,LABEL=label,BINDING=binding));b.check()
   if value.get('verified'):return value
   await asyncio.sleep(.2)
  raise Rejected('contact_phone_not_visible_no_guess')
