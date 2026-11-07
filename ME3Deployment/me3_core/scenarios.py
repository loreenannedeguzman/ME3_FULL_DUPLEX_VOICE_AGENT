"""Focused functional checks, not a comprehensive latency benchmark."""
LIMITS={'asr':22,'reasoning':12,'tts':26,'commit_item':1,'controlled_timeout':1}
SESSIONS={'A':{
 'A1':{'script':'The test word is mango; please remember it.','expected':'coherent acknowledgment'},
 'A2':{'script':'What was the test word?','expected':'mango'},
 'A3':{'script':'Count slowly from one to ten. When audible, overlap: What is two plus two?','expected':'audible count stops, four, no resumption'},
 'A4':{'script':'What is the capital [pause TWO seconds] of France?','expected':'Paris from actual split continuation'},
 'A5':{'script':'Read my shopping list.','expected':'existing saved list accurately read'}},
 'B':{
 'B1':{'script':'I have an early exam tomorrow and several chapters left to study. Give me one practical study tip in a single short sentence.','expected':'relevant short study tip'},
 'B2':{'script':'Count slowly from one to ten. When audible, overlap: What is the capital of France?','expected':'count stops, Paris, no resumption'},
 'B3':{'script':'Name one famous landmark there.','expected':'Paris landmark in context'},
 'B4':{'script':'Please tell me [pause TWO seconds] Actually, what is five plus five?','expected':'ten, abandoned prefix not executed'}},
 'C':{
 'C1':{'script':'Hello.','expected':'explicit timeout notice under one armed100ms local ASR deadline; not natural outage'},
 'C2':{'script':'What is twelve plus eight?','expected':'twenty after cleanup, without restart'},
 'C3':{'script':'Add three erasers to my shopping list. [wait for correct proposal] Confirm. [after verification] Read my shopping list.','expected':'correct proposal/direct confirmation/actual TaskMCP/independent SQL/original rows preserved/new3erasers'}}}

# Explicitly approved task-only follow-up; shared original allowance, no text calls.
SESSIONS["D"]={"D1":{"script":"Add three erasers to my shopping list. [correct proposal] Confirm. [verified] Read my shopping list.","expected":"three erasers independently saved and all four rows spoken; no automatic write or provider retry"}}

SESSIONS['E']={'E1':{'script':'Add to my shopping list. [item question] Three erasers. [correct proposal] Confirm.', 'expected':'essential detail question, correct proposal/direct confirmation, independently verified three-eraser save; no text calls or extra read utterance'}}
