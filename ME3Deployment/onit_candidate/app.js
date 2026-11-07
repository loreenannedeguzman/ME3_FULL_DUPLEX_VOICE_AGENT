'use strict';
const $=id=>document.getElementById(id),csrf=document.querySelector('meta[name="me3-csrf"]').content;
let current=null,busy=false,rendered=null,acknowledged=null,refreshing=false,disconnected=false,startupNotice=false;
const states={preparing_speech:'Preparing voice',waiting_capacity:'Waiting for service capacity',stopping:'Stopping',stopped:'Ready',starting:'Connecting',ready:'Ready',listening:'Listening',user_holds_turn:'Listening',pause_hold:'Listening',processing:'Thinking',processing_user_turn:'Thinking',speaking:'Speaking',agent_response:'Speaking',agent_listener_backchannel:'Listening',response_suspended_for_classification:'Listening',awaiting_display:'Responding',closed:'Stopped'};
const taskStates={started:'In progress',preparing:'Preparing',needs_confirmation:'Awaiting confirmation',reading:'Working',read_received:'Result received',verified:'Verified',completed:'Completed',failed:'Failed',canceled:'Canceled',uncertain:'Outcome uncertain',not_run:'Not started',execution_failed:'Failed',running:'Working',received:'Result received'};
async function request(action,extra={}){
  const r=await fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/json','X-ME3-CSRF':csrf},body:JSON.stringify({action,...extra})});
  const d=await r.json();if(!r.ok)throw new Error(d.error||'The request could not be completed.');return d;
}
function showNotice(text){$('notice').textContent=text;$('notice').hidden=!text;}
function blocked(gate){if(!gate?.ready&&/^(Reasoning|Speech recognition) service capacity is unavailable[.]/.test(gate?.reason||''))return gate.reason;return gate?.ready?'':gate?.reason==='operating_policy_revoked'?'Conversation is paused.': 'Conversation is unavailable. Please check the Pi connection and operating policy.';}
async function switchMode(mode){
  if(current?.call?.held)throw new Error('End the WhatsApp call before starting ME3 voice or text.');
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
  if(mode==='voice'){startupNotice=true;showNotice('Connecting. Please wait for Listening before speaking.');return;}
  throw new Error('The Pi is still starting. Check its status before continuing.');
}
async function action(fn){if(busy)return;busy=true;try{showNotice('');await fn();}catch(e){showNotice(e.message.replaceAll('_',' '));}finally{busy=false;await refresh();}}

