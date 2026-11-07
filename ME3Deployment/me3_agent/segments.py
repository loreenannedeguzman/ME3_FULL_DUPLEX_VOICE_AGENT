"""Preserve explicit floor holds while accepting varied complete question forms."""
import re
from me3_completion.policy import holds_floor,resolve_segments as legacy_resolve
from task_mcp.reasoner import normalized,CONFIRM

# A question word and auxiliary followed by actual content, not a demo phrase.
QUESTION=re.compile(r"(?:^|[.!?]\s+)(?:why|who|when|where|what|which|how)(?:\s+[a-z]+){0,3}\s+(?:am|is|are|was|were|do|does|did|can|could|should|will|would|has|have|makes|causes|happens|invented)\s+\S+",re.I)

def resolve_segments(previous,current):
    if normalized(current) in CONFIRM or re.match(r'^(?:actually|instead|stop)[,.:! ]+',current.strip(),re.I):
        return legacy_resolve(previous,current)
    if QUESTION.search(current):
        if previous and holds_floor(previous):return 'continuation',previous.rstrip()+' '+current.strip()
        if holds_floor(current):return 'complete',current
    return legacy_resolve(previous,current)
