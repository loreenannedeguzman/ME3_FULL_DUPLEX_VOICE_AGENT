"""Arm capture after the greeting and optional counted planning-cue preparation."""
def gate_startup(processor,events):
    processor.candidate_listening=False
    original_speak=processor.controller.speak
    async def speak(*args,**kwargs):
        played=await original_speak(*args,**kwargs)
        if kwargs.get('greeting') and played and not processor.closing:
            prepare=getattr(processor,'prepare_planning_ack',None)
            if callable(prepare):await prepare()
            if processor.closing or processor.controller.clock.current!=0:return played
            processor.candidate_listening=True
            events.emit('onit_voice_listening',generation=processor.controller.clock.current)
        return played
    processor.controller.speak=speak
    original=processor.process_frame
    async def process(frame,direction):
        from pipecat.frames.frames import InputAudioRawFrame
        if isinstance(frame,InputAudioRawFrame) and not processor.candidate_listening:return
        await original(frame,direction)
    processor.process_frame=process
