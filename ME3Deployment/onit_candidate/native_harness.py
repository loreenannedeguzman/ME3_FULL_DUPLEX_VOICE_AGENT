"""Pinned upstream context tools, not a second planner or external authority."""
import copy
from jsonschema import Draft202012Validator
from onit_candidate.protocol import CandidateError
SPECS=[{'name': 'context_status', 'description': 'How full your context window is, what this run has done so far, and which notes you hold. Takes no arguments.', 'parameters': {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False}}, {'name': 'note_write', 'description': 'Save a short note that survives context summarization. Writing a key again replaces what was there.', 'parameters': {'type': 'object', 'properties': {'key': {'type': 'string', 'description': "Short identifier, letters/digits/._- only (e.g. 'findings', 'remaining_work')."}, 'text': {'type': 'string', 'description': 'The note, up to 8000 characters.'}}, 'required': ['key', 'text'], 'additionalProperties': False}}, {'name': 'note_read', 'description': 'Read back a note saved with note_write. context_status lists the keys.', 'parameters': {'type': 'object', 'properties': {'key': {'type': 'string', 'description': "Short identifier, letters/digits/._- only (e.g. 'findings', 'remaining_work')."}}, 'required': ['key'], 'additionalProperties': False}}, {'name': 'result_read', 'description': 'Read a window of a large tool result that was stored instead of being pasted in full. The [result:NNNN …] line above a truncated result carries its handle.', 'parameters': {'type': 'object', 'properties': {'handle': {'type': 'string', 'description': "The handle from a [result:NNNN …] line, e.g. '0007'."}, 'offset': {'type': 'integer', 'minimum': 0, 'description': 'Character to start from. Default 0.'}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 8000, 'description': 'Characters to return, up to 8000. Default 4000.'}}, 'required': ['handle'], 'additionalProperties': False}}, {'name': 'result_grep', 'description': 'Find lines matching a pattern in a stored tool result, with surrounding context. Faster than paging through it with result_read when you know what you are looking for.', 'parameters': {'type': 'object', 'properties': {'handle': {'type': 'string', 'description': "The handle from a [result:NNNN …] line, e.g. '0007'."}, 'pattern': {'type': 'string', 'description': 'Regular expression, or a plain string.'}, 'context': {'type': 'integer', 'minimum': 0, 'maximum': 10, 'description': 'Lines to show around each match. Default 3.'}}, 'required': ['handle', 'pattern'], 'additionalProperties': False}}]
# Raw pinned upstream declarations remain immutable and independently checked.
ALIASES={'context_note_write':'note_write','context_note_read':'note_read'}
DESCRIPTIONS={
 'context_status':'Inspect this task’s ephemeral Onit working-memory/context usage. Listed note keys belong only to this task, never the user’s saved Pi notes.',
 'context_note_write':'Write ephemeral agent working memory private to this current task sandbox, retained through context compaction. It is not reloaded as persistent user notes or by later tasks. This does NOT save a user note or create/update any persistent Pi record. To fulfill a user request to save a note, use guarded propose_productivity with kind note and separate confirmation.',
 'context_note_read':'Read this current task’s ephemeral agent working-memory key. This is NOT the user’s saved Pi note library. Read persistent user notes with the productivity MCP read_note tool.'}

def upstream_declarations():return copy.deepcopy(SPECS)

def declarations():
 ds=upstream_declarations()
 for d in ds:
  d['name']={v:k for k,v in ALIASES.items()}.get(d['name'],d['name'])
  if d['name'] in DESCRIPTIONS:d['description']=DESCRIPTIONS[d['name']]
 return ds

NAMES=frozenset(d['name'] for d in declarations())

def upstream_name(name):
 if name not in NAMES:raise CandidateError('native_context_tool_unknown')
 return ALIASES.get(name,name)

def native_result(name,text):
 # The unchanged upstream output stays a string, not parsed/reinterpreted data.
 if not isinstance(text,str) or len(text)>20000:raise CandidateError('native_context_result_size')
 if name in ALIASES:
  import json
  result=json.dumps(dict(scope='ephemeral_agent_working_memory',lifetime='current_task_sandbox',user_record_saved=False,persistent_note_written=False,upstream_result=text),ensure_ascii=False)
  if len(result)>20000:raise CandidateError('native_context_result_size')
  return result
 return text

def validate(calls):
 if len(calls)!=1 or calls[0].get('name') not in NAMES:raise CandidateError('native_context_tool_must_be_alone')
 Draft202012Validator(next(d['parameters'] for d in declarations() if d['name']==calls[0]['name'])).validate(calls[0].get('args'))