function safeConversationLink(raw){
  if(typeof raw!=='string'||raw.length>2048||/[\u0000-\u0020\\]/.test(raw))return null;
  try{
    const u=new URL(raw),host=u.hostname.toLowerCase().replace(/\.$/,'');
    if(u.protocol!=='https:'||u.username||u.password||u.port||!host.includes('.')||host.includes(':')||/^[\d.]+$/.test(host))return null;
    if(!/^[a-z0-9-]+(?:\.[a-z0-9-]+)+$/.test(host)||host.split('.').some(p=>p.length>63||p.startsWith('-')||p.endsWith('-')))return null;
    if(['.local','.localhost','.internal','.test','.invalid','.example','.lan','.home','.home.arpa','.corp','.intranet'].some(s=>host.endsWith(s)))return null;
    return u.href;
  }catch(e){return null;}
}
function conversationSegments(text){
  const parts=[];let offset=0,count=0;
  for(const match of text.matchAll(/https:\/\/[^\s<>"'\x60]+/gi)){
    let raw=match[0].replace(/[.,!;:]+$/,'');
    for(const [open,close] of [['(',')'],['[',']'],['{','}']]){
      while(raw.endsWith(close)&&raw.split(close).length>raw.split(open).length)raw=raw.slice(0,-1);
    }
    const href=safeConversationLink(raw);if(!href||count>=64)continue;
    parts.push({text:text.slice(offset,match.index)});parts.push({text:raw,href});offset=match.index+raw.length;count++;
  }
  parts.push({text:text.slice(offset)});return parts;
}
function appendConversationText(bubble,text){
  for(const part of conversationSegments(text)){
    if(!part.href){bubble.appendChild(document.createTextNode(part.text));continue;}
    const a=document.createElement('a');a.className='source-link';a.href=part.href;a.textContent=part.text;
    a.target='_blank';a.rel='noopener noreferrer';a.referrerPolicy='no-referrer';bubble.appendChild(a);
  }
}

// Copy only the displayed conversation, on an explicit user click. No service call.
let copyingConversation=false;
function conversationCopyText(s){
  if(!s||!Array.isArray(s.messages))return '';
  const labels={preparing:'Preparing',interrupted:'Interrupted',failed:'Not completed'};
  const messages=s.messages.filter(m=>(m.role==='user'||m.role==='assistant')&&typeof m.text==='string');
  if(!messages.length)return '';
  return 'ME3 conversation\n\n'+messages.map(m=>{
    const state=m.role==='assistant'&&labels[m.state]?' ('+labels[m.state]+')':'';
    return (m.role==='user'?'You':'ME3')+state+':\n'+m.text;
  }).join('\n\n');
}
function manualConversationCopy(text){
  const dialog=document.createElement('dialog');dialog.setAttribute('aria-label','Copy conversation');
  const label=document.createElement('p');label.textContent='Select the text and copy it (Ctrl+C).';
  const field=document.createElement('textarea');field.readOnly=true;field.value=text;field.rows=14;
  field.setAttribute('aria-label','Conversation text');field.style.width='min(75vw, 640px)';
  const close=document.createElement('button');close.type='button';close.textContent='Close';
  close.onclick=()=>dialog.close();dialog.addEventListener('close',()=>dialog.remove(),{once:true});
  dialog.append(label,field,close);document.body.append(dialog);dialog.showModal();field.focus();field.select();
}
async function writeConversationCopy(text){
  try{if(navigator.clipboard?.writeText){await navigator.clipboard.writeText(text);return 'copied';}}catch(e){}
  const prior=document.activeElement,selection=prior&&typeof prior.selectionStart==='number'?[prior.selectionStart,prior.selectionEnd,prior.selectionDirection]:null;
  const field=document.createElement('textarea');field.readOnly=true;field.value=text;
  field.style.position='fixed';field.style.left='-10000px';field.setAttribute('aria-label','Copy conversation');
  document.body.append(field);let copied=false;
  try{field.focus();field.select();copied=document.execCommand('copy');}catch(e){}
  finally{field.remove();if(prior?.isConnected){prior.focus({preventScroll:true});if(selection)prior.setSelectionRange(...selection);}}
  if(copied)return 'copied';
  manualConversationCopy(text);return 'manual';
}
const copyConversationButton=document.createElement('button');
copyConversationButton.id='copy-conversation';copyConversationButton.type='button';copyConversationButton.className='quiet';
copyConversationButton.textContent='Copy conversation';copyConversationButton.title='Copy the displayed conversation';
copyConversationButton.setAttribute('aria-live','polite');copyConversationButton.disabled=true;
$('clear').before(copyConversationButton);
copyConversationButton.onclick=async()=>{
  if(copyingConversation)return;
  const text=conversationCopyText(current);if(!text)return;
  copyingConversation=true;copyConversationButton.disabled=true;
  try{const result=await writeConversationCopy(text);copyConversationButton.textContent=result==='copied'?'Copied':'Copy conversation';}
  catch(e){copyConversationButton.textContent='Copy unavailable';}
  finally{
    copyingConversation=false;copyConversationButton.disabled=!conversationCopyText(current);
    setTimeout(()=>{copyConversationButton.textContent='Copy conversation';},2000);
  }
};

const endCallButton=document.createElement('button');
endCallButton.id='end-call';endCallButton.type='button';endCallButton.className='quiet';
endCallButton.textContent='End call';endCallButton.hidden=true;$('clear').before(endCallButton);
endCallButton.onclick=()=>action(async()=>{
  if(!current?.call?.held||!current.call.can_end)return;
  endCallButton.disabled=true;
  const r=await fetch('/api/call/end',{method:'POST',headers:{'Content-Type':'application/json','X-ME3-CSRF':csrf},body:JSON.stringify({operation_id:current.call.operation_id})});
  const d=await r.json();if(!r.ok)throw new Error(d.error||'Call end is unverified. No retry.');
});
const checkCallButton=document.createElement('button');
checkCallButton.id='check-call-status';checkCallButton.type='button';checkCallButton.className='quiet';
checkCallButton.textContent='Check call status';checkCallButton.hidden=true;$('clear').before(checkCallButton);
checkCallButton.onclick=()=>action(async()=>{
  if(!current?.call?.held||!current.call.can_check)return;
  checkCallButton.disabled=true;
  const r=await fetch('/api/call/status',{method:'POST',headers:{'Content-Type':'application/json','X-ME3-CSRF':csrf},body:JSON.stringify({operation_id:current.call.operation_id})});
  const d=await r.json();if(!r.ok)throw new Error(d.error||'Call status is unverified. No action was repeated.');
});
function draw(s){
  if(disconnected){if(!s.error)showNotice('');disconnected=false;}
  current=s;copyConversationButton.disabled=copyingConversation||!conversationCopyText(s);$('connection-dot').classList.add('online');$('status').textContent=s.state==='stopped'&&s.mode!=='standby'?'Stopped':states[s.state]||'Working';
  $('stop').hidden=!s.active;$('clear').disabled=busy||s.active;
  $('voice').classList.toggle('active',s.active&&s.mode==='voice');
  $('voice').setAttribute('aria-label',s.active&&s.mode==='voice'?'Stop voice conversation':'Start voice conversation');
  $('voice').title=s.active&&s.mode==='voice'?'Stop voice conversation':'Start voice conversation';
  $('voice').disabled=busy||(!(s.active&&s.mode==='voice')&&!s.gates.normal.ready);
  $('message').disabled=false;$('send').disabled=busy||!$('message').value.trim()||!s.gates.text.ready;
  const callHeld=!!s.call?.held;endCallButton.hidden=!callHeld||!!s.call?.end_requested;endCallButton.disabled=busy||!s.call?.can_end;
  checkCallButton.hidden=!callHeld||!s.call?.end_requested;checkCallButton.disabled=busy||!s.call?.can_check;
  if(callHeld){
    $('stop').hidden=true;$('voice').disabled=true;$('clear').disabled=true;
    $('message').disabled=true;$('send').disabled=true;
    $('status').textContent=({HANDOFF_AUTHORIZED:'Preparing call',CLOSING_VOICE:'Preparing call',VOICE_CLOSED:'Preparing call',DIALING:'Calling',RINGING:'Ringing',CONNECTED:'In call',HELD_UNCERTAIN:'Call status uncertain'}[s.call.state]||'Call active');
    if(s.notice)showNotice(s.notice);
  }
  if(s.error){startupNotice=false;showNotice(s.error);}
  else if(startupNotice&&((s.active&&s.can_send)||(!s.active&&s.state==='stopped'))){startupNotice=false;showNotice('');}
  else if(!s.active&&/^(Reasoning|Speech recognition) service capacity is unavailable[.]/.test(s.gates.normal.reason||''))showNotice(blocked(s.gates.normal));
  const signature=JSON.stringify(s.messages);
  if(signature!==rendered){
    const pane=document.querySelector('.conversation'),follow=pane.scrollHeight-pane.scrollTop-pane.clientHeight<90;
    rendered=signature;$('messages').replaceChildren();$('empty').hidden=s.messages.length>0;
    for(const m of s.messages){const el=document.createElement('article');el.className='message '+(m.role==='user'?'user':'assistant');const who=document.createElement('p');who.className='who';who.textContent=m.role==='user'?'You':({'preparing':'ME3 - Preparing','interrupted':'ME3 - Interrupted','failed':'ME3 - Not completed'}[m.state]||'ME3');const bubble=document.createElement('div');bubble.className='bubble';appendConversationText(bubble,m.text);el.append(who,bubble);$('messages').append(el);}
    if(follow)pane.scrollTop=pane.scrollHeight;
  }
  const tasks=s.tasks||[];$('tasks').hidden=!tasks.length;$('task-list').replaceChildren();
  for(const task of tasks){const item=document.createElement('li');item.dataset.state=task.state;item.textContent=task.label+' - '+(taskStates[task.state]||'In progress');$('task-list').append(item);}
  const due=(s.reminders?.reminders||[]).filter(r=>r.state==='due');$('reminders').hidden=!due.length;$('reminders').replaceChildren();
  for(const r of due){const text=document.createElement('p');text.setAttribute('role','alert');text.textContent=r.label;const button=document.createElement('button');button.textContent='Dismiss';button.onclick=()=>action(()=>request('ack_reminder',{reminder_id:r.id}));text.append(button);$('reminders').append(text);}
  if(s.pending_display&&s.pending_display!==acknowledged&&!busy){acknowledged=s.pending_display;requestAnimationFrame(()=>request('ack',{id:s.pending_display}).catch(e=>{acknowledged=null;showNotice('The response could not be acknowledged.');}));}
  drawTaskRecovery(s);
  drawConfirmedArtifactPreview(s);
}
async function refresh(){if(refreshing)return;refreshing=true;try{const r=await fetch('/api/state',{cache:'no-store'});if(!r.ok)throw new Error();draw(await r.json());}catch(e){disconnected=true;$('status').textContent='Disconnected';$('connection-dot').classList.remove('online');$('voice').disabled=true;$('send').disabled=true;showNotice('Connection lost. The Pi may still be active. Reconnect to stop it.');}finally{refreshing=false;}}
$('voice').onclick=()=>action(async()=>{if(current?.active&&current.mode==='voice')await request('stop');else await switchMode('voice');});
$('stop').onclick=()=>action(()=>request('stop'));
$('clear').onclick=()=>action(async()=>{await request('clear',{expected_conversation_id:current?.conversation_id});$('message').value='';$('message').style.height='auto';});
$('composer').onsubmit=e=>{e.preventDefault();if(current?.call?.held)return;const text=$('message').value.trim();if(!text)return;action(async()=>{if(!(current?.active&&current.mode==='voice')){await switchMode('text');if(current.state==='processing'||current.pending_display)await request('cancel');}await request('send',{text});$('message').value='';$('message').style.height='auto';});};
$('message').oninput=()=>{$('message').style.height='auto';$('message').style.height=Math.min(160,$('message').scrollHeight)+'px';if(current)$('send').disabled=busy||!!current.call?.held||!$('message').value.trim()||!current.gates.text.ready;};
$('message').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('composer').requestSubmit();}};
refresh();setInterval(refresh,750);

