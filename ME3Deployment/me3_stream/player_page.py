"""Own visible SoundCloud widget shell; no account, private endpoint or stream URL."""
HTML='''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ME3 SoundCloud</title><link rel="stylesheet" href="/style.css"></head><body><h1>ME3 · SoundCloud</h1><p id="status">Waiting for a music request</p><div id="controls"><button id="play" disabled>Play</button><button id="pause" disabled>Pause</button><button id="resume" disabled>Resume</button><button id="stop" disabled>Stop</button><button id="volume" disabled>Set volume</button></div><div id="player"></div><script src="/player.js"></script></body></html>'''
CSS='''html{color-scheme:dark;background:#111923;color:#e4f0f7;font-family:system-ui}body{margin:10px}h1{font-size:18px;margin:6px 0}button{padding:6px 10px;margin:0 4px 6px 0;font-size:14px}iframe{display:block;width:100%;max-width:900px;height:166px;border:0}#status{min-height:18px;margin:6px 0}'''
JS=r'''"use strict";(()=>{
 let widget=null,ready=false,error=null,last=null,pending=null,api=null;
 const status=document.getElementById('status'),commands=['play','pause','resume','stop','volume'];
 const legal=u=>/^https:\/\/soundcloud\.com\/[A-Za-z0-9_-]{1,128}\/[A-Za-z0-9_-]{1,128}$/.test(u);
 const visible=()=>document.visibilityState==='visible';
 function enable(){for(const id of commands)document.getElementById(id).disabled=!ready||!!error;}
 function fail(code){error=code;try{if(widget)widget.pause();}catch{}status.textContent='Playback unavailable';enable();}
 function loadAPI(){if(api)return api;api=new Promise((resolve,reject)=>{const script=document.createElement('script');script.src='https://w.soundcloud.com/player/api.js';script.onload=resolve;script.onerror=()=>reject(new Error('network'));document.head.appendChild(script);});return api;}
 function getter(name){return new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('getter_timeout')),2500);widget[name](value=>{clearTimeout(timer);resolve(value);});});}
 async function cue(url){
  if(!legal(url))throw new Error('invalid_track');if(!visible())throw new Error('hidden');
  ready=false;error=null;pending=null;enable();status.textContent='Preparing SoundCloud';await loadAPI();
  const target=document.getElementById('player');target.replaceChildren();const frame=document.createElement('iframe');frame.title='SoundCloud player';frame.allow='autoplay';frame.src='https://w.soundcloud.com/player/?url='+encodeURIComponent(url)+'&auto_play=false&show_user=true';target.appendChild(frame);widget=SC.Widget(frame);
  widget.bind(SC.Widget.Events.READY,()=>{ready=true;status.textContent='Player ready';enable();});
  widget.bind(SC.Widget.Events.ERROR,()=>fail('player_error'));
 }
 function stage(intent,operation){
  if(!/^[0-9a-f]{32}$/.test(operation)||!commands.includes(intent.command))throw new Error('invalid_operation');
  if(!ready||error||!visible())throw new Error('player_not_ready');
  if(['play','volume'].includes(intent.command)&&(!Number.isInteger(intent.volume)||intent.volume<0||intent.volume>100))throw new Error('invalid_volume');
  pending={...intent,operation};
 }
 for(const id of commands)document.getElementById(id).onclick=()=>{
  const p=pending;if(!p||p.command!==id||!ready||error||!visible())return;
  pending=null;last=p.operation;
  try{if(id==='play'){widget.setVolume(p.volume);widget.play();}else if(id==='resume')widget.play();else if(id==='pause')widget.pause();else if(id==='stop'){widget.pause();widget.seekTo(0);}else widget.setVolume(p.volume);status.textContent='Player command sent';}catch{fail('player_error');}
 };
 async function inspect(){
  if(!widget)return {ready:true,track_url:null,paused:true,position_ms:0,duration_ms:0,volume:40,visible:visible(),error:null,last_operation_id:last};
  if(!ready)return {ready:false,track_url:null,paused:true,position_ms:0,duration_ms:0,volume:40,visible:visible(),error,last_operation_id:last};
  const [sound,paused,position,duration,volume]=await Promise.all(['getCurrentSound','isPaused','getPosition','getDuration','getVolume'].map(getter));
  return {ready,track_url:sound&&sound.permalink_url||null,paused,position_ms:position,duration_ms:Number.isFinite(duration)?duration:0,volume:Math.round(volume),visible:visible(),error,last_operation_id:last};
 }
 document.addEventListener('visibilitychange',()=>{if(!visible()&&widget){try{widget.pause();}catch{}pending=null;}});
 window.addEventListener('pagehide',()=>{try{if(widget)widget.pause();}catch{}pending=null;});
 window.me3Media={cue,stage,inspect,clear:()=>{pending=null;}};
})();'''
