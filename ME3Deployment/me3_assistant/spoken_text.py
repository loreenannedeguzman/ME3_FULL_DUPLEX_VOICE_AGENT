"""Deterministic speech surface for explicit notation; display/source stay intact."""
import re

def spoken_text(text):
    pieces=re.split(r'(https?://\S+|(?:[A-Za-z]:)?[/\\]\S+|\b[\w-]+\.(?:pdf|txt|md)\b)',text)
    for i in range(0,len(pieces),2):
        part=pieces[i]
        part=re.sub(r'(?<![\w.])([A-Za-z]|[0-9]+(?:\.[0-9]+)?)\s*(?:²|\^\{?2\}?)(?![0-9])',r'\1 squared',part)
        part=re.sub(r'(?<![\w.])([A-Za-z]|[0-9]+(?:\.[0-9]+)?)\s*(?:³|\^\{?3\}?)(?![0-9])',r'\1 cubed',part)
        part=re.sub(r'(?<![\w.])([A-Za-z]|[0-9]+(?:\.[0-9]+)?)\s*\^\{?([0-9]{1,3})\}?',r'\1 to the power of \2',part)
        part=re.sub(r'\b(PDF|AI|API)\b',lambda m:' '.join(m[0]),part)
        pieces[i]=part
    return ''.join(pieces)
