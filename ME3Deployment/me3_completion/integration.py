"""Process-local selection; original core and milestone launches are not edited."""
from me3_completion.pipeline import build_pipeline
from me3_completion.client import ConversationClients
from me3_completion.task import ConversationReasoner
from me3_core.reasoning import CoreReasoning
def apply(events):
    import m4_voice_agent as app
    class Reasoning(CoreReasoning):
        def __init__(self):super().__init__(events)
    def build(*args,**kwargs):
        result=build_pipeline(*args,**kwargs);events.processor=result[3];return result
    app.GoogleSpeech=Reasoning;app.SpeechClients=ConversationClients;app.TaskReasoner=ConversationReasoner;app.m3.build_pipeline=build
    return app
