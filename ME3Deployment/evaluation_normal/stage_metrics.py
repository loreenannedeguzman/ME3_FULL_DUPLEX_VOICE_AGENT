"""First response PCM timing, excluding the lookahead synthesis and listener cues."""
def ns(e):return e.get('observed_ns',e.get('monotonic_ns'))

def first_playable(events,generation):
    own=sorted((e for e in events if e.get('generation')==generation),key=ns)
    reason=next((e for e in own if e['event']=='reasoning_end'),None)
    missing={'status':'NOT_MEASURED','physical_acoustic_onset':'NOT_MEASURED','LLM_TTFT':'NOT_MEASURED'}
    if reason is None:return dict(missing,reason='No completed reasoning boundary for substantive response.')
    ready=next((e for e in own if e['event']=='response_audio_ready' and not e.get('greeting') and not e.get('error_notice') and ns(e)>=ns(reason)),None)
    start=next((e for e in own if e['event']=='mcp_request_start' and e.get('role')=='tts' and ns(e)>=ns(reason)),None)
    if ready is None or start is None:return dict(missing,reason='First substantive PCM marker or TTS dispatch absent.')
    end=next((e for e in own if e['event']=='mcp_request_end' and e.get('role')=='tts' and e.get('request_id')==start.get('request_id') and ns(start)<=ns(e)<=ns(ready)),None)
    if end is None:return dict(missing,reason='First response TTS request has no identity-matched completion before first PCM-ready marker.')
    return {'status':'MEASURED_SOFTWARE_PROXY','request_id':start['request_id'],'first_response_TTS_request_seconds':(ns(end)-ns(start))/1e9,'TTS_dispatch_to_first_application_PCM_ready_seconds':(ns(ready)-ns(start))/1e9,'selection':'First TTS dispatch after completed reasoning; identity-matched completion and first substantive PCM-ready marker. Prefetched later part and prior listener cues excluded.','physical_acoustic_onset':'NOT_MEASURED','LLM_TTFT':'NOT_MEASURED'}
