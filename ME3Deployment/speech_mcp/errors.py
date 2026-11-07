"""Allowlisted diagnostic codes only; provider messages are never forwarded."""
VALIDATION_REASONS=frozenset({
 'invalid_generation','invalid_request_id','invalid_audio_format','audio_size_limit',
 'invalid_base64','invalid_pcm_size','invalid_text','tts_audio_size_limit',
 'unsupported_tts_wav','tts_duration_limit','truncated_tts_wav','invalid_tts_wav',
 'tts_no_audio','unsupported_tts_audio','unsupported_tts_rate','invalid_tts_pcm',
 'tts_expected_one_audio_part','invalid_human_turn_limit',
 'invalid_tts_provider','invalid_google_tts_model','invalid_tts_timeout','invalid_flite_voice','local_tts_engine_required',
 'invalid_elevenlabs_voice','invalid_elevenlabs_model','invalid_tts_budget','invalid_tts_budget_state',
 'elevenlabs_key_required','elevenlabs_voice_required','elevenlabs_access_confirmation_required','elevenlabs_budget_required',
 'invalid_piper_worker_mode','invalid_piper_frame','piper_frame_identity_mismatch','piper_worker_closed',
 'piper_python_required','piper_model_required','invalid_piper_header','invalid_piper_pcm',
 'tts_request_budget_exhausted','tts_provider_http_error','tts_timeout','tts_transport_error','local_tts_process_failed',
})

def known_validation_reason(value):
    return value if isinstance(value,str) and value in VALIDATION_REASONS else 'unclassified'

def validation_reason(exc):
    safe=known_validation_reason(getattr(exc,'safe_reason',None))
    if safe!='unclassified':return safe
    # Never stringify an arbitrary SDK exception or serialize its arguments.
    if type(exc) is ValueError and len(exc.args)==1:
        return known_validation_reason(exc.args[0])
    return 'unclassified'
