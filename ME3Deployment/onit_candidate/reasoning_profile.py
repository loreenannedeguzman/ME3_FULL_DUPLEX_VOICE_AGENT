"""One explicitly selected reasoner, independent of the unchanged ASR provider."""
from pathlib import Path
ACTIVE='openai_chatgpt_plan'  # Owner-authorized, explicit110qualified replacement; no fallback.
GROQ_MODEL='qwen/qwen3.8-27b'
GEMINI_MODEL='gemini-3.1-flash-lite'
OPENAI_MODEL='gpt-6.1-sol'  # Only the explicitly qualified saved account model.
OPENAI_PROVIDER='openai_chatgpt_plan'
def profile(root=None):
    if ACTIVE=='groq_qwen':return dict(provider='groq',model=GROQ_MODEL)
    if ACTIVE=='google_gemini':return dict(provider='google',model=GEMINI_MODEL)
    if ACTIVE==OPENAI_PROVIDER:
        return dict(provider=OPENAI_PROVIDER,model=OPENAI_MODEL)
    raise ValueError('unapproved_reasoning_profile')

def admit_model(model,provider=None,root=None):
    if provider==OPENAI_PROVIDER or ACTIVE==OPENAI_PROVIDER:
        if ACTIVE!=OPENAI_PROVIDER or profile(root)!=dict(provider=OPENAI_PROVIDER,model=model):raise ValueError('active_selected_OpenAI_model_required')
        if provider!=OPENAI_PROVIDER:raise ValueError('selected_OpenAI_transport_required_no_fallback')
        return OPENAI_PROVIDER
    if model not in {GROQ_MODEL,GEMINI_MODEL} or provider not in {None,'legacy','google','groq'}:raise ValueError('unapproved_reasoning_model')
    return provider or 'legacy'
def transport(emit,*,validated_delivery=False):
    if type(validated_delivery) is not bool:raise ValueError('validated_delivery_boolean_required')
    p=profile()
    if validated_delivery and p['provider']!=OPENAI_PROVIDER:raise ValueError('validated_delivery_selected_OpenAI_required')
    if p['provider']==OPENAI_PROVIDER:
        from me3_openai.transport import Transport,selected_profile
        if selected_profile()!=p:raise ValueError('qualified_OpenAI_selection_required')
        result=Transport(emit,model=p['model'],**(dict(validated_final_units=True) if validated_delivery else {}))
        if validated_delivery and getattr(result,'validated_final_units',False) is not True:raise ValueError('validated_delivery_transport_capability_required')
        return result
    if p['provider']=='google':
        from onit_candidate.gemini_native import Transport
        return Transport(emit)
    from onit_candidate.provider import Transport
    from me3_assistant.backends import groq_key
    result=Transport(emit,key=groq_key(),opt_in=True);result.model=p['model'];result.provider='groq';return result
def require_capacity(root,mode):
    if profile()['provider']=='groq':
        from onit_candidate.provider import require_capacity as check
        return check(root,mode)
    if profile(root)['provider']==OPENAI_PROVIDER:
        from me3_openai.transport import capacity_snapshot as check
        from me3_assistant.admission import Admission
        from onit_candidate.provider import capacity_snapshot
        reasoning=check(root)
        asr=capacity_snapshot(Admission(Path(root)/'evidence/assistant/provider-admission/groq.json'),['asr']) if mode=='normal' else dict(ready=True,reasons=[])
        result=dict(ready=reasoning['ready'] and asr['ready'],reasons=reasoning['reasons']+asr['reasons'],provider_capacity_verified=False,network_calls=0,counters_changed=False,reasoning_provider=OPENAI_PROVIDER,reasoning_model=OPENAI_MODEL,refresh_required=reasoning.get('refresh_required',False))
        if not result['ready']:raise ValueError('Reasoning or speech recognition service capacity is unavailable. No automatic retry.')
        return result
    from me3_assistant.admission import Admission
    from onit_candidate.provider import capacity_snapshot
    from onit_candidate.gemini_provider import admission_path,key
    key(root)  # Presence only, never a request or a provider choice.
    reasoning=capacity_snapshot(Admission(admission_path(root)),['reasoning'])
    asr=capacity_snapshot(Admission(Path(root)/'evidence/assistant/provider-admission/groq.json'),['asr']) if mode=='normal' else dict(ready=True,reasons=[])
    result=dict(ready=reasoning['ready'] and asr['ready'],reasons=reasoning['reasons']+asr['reasons'],provider_capacity_verified=False,network_calls=0,counters_changed=False,reasoning_provider='google',reasoning_model=GEMINI_MODEL)
    if not result['ready']:raise ValueError('Reasoning or speech recognition service capacity is unavailable. No automatic retry.')
    return result