// These read-only requests begin only after an owner clicks Artifacts/Refresh.
let artifactListing=null,artifactLoading=false;
const artifactPanel=$('artifact-panel'),artifactFrame=$('artifact-frame');
function artifactURL(route,params={}){return route+'?'+new URLSearchParams(params).toString();}
let runtimeView=null,runtimePollBusy=false,artifactActiveView=null;
function closeArtifactPreview(){runtimeView=null;artifactActiveView=null;artifactFrame.removeAttribute('srcdoc');artifactFrame.removeAttribute('src');artifactFrame.hidden=true;$('artifact-selected').textContent='Choose an HTML file to preview.';}
// Only a host-issued, separately confirmed current-context ID opens this frame.
const confirmedArtifactOpens=new Set();let artifactConversation=null;
function drawConfirmedArtifactPreview(s){
  drawPreviewClose(s);
  if(artifactConversation!==null&&artifactConversation!==s.conversation_id)closeArtifactPreview();
  artifactConversation=s.conversation_id;
  const e=s.artifact_preview_open;
  if(!e||e.conversation_id!==s.conversation_id||e.revision!==s.task_context?.revision||!(/^[a-f0-9]{32}$/.test(e.open_id))||typeof e.path!=='string'||confirmedArtifactOpens.has(e.open_id))return;
  confirmedArtifactOpens.add(e.open_id);if(confirmedArtifactOpens.size>64)confirmedArtifactOpens.delete(confirmedArtifactOpens.values().next().value);
  closeArtifactPreview();if(!artifactPanel.open)artifactPanel.show();
  $('artifact-selected').textContent=e.path;
  $('artifact-notice').textContent='Opening your confirmed sandboxed preview. Backend services and external resources are unavailable here.';
  if(e.kind==='runtime'){openRuntimeView(e);return;}
  artifactActiveView={view_id:e.open_id,conversation_id:e.conversation_id};
  artifactFrame.src=artifactURL('/api/artifacts/confirmed-preview',{open_id:e.open_id});artifactFrame.hidden=false;
}
async function loadArtifacts(){
  if(artifactLoading)return;artifactLoading=true;$('artifact-refresh').disabled=true;$('artifact-zip').disabled=true;
  closeArtifactPreview();artifactListing=null;$('artifact-list').replaceChildren();$('artifact-notice').textContent='Reading your Pi artifact workspace…';
  try{
    const r=await fetch('/api/artifacts',{cache:'no-store',credentials:'same-origin'}),d=await r.json();
    if(!r.ok)throw new Error(d.error||'Artifacts are unavailable.');
    if(!Array.isArray(d.files)||!/^[a-f0-9]{64}$/.test(d.workspace_sha256))throw new Error('Invalid artifact list.');
    artifactListing=d;
    const projects=[...new Set(d.files.filter(file=>typeof file.path==='string'&&file.path.includes('/')).map(file=>file.path.split('/')[0]))].sort();
    for(const directory of projects){
      const row=document.createElement('li'),label=document.createElement('p'),download=document.createElement('a');
      label.textContent=directory+'/';download.textContent='Download project ZIP';
      download.href=artifactURL('/api/artifacts/project.zip',{workspace_sha256:d.workspace_sha256,directory});download.setAttribute('download','');
      row.append(label,download);$('artifact-list').append(row);
    }
    for(const file of d.files){
      if(typeof file.path!=='string'||!/^[a-f0-9]{64}$/.test(file.sha256))throw new Error('Invalid artifact entry.');
      const row=document.createElement('li'),label=document.createElement('p'),meta=document.createElement('small'),download=document.createElement('a');
      label.textContent=file.path;meta.textContent=file.bytes+' bytes';
      download.textContent='Download';download.href=artifactURL('/api/artifacts/download',{path:file.path,sha256:file.sha256});download.setAttribute('download','');
      row.append(label,meta,download);
      if(file.previewable){
        const preview=document.createElement('button');preview.type='button';preview.textContent='Preview';
        preview.onclick=()=>{
          closeArtifactPreview();$('artifact-selected').textContent=file.path;
          artifactFrame.src=artifactURL('/api/artifacts/preview',{path:file.path,workspace_sha256:d.workspace_sha256});artifactFrame.hidden=false;
        };row.append(preview);
      }
      $('artifact-list').append(row);
    }
    $('artifact-notice').textContent=d.files.length?d.files.length+' files. Downloads and previews use this exact workspace version.':'No files yet. Ask ME3 to create something in your artifact workspace.';
    $('artifact-zip').disabled=!d.files.length;
  }catch(e){artifactListing=null;closeArtifactPreview();$('artifact-list').replaceChildren();$('artifact-notice').textContent='Files could not be read, or changed. Refresh files to try again.';}
  finally{artifactLoading=false;$('artifact-refresh').disabled=false;}
}
$('artifacts').onclick=()=>{if(!artifactPanel.open)artifactPanel.show();return loadArtifacts();};
$('artifact-close').onclick=()=>{closeArtifactPreview();artifactPanel.close();};
artifactPanel.addEventListener('cancel',closeArtifactPreview);
$('artifact-refresh').onclick=loadArtifacts;
$('artifact-zip').onclick=()=>{
  if(!artifactListing)return;
  const link=document.createElement('a');link.href=artifactURL('/api/artifacts/project.zip',{workspace_sha256:artifactListing.workspace_sha256});link.setAttribute('download','');link.click();
};

