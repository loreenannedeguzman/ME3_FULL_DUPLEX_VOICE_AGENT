"""Normal voice startup and output-capacity admission; never extends budgets."""
import asyncio,re
from speech_mcp.client import ProviderLimited

def require_session_open(stop_requested):
    if stop_requested is not None and stop_requested.is_set():
        raise asyncio.CancelledError()

def reserve_open_session(budget,role,stop_requested):
    require_session_open(stop_requested)
    return reserve_voice_reasoning(budget) if role=="reasoning" else budget.reserve(role)

def reserve_voice_reasoning(budget):
    """Recognized input may be displayed; do not buy an unplayable answer."""
    capacity=budget.remaining('tts')
    if type(capacity) is not int or capacity<0:raise ValueError('invalid_voice_output_allowance')
    if capacity==0:
        budget.exhausted.set()
        raise ProviderLimited('local_speech_allowance_exhausted_before_reasoning')
    return budget.reserve('reasoning')

def install_normal_startup(processor,events):
    """Normal greeting only; automatic planning cues and cache stay retired.

    No automatic acknowledgment is synthesized or played while planning.
    Explicit recognized turn-hold cues and listener responses remain unchanged.
    Shared startup is unchanged.
    """
    # An unrecognized pause is not an invitation for speculative TTS. Canceling
    # that optional synthesis retires the resident worker before the real reply.
    # Explicit recognized turn-hold cues and listener acknowledgments remain.
    processor.allow_speculative_pause_cues=False
    async def startup():
        events.emit('assistant_startup_synthesis_policy',unused_cue_priming=False,
                    greeting_uses_actual_tts=True,on_demand_acknowledgments_unchanged=True,
                    speculative_pause_cues=False,planning_ack_cache_priming=False,
                    automatic_planning_acknowledgments=False)
        if not processor.closing and processor.controller.clock.current==0:
            processor.controller.greet()
    def greet():
        return processor.controller.spawn(processor.controller.speak(
            "Hi there! What's on your mind today?",0,'normal',greeting=True))
    processor.controller.greet=greet
    processor.startup=startup


def voice_session_end_kind(text):
    """Reserved direct-user session control, never inferred from tool/model text.

    Targeted task requests such as stop music/talking are not session shutdown.
    Negation, quoted instructions to repeat words and conditional discussion do
    not match this anchored grammar. Ordinary goals still use dynamic reasoning.
    """
    if not isinstance(text,str) or len(text)>256:return None
    surface=' '.join(re.findall(r"[a-z]+(?:'[a-z]+)?",text.casefold().replace('’',"'")))
    thanks=r'(?:(?:thank you(?: for (?:the information|your help|the help))?|thanks) )?'
    polite=r'(?:(?:can|could|would) you )?(?:please )?'
    together=r"(?:(?:let's|lets|let us) )?"
    target=r'(?:(?:the|this|our) )?(?:conversation|session)'
    farewell=r'(?:good ?bye|bye(?: bye)?)(?: for now)?'
    command=r'(?:(?P<farewell>'+farewell+r')|stop(?: listening(?: to me)?| '+target+r')?|(?:end|finish) '+target+r')'
    match=re.fullmatch(thanks+polite+together+command+r'(?: now)?(?: please)?',surface)
    return ('farewell' if match.group('farewell') is not None else 'immediate') if match else None

def ends_voice_session(text):
    return voice_session_end_kind(text) is not None


def bind_voice_session_stop(router,stop_requested,events):
    """Bind to the supervised owner's existing event; never cancel its task."""
    def request(generation):
        if not router.current(generation):return False
        stop_requested.set()
        events.emit('assistant_voice_stop_requested',generation=generation,
                    reason='direct_user_session_end',owner_shutdown_requested=True,
                    external_actions_undone=False,reasoning_requests=0,TTS_requests=0)
        return True
    router.request_voice_session_stop=request
