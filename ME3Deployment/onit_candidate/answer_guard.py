"""Narrow delivery contracts, never tool interpretation or task planning."""
import re
from onit_candidate.protocol import CandidateError
HOLD='hold_reading'
HOLD_DECL=dict(name=HOLD,description='Pause or stop the current explanation/reading when the user wants to listen to music or have quiet. Leaves the music player and all committed actions unchanged. Preserves any retained text for a later explicit continuation. Call alone, never with music or other operations. Does not end the conversation.',parameters=dict(type='object',properties={},additionalProperties=False))
def clean_answer(text):
 if not isinstance(text,str) or re.search(r'(?:<|&lt;)\s*/?\s*(?:tool_call|function|parameter)(?:\s|=|>|&gt;|$)',text,re.I):raise CandidateError('native_tool_markup_in_answer')
 return text
def assertion_text(text):
 """A quoted regression/example sentence followed by passes/fails is data.

 A quoted track title followed by a real playing assertion is NOT masked.
 A stand-alone quoted completion claim remains subject to verification.
 """
 return re.sub(r'["“]([^"”\n]+)["”](?=\s+(?:passes?|fails?|would|should|illustrates|means)\b)', '[quoted example]',text,flags=re.I)

def player_volume_subject(clause):
 """Recognize player-status/control assertions, not arbitrary physical volume.

 This is a scoped assertion validator, never request routing or authorization.
 Generic qualified objects and volume-of-object explanations do not acquire
 a player identity from an unrelated word elsewhere in the same sentence.
 """
 audio=r'(?:player|music|playback|speaker|audio|sound|track|song)'
 explicit=re.search(r'\b'+audio+r"(?:'s)?\s+volume\b|\bvolume\s+of\s+(?:(?:the|your)\s+)?"+audio+r'\b',clause,re.I)
 playing=re.search(r'\bplaying\s+at\s*\d+(?:\.\d+)?\s*(?:%|percent)\s+(?:player\s+)?volume\b',clause,re.I)
 if explicit or playing:return 'explicit_player'
 if re.search(r'\bvolume\s+of\b',clause,re.I):return None
 subject=r'(?:(?:the|your|current)\s+){0,3}(?:'+audio+r'\s+)?volume\b'
 if re.match(r'^\s*["“\-]*\s*'+subject,clause,re.I):return 'bare_status'
 if re.search(r"\b(?:I(?: have|'ve)?|we(?: have|'ve)?)\s+(?:set|raised|increased|decreased|changed|adjusted)\s+"+subject,clause,re.I):return 'reported_control'
 if re.match(r'^\s*(?:set|raised|increased|decreased|changed|adjusted)\s+'+subject,clause,re.I):return 'control_fragment'
 return None

def volume_claims(text,observations):
 """Guard concrete player-volume assertions, not general semantic truthfulness."""
 verified=[];changed=[]
 for row in observations:
  data=row.get('result',{});music=data.get('verified_music',{}) if isinstance(data,dict) else {}
  if row.get('tool') in {'propose_music_control','propose_stream_control'} and (row.get('tool')!='propose_stream_control' or music.get('source')=='SoundCloud') and data.get('status')=='verified' and data.get('verification')=='independent_readback' and music.get('command') in {'volume','play','play_playlist'} and type(music.get('volume_percent')) is int:
   verified.append(music['volume_percent']);changed.append(music['volume_percent'])
  elif row.get('tool')=='music__get_playback_status' and isinstance(data,dict) and type(data.get('volume_percent')) is int:verified.append(data['volume_percent'])
  elif row.get('tool')=='soundcloud__get_stream_status' and isinstance(data,dict):
   snapshot=data.get('snapshot',{});value=snapshot.get('volume')
   if snapshot.get('ready') is True and snapshot.get('error') is None and type(value) is int and 0<=value<=100:verified.append(value)
 plain=re.sub(r'[*_`#]','',assertion_text(text))
 # Split contrasts so a refused200percent does not exempt a later concrete
 #100percent assertion. A maximum/range explanation is not a current readback.
 clauses=re.split(r'(?<!\d)[.!?](?!\d)|[;\n]|\b(?:but|however)\b|,(?=\s*(?:the\s+)?current\s+volume)',plain,flags=re.I)
 for sentence in clauses:
  if not re.search(r'\bvolume\b',sentence,re.I):continue
  scope=player_volume_subject(sentence)
  if scope is None:continue
  if re.match(r'^\s*(?:if|would|could|can you|please)\b',sentence,re.I):continue
  if re.fullmatch(r'\s*(?:the\s+)?(?:supported|allowed|permitted)\s+(?:player\s+)?volume\s+range\s+is\s+0\s*[–−-]\s*100\s*(?:%|percent)\s*',sentence,re.I):continue
  assertion=(re.search(r"\bvolume\b.{0,55}\b(?:is|was|now|set|raised|increased|decreased|changed|adjusted|at)\b",sentence,re.I)
    or re.search(r'\b(?:set|raised|increased|decreased|changed|adjusted)\b.{0,45}\bvolume\b',sentence,re.I)
    or re.search(r'\b(?:playing|at|to)\b.{0,45}\d+(?:\.\d+)?\s*(?:%|percent).{0,15}\b(?:player\s+)?volume\b',sentence,re.I)
    or re.search(r'\b(?:current\s+)?volume\s*:\s*\d',sentence,re.I))
  if not assertion:continue
  if re.search(r"\b(?:cannot|can't|couldn't|didn't|did not|have not)\b.{0,30}\b(?:set|raise|increase|change|adjust)\b",sentence,re.I):continue
  numbers=[float(n) for n in re.findall(r'(\d+(?:\.\d+)?)\s*(?:%|percent)',sentence,re.I)]
  change=bool(re.search(r'\b(?:set|raised|increased|decreased|changed|adjusted)\b',sentence,re.I))
  if not numbers and not change and not re.search(r'\b(?:now|higher|lower|louder|quieter|maximum)\b',sentence,re.I):continue
  evidence=changed if change else verified
  negated=bool(re.search(r"\bvolume\b.{0,20}\b(?:is not|isn't|was not|wasn't)\b",sentence,re.I))
  if not evidence or any((n==evidence[-1]) if negated else (n!=evidence[-1]) for n in numbers):
   error=CandidateError('unverified_volume_claim')
   error.diagnostics=dict(scope='player_volume',subject_kind=scope,percentage_values=numbers[:4],current_readback_present=bool(verified),verified_change_required=change,last_observed_volume=verified[-1] if verified else None,text_retained=False)
   raise error

 return text
