from me3_completion.integration import apply as completion_apply
from me3_agent.reasoning import AgentReasoning
from me3_agent.router import AgentRouter
from me3_agent.client import AgentTasks
from me3_agent.controller import AgentController
from me3_agent.speech import AgentSpeechClients
def apply(events):
    import me3_completion.pipeline as pipeline
    pipeline.ConversationController=AgentController
    app=completion_apply(events)
    original=app.m3.build_pipeline
    def build(*args,**kwargs):
        result=original(*args,**kwargs)
        from me3_agent.overlap import attach
        attach(result[3],args[2],args[3],events)
        from me3_agent.recovery import attach as attach_recovery
        attach_recovery(result[3],events)
        return result
    app.m3.build_pipeline=build
    class Reasoning(AgentReasoning):
        def __init__(self):super().__init__(events)
    app.GoogleSpeech=Reasoning;app.TaskReasoner=AgentRouter;app.TaskClient=AgentTasks;app.SpeechClients=AgentSpeechClients
    return app