// Saved task controls use the normal authenticated command path. Nothing is
// loaded or resumed automatically, and displayed text never becomes markup.
const savedTaskButton=document.createElement('button');
savedTaskButton.id='saved-tasks';savedTaskButton.type='button';savedTaskButton.className='quiet';
savedTaskButton.textContent='Saved tasks';savedTaskButton.hidden=true;$('clear').before(savedTaskButton);
const savedTaskPanel=document.createElement('section');savedTaskPanel.id='saved-task-panel';
savedTaskPanel.className='tasks';savedTaskPanel.hidden=true;savedTaskPanel.setAttribute('aria-label','Saved tasks');
function taskElement(tag,text){const e=document.createElement(tag);if(text)e.textContent=text;return e;}
function taskButton(text,handler){const b=taskElement('button',text);b.type='button';b.className='quiet';b.onclick=handler;return b;}
const savedTaskHeading=taskElement('h2','Saved tasks');
const savedTaskHelp=taskElement('p','Save the next request when you want to work on a task across turns. A finished reply does not mark your task done. Interrupting a running task pauses tracking. Choose the saved task here to continue.');
const trackNextButton=taskButton('Track next request',()=>action(async()=>{
  await taskSession();await request('track_next_task',{expected_conversation_id:current.conversation_id});
  showNotice('The next request will be saved as a task. Speak normally or type it.');
}));
const stopTrackingButton=taskButton('Cancel tracking',()=>action(async()=>{
  await request('cancel_task_tracking',{expected_conversation_id:current.conversation_id});
  savedTaskSelection=null;taskContinue.disabled=true;showNotice('Selection or next-request tracking cleared. Saved work is unchanged.');
}));
const taskRefreshButton=taskButton('Refresh saved tasks',()=>action(()=>loadSavedTasks(0)));
const taskCloseButton=taskButton('Close',()=>{savedTaskPanel.hidden=true;});
const savedTaskStatus=taskElement('p','');savedTaskStatus.setAttribute('role','status');
const savedTaskRows=taskElement('ol','');savedTaskRows.id='saved-task-list';
const taskMoreButton=taskButton('More tasks',()=>action(()=>loadSavedTasks(savedTaskNext)));taskMoreButton.hidden=true;
const taskReference=taskElement('section','');taskReference.hidden=true;
const taskReferenceTitle=taskElement('p',''),taskReferenceState=taskElement('p',''),taskVersions=taskElement('ul','');
const taskRequestLabel=taskElement('label','What would you like ME3 to continue or change?');taskRequestLabel.htmlFor='task-continue-request';
const taskRequest=taskElement('textarea','');taskRequest.id='task-continue-request';taskRequest.rows=2;taskRequest.maxLength=4000;
const taskContinue=taskButton('Prepare continuation',()=>action(async()=>{
  const selected=savedTaskSelection,text=taskRequest.value.trim();if(!selected||!text)return;
  if(selected.conversation_id!==current.conversation_id)throw new Error('The conversation changed. Choose the task again.');
  await request('select_task',{expected_conversation_id:selected.conversation_id,goal_id:selected.goal_id,checkpoint_sha256:selected.checkpoint_sha256,owner_request:text});
  $('message').value=text;$('message').readOnly=true;taskContinue.disabled=true;
  showNotice('Task selected. Press Send to continue with the request in the message box. Use Discard selection to change it first.');
}));taskContinue.disabled=true;
taskRequest.oninput=()=>{taskContinue.disabled=busy||!savedTaskSelection||!taskRequest.value.trim()||!!current?.task_recovery?.selection_pending;};
taskReference.append(taskReferenceTitle,taskReferenceState,taskVersions,taskRequestLabel,taskRequest,taskContinue);
savedTaskPanel.append(savedTaskHeading,savedTaskHelp,trackNextButton,stopTrackingButton,taskRefreshButton,taskCloseButton,savedTaskStatus,savedTaskRows,taskMoreButton,taskReference);
$('composer').before(savedTaskPanel);
let savedTaskSelection=null,savedTaskNext=null;
const savedTaskStates={running:'Working',reply_ready:'Needs reconciliation',awaiting_owner:'Waiting for your next step',waiting_confirmation:'Review still required',interrupted:'Interrupted',failed:'Stopped with a failure',canceled:'Canceled',completed:'Marked done by you'};
async function taskSession(){if(!current?.active)await switchMode('text');}
async function showSavedTask(row){
  const result=await request('task_reference',{expected_conversation_id:current.conversation_id,goal_id:row.goal_id});
  if(!result.reference)throw new Error('This saved task is no longer available.');
  const ref=result.reference,checkpoint=ref.checkpoint;
  savedTaskSelection={goal_id:checkpoint.goal_id,checkpoint_sha256:ref.checkpoint_sha256,conversation_id:current.conversation_id};
  taskReferenceTitle.textContent=checkpoint.request;
  taskReferenceState.textContent=savedTaskStates[checkpoint.state]||'Needs review';taskVersions.replaceChildren();
  const artifacts=result.artifact_reconciliation?.artifacts||[];
  if(!artifacts.length)taskVersions.append(taskElement('li','No files are linked to this task. Its saved description does not prove the work finished.'));
  for(const file of artifacts)taskVersions.append(taskElement('li',file.path+' — saved version matches the current file.'));
  const resumable=['interrupted','failed','awaiting_owner'].includes(checkpoint.state);
  taskReference.hidden=false;taskRequest.value='';taskRequest.hidden=!resumable;taskRequestLabel.hidden=!resumable;
  taskContinue.hidden=!resumable;taskContinue.disabled=true;
}
async function loadSavedTasks(offset=0){
  await taskSession();
  const result=await request('task_list',{expected_conversation_id:current.conversation_id,offset});
  savedTaskRows.replaceChildren();savedTaskNext=result.next_offset;taskMoreButton.hidden=savedTaskNext===null;
  savedTaskStatus.textContent=result.total?'Choose a task to inspect its saved reference and current artifact versions.':'No tasks have been saved yet.';
  for(const row of result.tasks){
    const item=taskElement('li',''),title=taskElement('p',row.title),state=taskElement('p',savedTaskStates[row.state]||'Needs review');
    const review=taskButton('Review task',()=>action(()=>showSavedTask(row)));item.append(title,state,review);
    if(row.state==='awaiting_owner')item.append(taskButton('Mark done',()=>action(async()=>{
      await request('finish_saved_task',{expected_conversation_id:current.conversation_id,goal_id:row.goal_id,checkpoint_sha256:row.checkpoint_sha256,cancel:false});
      await loadSavedTasks(0);showNotice('Marked done by you. Your saved files stay unchanged.');
    })));
    if(['awaiting_owner','interrupted','failed','waiting_confirmation'].includes(row.state))item.append(taskButton('Cancel task',()=>action(async()=>{
      await request('finish_saved_task',{expected_conversation_id:current.conversation_id,goal_id:row.goal_id,checkpoint_sha256:row.checkpoint_sha256,cancel:true});
      await loadSavedTasks(0);showNotice('Task canceled. Previously saved files and completed actions remain unchanged.');
    })));
    savedTaskRows.append(item);
  }
}
savedTaskButton.onclick=()=>action(async()=>{savedTaskPanel.hidden=false;await loadSavedTasks(0);});
function drawTaskRecovery(s){
  const enabled=s.task_recovery?.enabled===true;savedTaskButton.hidden=!enabled;
  if(!enabled){savedTaskPanel.hidden=true;return;}
  const pending=s.task_recovery.selection_pending===true,armed=s.task_recovery.armed_next===true;
  $('message').readOnly=pending;
  trackNextButton.disabled=busy||(!s.task_recovery.can_track&&s.active)||!!s.call?.held;
  stopTrackingButton.hidden=!pending&&!armed;stopTrackingButton.textContent=pending?'Discard selection':'Cancel tracking';
  stopTrackingButton.disabled=busy||!!s.call?.held;
  if(savedTaskSelection&&savedTaskSelection.conversation_id!==s.conversation_id){savedTaskSelection=null;taskReference.hidden=true;taskContinue.disabled=true;}
  if(armed)savedTaskStatus.textContent='Tracking is armed for your next voice or typed request. You can cancel it before speaking.';
  taskContinue.disabled=busy||pending||!savedTaskSelection||!taskRequest.value.trim();
}