def stop_metadata(raw):
 if not isinstance(raw,dict) or set(raw)!={'stop_reason','drafts','final_complete'}:raise CandidateError('invalid_native_stop_diagnostics')
 if raw['stop_reason'] not in {'answered','turn_limit','repeated_tool_call','planning_exhausted','repetition_loop','safety_abort','unrecognized'} or type(raw['drafts']) is not int or not 0<=raw['drafts']<=9 or type(raw['final_complete']) is not bool:raise CandidateError('invalid_native_stop_diagnostics')
 return dict(**raw,text_retained=False)


def music_state_claims(text,observations):
 """Bind concrete player-state/readback assertions to this turn, not history."""
 plain=re.sub(r'[*_`#]','',assertion_text(text))
 states=[];readback=False
 for row in observations:
  data=row.get('result',{})
  if not isinstance(data,dict):continue
  if row.get('tool')=='music__get_playback_status':
   readback=True
   if data.get('state') in {'playing','paused','stopped','ended'}:states.append(data['state'])
  elif row.get('tool') in {'propose_music_control','propose_stream_control'} and (row.get('tool')!='propose_stream_control' or data.get('verified_music',{}).get('source')=='SoundCloud') and data.get('status')=='verified' and data.get('verification')=='independent_readback':
   readback=True;value=data.get('verified_music',{}).get('state')
   if value in {'playing','paused','stopped','ended'}:states.append(value)
 claims=[(m.group('state').casefold(),bool(m.group('negated'))) for m in re.finditer(r'\bstate\s*:\s*(?P<negated>not\s+)?(?P<state>playing|paused|stopped|ended)\b',plain,re.I)]
 pattern=r"\b(?P<no>no\s+)?(?:music|player|playback|track)\s+(?P<verb>isn't|wasn't|hasn't been|is|was|has been)\s+(?:now\s+)?(?P<not>not\s+)?(?:now\s+)?(?P<state>playing|paused|stopped|ended)\b"
 for match in re.finditer(pattern,plain,re.I):
  negations=int(bool(match.group('no')))+int(bool(match.group('not')))+int("n't" in match.group('verb').casefold())
  if negations>1:raise CandidateError('ambiguous_music_state_claim')
  claims.append((match.group('state').casefold(),negations==1))
 # A negated state still needs current evidence. In particular, "no music is
 # playing" is supported by stopped/paused/ended, never by playing or history.
 if claims and (not states or any((value==states[-1]) if negated else (value!=states[-1]) for value,negated in claims)):raise CandidateError('unverified_music_state_claim')
 if re.search(r'\b(?:verified|checked|confirmed)\s+(?:via|by|with|through|using)\s+(?:an?\s+)?independent\s+readback',plain,re.I) and not readback:raise CandidateError('unverified_music_readback_claim')
 return text


def substantive_answer(text):
 """An unexplained generic refusal is a failed goal, never an automatic retry.

 Legitimate specific limitations and supported alternatives remain ordinary
 answers. This catches empty refusal boilerplate, not semantic safety decisions.
 """
 plain=text.strip().casefold().replace("’", "'")
 if re.fullmatch(r"(?:i(?:'m| am) sorry[, ]*(?:but )?|sorry[, ]*)?(?:i )?(?:can(?:not|'t)|am unable to) help(?: you)?(?: with)? (?:that|this)(?: request)?[.! ]*",plain):raise CandidateError('unexplained_generic_refusal')
 return text
