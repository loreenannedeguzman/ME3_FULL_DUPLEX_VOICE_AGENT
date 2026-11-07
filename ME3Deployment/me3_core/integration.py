"""Opt-in process-local extension; accepted M1-M7 files remain unchanged."""
import json
from pathlib import Path
from me3_core.client import CoreSpeechClients
from me3_core.controller import CoreTurnController
ROOT=Path(__file__).resolve().parents[1]
def apply(events=None):
    import m4_voice_agent as app
    config=json.loads((ROOT/'config/core_agent.json').read_text(encoding='utf-8'))
    if config['empty_transcript_prompt'] is not True:raise ValueError('core_empty_prompt_required')
    class Controller(CoreTurnController):
        def __init__(self,*args,**kwargs):super().__init__(*args,turn_deadline_seconds=config['turn_deadline_seconds'],**kwargs)
    if events is not None:
        from me3_core.reasoning import CoreReasoning
        class Reasoning(CoreReasoning):
            def __init__(self):super().__init__(events)
        app.GoogleSpeech=Reasoning
    if events is not None:
        from me3_core.events import attach
        original=getattr(app.m3.build_pipeline,'_core_original',app.m3.build_pipeline)
        def build(*args,**kwargs):
            runner,worker,transport,processor=original(*args,**kwargs);attach(processor,transport,events);return runner,worker,transport,processor
        build._core_original=original;app.m3.build_pipeline=build
    from me3_core.task import CoreTaskReasoner
    app.TaskReasoner=CoreTaskReasoner
    app.SpeechClients=CoreSpeechClients;app.m3.TurnController=Controller
    return app
