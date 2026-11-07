'use strict';
const $=id=>document.getElementById(id),csrf=document.querySelector('meta[name="me3-csrf"]').content;
let current=null,busy=false,rendered=null,acknowledged=null,refreshing=false,disconnected=false;
const states={preparing_speech:'Preparing voice…',waiting_capacity:'Waiting for service capacity…',stopping:'Stopping…',stopped:'Ready',starting:'Connecting…',ready:'Ready',listening:'Listening',user_holds_turn:'Listening',pause_hold:'Listening',processing:'Thinking…',processing_user_turn:'Thinking…',speaking:'Speaking',agent_response:'Speaking',agent_listener_backchannel:'Listening',response_suspended_for_classification:'Listening',awaiting_display:'Responding…',closed:'Stopped'};
const taskStates={started:'In progress',preparing:'Preparing',needs_confirmation:'Awaiting confirmation',reading:'Working',read_received:'Result received',verified:'Verified',completed:'Completed',failed:'Failed',canceled:'Canceled',uncertain:'Outcome uncertain',not_run:'Not started',execution_failed:'Failed',running:'Working',received:'Result received'};
async function request(action,extra={}){
  const r=await fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/json','X-ME3-CSRF':csrf},body:JSON.stringify({action,...extra})});
  const d=await r.json();if(!r.ok)throw new Error(d.error||'The request could not be completed.');return d;
}
function showNotice(text){$('notice').textContent=text;$('notice').hidden=!text;}
function blocked(gate){return gate?.ready?'':gate?.reason==='operating_policy_revoked'?'Conversation is paused.': 'Conversation is unavailable. Please check the Pi connection and operating policy.';}
async function switchMode(mode){
  if(current?.active&&current.mode===mode)return;
  const gate=current?.gates?.[mode==='voice'?'normal':'text'];if(!gate?.ready)throw new Error(blocked(gate));
  if(current?.active)await request('stop');
  await request(mode==='voice'?'start_voice':'start_text');
  // Readiness is local state, never a repeated provider request or an automatic retry.
  for(let i=0;i<40;i++){
    await refresh();if(current?.active&&current.mode===mode&&((mode==='text'&&current.can_send)||(mode==='voice'&&current.state!=='starting')))return;
    if(current?.error&&!current.active)throw new Error(current.error);
    await new Promise(resolve=>setTimeout(resolve,250));
  }
  throw new Error('The Pi is still starting. Check its status before continuing.');
}
async function action(fn){if(busy)return;busy=true;try{showNotice('');await fn();}catch(e){showNotice(e.message.replaceAll('_',' '));}finally{busy=false;await refresh();}}
function draw(s){
  if(disconnected){if(!s.error)showNotice('');disconnected=false;}
  current=s;$('connection-dot').classList.add('online');$('status').textContent=states[s.state]||'Working';
  $('stop').hidden=!s.active;$('clear').disabled=busy||s.active;
  $('voice').classList.toggle('active',s.active&&s.mode==='voice');
  $('voice').setAttribute('aria-label',s.active&&s.mode==='voice'?'Stop voice conversation':'Start voice conversation');
  $('voice').title=s.active&&s.mode==='voice'?'Stop voice conversation':'Start voice conversation';
  $('voice').disabled=busy||(!(s.active&&s.mode==='voice')&&!s.gates.normal.ready);
  $('message').disabled=false;$('send').disabled=busy||!$('message').value.trim()||!s.gates.text.ready;
  if(s.error)showNotice(s.error);
  const signature=JSON.stringify(s.messages);
  if(signature!==rendered){
    const pane=document.querySelector('.conversation'),follow=pane.scrollHeight-pane.scrollTop-pane.clientHeight<90;
    rendered=signature;$('messages').replaceChildren();$('empty').hidden=s.messages.length>0;
    for(const m of s.messages){const el=document.createElement('article');el.className='message '+(m.role==='user'?'user':'assistant');const who=document.createElement('p');who.className='who';who.textContent=m.role==='user'?'You':({'preparing':'ME3 · Preparing','interrupted':'ME3 · Interrupted','failed':'ME3 · Not completed'}[m.state]||'ME3');const bubble=document.createElement('div');bubble.className='bubble';bubble.textContent=m.text;el.append(who,bubble);$('messages').append(el);}
    if(follow)pane.scrollTop=pane.scrollHeight;
  }
  const tasks=s.tasks||[];$('tasks').hidden=!tasks.length;$('task-list').replaceChildren();
  for(const task of tasks){const item=document.createElement('li');item.dataset.state=task.state;item.textContent=task.label+' · '+(taskStates[task.state]||'In progress');$('task-list').append(item);}
  const due=(s.reminders?.reminders||[]).filter(r=>r.state==='due');$('reminders').hidden=!due.length;$('reminders').replaceChildren();
  for(const r of due){const text=document.createElement('p');text.setAttribute('role','alert');text.textContent=r.label;const button=document.createElement('button');button.textContent='Dismiss';button.onclick=()=>action(()=>request('ack_reminder',{reminder_id:r.id}));text.append(button);$('reminders').append(text);}
  if(s.pending_display&&s.pending_display!==acknowledged&&!busy){acknowledged=s.pending_display;requestAnimationFrame(()=>request('ack',{id:s.pending_display}).catch(e=>{acknowledged=null;showNotice('The response could not be acknowledged.');}));}
}
async function refresh(){if(refreshing)return;refreshing=true;try{const r=await fetch('/api/state',{cache:'no-store'});if(!r.ok)throw new Error();draw(await r.json());}catch(e){disconnected=true;$('status').textContent='Disconnected';$('connection-dot').classList.remove('online');$('voice').disabled=true;$('send').disabled=true;showNotice('Connection lost. The Pi may still be active. Reconnect to stop it.');}finally{refreshing=false;}}
$('voice').onclick=()=>action(async()=>{if(current?.active&&current.mode==='voice')await request('stop');else await switchMode('voice');});
$('stop').onclick=()=>action(()=>request('stop'));
$('clear').onclick=()=>action(()=>request('clear'));
$('composer').onsubmit=e=>{e.preventDefault();const text=$('message').value.trim();if(!text)return;action(async()=>{await switchMode('text');if(current.state==='processing'||current.pending_display)await request('cancel');await request('send',{text});$('message').value='';$('message').style.height='auto';});};
$('message').oninput=()=>{$('message').style.height='auto';$('message').style.height=Math.min(160,$('message').scrollHeight)+'px';if(current)$('send').disabled=busy||!$('message').value.trim()||!current.gates.text.ready;};
$('message').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('composer').requestSubmit();}};
refresh();setInterval(refresh,750);