// The opaque frame cannot address GUI APIs. Parent relays only its current lease.
async function runtimeAPI(data){const r=await fetch('/api/artifacts/runtime-view',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-ME3-CSRF':csrf},body:JSON.stringify(data)});const d=await r.json();if(!r.ok)throw new Error('The app view is unavailable; no request was retried.');return d;}
async function openRuntimeView(e){try{const d=await runtimeAPI({operation:'open',open_id:e.open_id});if(current?.conversation_id!==d.conversation_id)return;runtimeView={view_id:d.view_id,nonce:d.nonce,conversation_id:d.conversation_id,inflight:false,seen:new Set()};artifactActiveView={view_id:d.view_id,conversation_id:d.conversation_id};artifactFrame.src=artifactURL('/api/artifacts/runtime-document',{view_id:d.view_id});artifactFrame.hidden=false;$('artifact-notice').textContent='Your running app. Keep chatting; ask ME3 to stop it when finished. Navigation and WebSockets are unavailable.';}catch(err){closeArtifactPreview();$('artifact-notice').textContent=err.message;}}
window.addEventListener('message',async e=>{const v=runtimeView,d=e.data;if(!v||e.source!==artifactFrame.contentWindow||e.origin!=='null'||d?.kind!=='me3-app-request'||d.nonce!==v.nonce||!Number.isSafeInteger(d.id)||d.id<1)return;const reply=value=>{if(runtimeView===v)e.source.postMessage({kind:'me3-app-response',nonce:v.nonce,id:d.id,...value},'*');};if(v.seen.has(d.id)||v.seen.size>=128){reply({ok:false});return;}v.seen.add(d.id);if(v.inflight){reply({ok:false});return;}v.inflight=true;try{const result=await runtimeAPI({operation:'relay',view_id:v.view_id,request_id:d.id,request:d.request});reply(result);}catch(err){reply({ok:false});closeArtifactPreview();$('artifact-notice').textContent=err.message;}finally{v.inflight=false;}});
setInterval(async()=>{const v=runtimeView;if(!v||runtimePollBusy)return;runtimePollBusy=true;try{const d=await runtimeAPI({operation:'status',view_id:v.view_id});if(runtimeView===v&&!d.open){closeArtifactPreview();if(d.ok===true&&d.open===false&&d.backend_closed_verified===true&&artifactPanel.open){artifactPanel.close();$('voice').focus();}$('artifact-notice').textContent=d.notice;}}catch(err){if(runtimeView===v){closeArtifactPreview();$('artifact-notice').textContent='The view was closed because its current backend status could not be verified.';}}finally{runtimePollBusy=false;}},3000);

// A close notification has no backend authority. Acknowledge only the exact removed frame.
const acknowledgedPreviewCloses=new Set();
function drawPreviewClose(s){const e=s.artifact_preview_close,v=artifactActiveView;if(!e||!v||e.conversation_id!==s.conversation_id||v.conversation_id!==s.conversation_id||e.view_id!==v.view_id||!(/^[a-f0-9]{32}$/.test(e.close_id))||acknowledgedPreviewCloses.has(e.close_id))return;acknowledgedPreviewCloses.add(e.close_id);if(acknowledgedPreviewCloses.size>64)acknowledgedPreviewCloses.delete(acknowledgedPreviewCloses.values().next().value);closeArtifactPreview();if(artifactPanel.open)artifactPanel.close();$('voice').focus();$('artifact-notice').textContent='The preview frame is closed. Backend services were left unchanged.';request('preview_close_ack',{close_id:e.close_id,view_id:e.view_id,conversation_id:e.conversation_id}).catch(()=>{$('artifact-notice').textContent='The frame is closed here, but ME3 could not confirm that acknowledgement. Backend services were left unchanged.';});}
