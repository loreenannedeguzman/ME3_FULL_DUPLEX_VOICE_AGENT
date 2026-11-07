'use strict';
(() => {
  const panel=document.getElementById('youtube'),note=document.getElementById('youtube-status');
  const csrf=document.querySelector('meta[name="me3-csrf"]').content;
  const client=crypto.randomUUID().replaceAll('-','');
  let player=null,loading=null,running=false,lastCommand=null,error=null,ready=false,stopped=false,voiceLocked=false;
  async function api(action,data={}) {
    const r=await fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/json','X-ME3-CSRF':csrf},body:JSON.stringify({action,...data})});
    const result=await r.json();if(!r.ok)throw new Error(result.error||'YouTube control unavailable');return result;
  }
  function visible(){return !document.hidden&&!panel.hidden;}
  function snapshot(id=null){
    let state='unavailable',video=null,volume=0;
    if(ready){const states={'-1':'unstarted','0':'ended','1':'playing','2':'paused','3':'buffering','5':'cued'};state=stopped?'stopped':(states[player.getPlayerState()]||'unavailable');video=player.getVideoData()?.video_id||null;volume=Math.round(player.getVolume());}
    return {command_id:id,state,video_id:video,volume,visible:visible(),error};
  }
  function load(){
    if(loading)return loading;
    loading=new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error('YouTube player could not connect')),12000);
      window.onYouTubeIframeAPIReady=()=>{
        player=new YT.Player('youtube-player',{width:'100%',height:270,playerVars:{autoplay:0,controls:1,playsinline:1,origin:location.origin},events:{
          onReady:()=>{ready=true;clearTimeout(timer);resolve(player);},
          onError:e=>{error=[100,101,150].includes(e.data)?'unsupported_video':'player_error';note.textContent='This video cannot be played here. Choose another YouTube link.';},
          onAutoplayBlocked:()=>{error='autoplay_blocked';note.textContent='Tap Play in the YouTube player to begin.';},
          onStateChange:e=>{if(e.data===1){stopped=false;error=null;if(!visible()||voiceLocked){player.pauseVideo();error=voiceLocked?'voice_audio_unqualified':'hidden';}}}
        }});
      };
      const script=document.createElement('script');script.src='https://www.youtube.com/iframe_api';script.onerror=()=>{clearTimeout(timer);reject(new Error('YouTube could not connect'));};document.head.append(script);
    });return loading;
  }
  function safePause(reason){if(ready&&player.getPlayerState()===1)player.pauseVideo();if(reason)error=reason;}
  document.getElementById('youtube-toggle').onclick=async()=>{
    if(voiceLocked){note.textContent='Stop voice to use YouTube playback.';return;}
    panel.hidden=!panel.hidden;
    if(panel.hidden){safePause('hidden');return;}
    error=null;try{await load();note.textContent='Open a YouTube link, or ask ME3 in text.';}catch(e){note.textContent=e.message;}
  };
  document.getElementById('youtube-open').onclick=async()=>{
    try{
      const {video_id}=await api('youtube_validate',{url:document.getElementById('youtube-url').value.trim()});
      const state=await api('youtube_poll',{client});if(state.voice_active)throw new Error('Stop voice before opening YouTube playback.');
      panel.hidden=false;await load();error=null;stopped=false;player.loadVideoById(video_id);
    }catch(e){note.textContent=e.message;}
  };
  document.addEventListener('visibilitychange',()=>{if(document.hidden)safePause('hidden');});
  window.addEventListener('pagehide',()=>safePause('hidden'));
  async function command(c){
    if(!c||lastCommand===c.id)return;
    lastCommand=c.id;
    try{
      if(c.command!=='stop'&&c.command!=='inspect')panel.hidden=false;
      if(c.command==='inspect'&&!ready){await api('youtube_report',{client,packet:snapshot(c.id)});return;}
      if(c.command==='stop'&&!ready){stopped=true;error=null;await api('youtube_report',{client,packet:{...snapshot(c.id),state:'stopped'}});return;}
      await load();if(!visible()&&c.command!=='stop')throw new Error('hidden');
      error=null;
      if(c.command==='play'){stopped=false;player.setVolume(c.volume);player.loadVideoById(c.video_id);}
      else if(c.command==='pause')player.pauseVideo();
      else if(c.command==='resume'){stopped=false;player.playVideo();}
      else if(c.command==='stop'){player.stopVideo();stopped=true;}
      else if(c.command==='volume')player.setVolume(c.volume);
      const expected={play:['playing'],pause:['paused','ended'],resume:['playing'],stop:['stopped'],volume:['playing','paused','ended','cued'],inspect:null}[c.command];
      for(let i=0;i<32;i++){
        await new Promise(r=>setTimeout(r,150));const s=snapshot(c.id);
        if(error||!expected||expected.includes(s.state)&&(!('volume'in c)||s.volume===c.volume)&&(!c.video_id||s.video_id===c.video_id)){await api('youtube_report',{client,packet:s});return;}
      }
      error='player_unavailable';await api('youtube_report',{client,packet:snapshot(c.id)});
    }catch(e){error=e.message==='hidden'?'hidden':'player_unavailable';await api('youtube_report',{client,packet:snapshot(c.id)}).catch(()=>{});}
  }
  async function poll(){
    if(running)return;running=true;
    try{
      const result=await api('youtube_poll',{client});
      voiceLocked=result.voice_active;if(voiceLocked){safePause('voice_audio_unqualified');}
      await api('youtube_report',{client,packet:snapshot()});
      if(result.command)await command(result.command);
    }catch(e){safePause(null);note.textContent='YouTube control connection lost; playback paused.';}
    finally{running=false;}
  }
  poll();setInterval(poll,500);
})();
