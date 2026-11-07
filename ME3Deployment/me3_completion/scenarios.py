"""Prospective functional cues; not a comprehensive latency benchmark."""
LIMITS={'asr':48,'reasoning':24,'tts':60,'commit_item':1,'controlled_timeout':1}
SESSIONS={
'A':{
'A1':{'dimension':'task/readback','cue':'Read my shopping list','expected':'two notebooks, two pens, five pencils, three erasers; no write'},
'A2':{'dimension':'pause/continuation','cue':'What is the capital [pause TWO seconds] of France?','expected':'holds incomplete turn, then Paris; no prefix lost'},
'A3':{'dimension':'pause/completed turn','cue':'I have an exam tomorrow [pause ONE second] give me one short study tip.','expected':'no premature substantive takeover; relevant completed reply'},
'A4':{'dimension':'hesitation/self-correction','cue':'I want to ask about Spain [pause ONE second] actually, what is the capital of France?','expected':'Paris; no abandoned Spain answer'},
'A5':{'dimension':'agent backchannel','cue':'Let me explain what I need help with [pause TWO seconds] I have several chapters to study [pause ONE second] please give me one short study tip.','expected':'brief neutral acknowledgment during held turn; no substantive takeover; one coherent tip'}},
'B':{
'B1':{'dimension':'context','cue':'The test word is mango; please remember it.','expected':'clear acknowledgment'},
'B2':{'dimension':'context','cue':'What was the test word?','expected':'mango'},
'B3':{'dimension':'user backchannel','cue':'Count slowly from one to twenty. While counting is audible, say one brief mm-hmm, then listen.','expected':'ack classified separately, suspended response may resume; no cancellation/new unrelated answer; report pause intrusiveness'},
'B4':{'dimension':'interruption','cue':'Count slowly from one to ten. Speak over audible counting: What is the capital of France?','expected':'prompt stop, Paris, no canceled counting returns'},
'B5':{'dimension':'context after interruption','cue':'Name one famous landmark there.','expected':'Paris landmark; no old counting'}},
'C':{
'C1':{'dimension':'repeated interruption','cue':'Count slowly from one to ten. Speak over audible counting: What is two plus two?','expected':'prompt stop, four, no canceled counting returns'},
'C2':{'dimension':'split replacement','cue':'Please tell me [pause TWO seconds] Actually, what is five plus five?','expected':'ten; abandoned request not revived'},
'C3':{'dimension':'split continuation','cue':'What is the capital [pause TWO seconds] of France?','expected':'Paris; no repetition required'},
'C4':{'dimension':'smooth completed turn','cue':'What is twelve plus eight?','expected':'twenty; coherent prompt reply'}},
'D':{
'D1':{'dimension':'controlled timeout','cue':'Hello','expected':'declared100ms ASR deadline notice; not a natural provider outage'},
'D2':{'dimension':'same-process recovery','cue':'What is twelve plus eight?','expected':'twenty after deadline; capture/server remain usable'},
'D3':{'dimension':'task clarification','cue':'Add to my shopping list','expected':'asks essential item/quantity; no write'},
'D4':{'dimension':'task proposal','cue':'Three markers','expected':'proposes three markers, asks confirmation; no write'},
'D5':{'dimension':'task correction','cue':'Four markers','expected':'new four-marker proposal; fresh confirmation required; no write'},
'D6':{'dimension':'task abandonment','cue':'Cancel','expected':'acknowledges cancellation; database remains original four rows'}},
'E':{
'E1':{'dimension':'task correct proposal','cue':'Add four markers to my shopping list','expected':'correct quantity/item; asks confirmation; no write yet'},
'E2':{'dimension':'task execution/verification','cue':'Confirm','expected':'one actual MCP commit and independent read-back; original rows unchanged'},
'E3':{'dimension':'task spoken read-back','cue':'Read my shopping list','expected':'original four entries plus four markers, verified independently'}}}
