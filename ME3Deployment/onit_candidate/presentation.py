"""Speech-only projection. Original model/source/display/history text stays intact.

Recognize presentation syntax conservatively, protect code and literal paths,
and keep mathematical multiplication distinct from Markdown emphasis.
"""
import re
from me3_assistant.spoken_text import spoken_text as notation
from me3_assistant.speech import response_parts

INITIALISMS={'AI':'artificial intelligence','PDF':'pee, dee, eff','API':'ay, pee, eye'}

def spoken_text(text,language='en'):
    if language not in {'en','es'}:raise ValueError('unsupported_speech_language')
    literals=[]
    def hold(value):
        literals.append(value)
        return '\ue000'+str(len(literals)-1)+'\ue001'
    # Code is literal; remove only the enclosing presentation delimiters.
    text=re.sub(r'^ {0,3}(`{3,}|~{3,})[^\n]*\n(.*?)^ {0,3}\1\s*$',lambda m:hold(m[2]),text,flags=re.M|re.S)
    text=re.sub(r'(`+)([^`\n]+?)\1',lambda m:hold(m[2]),text)
    # Link destinations remain in the original display/history. Read the label.
    text=re.sub(r'!?\[([^\]\n]+)\]\((?:https?://[^\s)]+)(?:\s+"[^"]*")?\)',r'\1',text)
    text=re.sub(r'(https?://\S+|(?:[A-Za-z]:)?[/\\]\S+|\b[\w-]+\.(?:pdf|txt|md)\b)',lambda m:hold(m[0]),text)
    text=re.sub(r'^ {0,3}#{1,6}\s+(.+?)(?:\s+#+)?$',r'\1',text,flags=re.M)
    text=re.sub(r'^ {0,3}>\s?', '',text,flags=re.M)
    text=re.sub(r'^\s{0,3}(?:[-*_]\s*){3,}$','',text,flags=re.M)
    text=re.sub(r'^(\s*)[-+*]\s+(?:\[[ xX]\]\s*)?',r'\1',text,flags=re.M)
    # Paired inline markers require adjacent non-space content. Thus a * b
    # remains arithmetic, and file_name and paths are never rewritten.
    for marker in ('**','__','~~','*','_'):
        escaped=re.escape(marker)
        text=re.sub(r'(?<![\w\\])'+escaped+r'(?=\S)(.+?\S|\S)'+escaped+r'(?!\w)',r'\1',text)
    if language=='en':text=re.sub(r'\b(?:AI|PDF|API)\b',lambda m:INITIALISMS[m[0]],text)
    # E.164 phone identities are spoken digit by digit, never as quantities.
    # Literals (code, paths and URLs) remain protected above.
    digits=('zero','one','two','three','four','five','six','seven','eight','nine')
    def phone(m):
        return 'plus ' + ', '.join(digits[int(c)] for c in m[0][1:])
    if language=='en':
        text=re.sub(r'(?<![\w.+-])\+[1-9][0-9]{6,14}(?!\w|\.[0-9])',phone,text)
        text=notation(text)
    return re.sub('\ue000([0-9]+)\ue001',lambda m:literals[int(m[1])],text)

def _phrase_cut(text,limit):
    # A sentence boundary wins over an earlier comma. Closing quotation marks
    # stay with their sentence. Decimal points are not followed by whitespace;
    # numbered list markers and initials are not treated as sentence endings.
    sentences=[]
    for match in re.finditer(r'[.!?]+[\"\'\u2019\u201d)\]]*(?=\s|$)',text[:limit+1]):
        end=match.end()
        if end>limit:continue
        if text[match.start()]=='.' and re.search(r'(?:^|\s)(?:[0-9]+|(?:[A-Za-z]\.)*[A-Za-z]{1,3})\.$',text[:end]):continue
        sentences.append(end)
    if sentences:return sentences[-1]
    clauses=[m.end() for m in re.finditer(r'[;,:](?=\s)',text[:limit+1]) if 35<=m.end()<=limit]
    if clauses:return clauses[-1]
    spaces=[m.start() for m in re.finditer(r'\s+',text[:limit+1]) if 0<m.start()<=limit]
    return spaces[-1] if spaces else 0

def speech_parts(text):
    # Preserve the established numeric-list grouping and every input character.
    if re.fullmatch(r'\s*[0-9]{1,4}(?:,\s+[0-9]{1,4}){7,19}[.!]?\s*',text):
        return response_parts(text)
    parts=[];remaining=text
    while remaining:
        # Keep the responsive initial window; later synthesis inputs stay160.
        limit=72 if not parts else 160
        if len(remaining)<=limit:
            # A trailing separator belongs to the preceding voiced chunk.
            # Preserve every character without asking TTS for blank speech;
            # its stripped input and the existing speech windows stay intact.
            if parts and not remaining.strip():parts[-1]+=remaining
            else:parts.append(remaining)
            break
        cut=_phrase_cut(remaining,limit)
        if not cut:
            # An unusually long token has no natural boundary. Retain the old
            # bounded lossless fallback; never wait indefinitely or omit it.
            cut=limit
        parts.append(remaining[:cut]);remaining=remaining[cut:]
    return parts
