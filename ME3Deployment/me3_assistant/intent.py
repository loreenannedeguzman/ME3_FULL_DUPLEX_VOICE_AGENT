"""Host-owned proposal provenance and no-effect prerequisite clarification.

A binding proves which direct owner turn was used, not its semantic meaning.
Onit interprets language. Only completed exact review and separate direct
confirmation authorize a communication effect; a draft is never authority.
"""
import hashlib,re

def request_binding(owner,goal,generation,request,*,source='direct_current_user'):
    if source!='direct_current_user' or any(not isinstance(v,str) or not re.fullmatch('[0-9a-f]{32}',v) for v in (owner,goal)) or type(generation) is not int or generation<1:
        raise ValueError('current_owner_request_required')
    if not isinstance(request,str) or not request.strip() or len(request.encode('utf-8'))>4000 or any(ord(c)<32 for c in request):
        raise ValueError('bounded_current_owner_request_required')
    return dict(version=1,owner=owner,goal=goal,generation=generation,request_sha256=hashlib.sha256(request.encode('utf-8')).hexdigest(),source=source)

def message_text(text):
    if not isinstance(text,str) or not text or text!=text.strip() or len(text.encode('utf-8'))>2000 or any(ord(c)<32 and c not in '\n\t' for c in text):
        raise ValueError('exact_bounded_message_draft_required')
    return text

class ClarificationNeeded(ValueError):
    REPLIES={
        'selected_recipient_required':'Please choose the intended person from the current contact results and let me verify their number before I prepare a send review. Nothing has been sent.',
        'fresh_recipient_required':'That contact selection is unavailable or expired. Ask me to find the person again, then choose the intended contact. Nothing has been sent.',
        'selected_recipient_mismatch':'That is not the currently selected verified contact. Please choose the intended person before I prepare a send review. Nothing has been sent.',
    }
    def __init__(self,code):
        if code not in self.REPLIES:raise ValueError('unknown_proposal_clarification')
        self.code=code;self.reply=self.REPLIES[code];super().__init__(code)
