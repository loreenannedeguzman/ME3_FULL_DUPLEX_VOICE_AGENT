"""A bound voice-owner control; no external effect or model-supplied identity."""
from onit_candidate.protocol import CandidateError

NAME='finish_conversation'
DECL=dict(name=NAME,description='End this voice conversation after a short final farewell is fully spoken. Use only for the latest user intent to finish the current session. Finish required player controls first. Call this alone, then final farewell text only; no further tools. Does not undo completed actions. Not for stopping music, pausing reading, quoted goodbyes, or negated/hypothetical requests.',parameters=dict(type='object',properties={},required=[],additionalProperties=False))

def validate_batch(calls,allowed):
    if allowed is not True or not isinstance(calls,list) or len(calls)!=1:
        raise CandidateError('session_end_requires_one_bound_voice_control')
    call=calls[0]
    if not isinstance(call,dict) or call.get('name')!=NAME or not isinstance(call.get('args'),dict) or call['args']!={}:
        raise CandidateError('session_end_takes_no_model_identity_or_arguments')
