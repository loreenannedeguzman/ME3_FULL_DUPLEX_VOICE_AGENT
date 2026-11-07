"""Native host delivery of one model-selected owned-library passage.

No intent parsing, tool selection, extra lookup, authorization or cursor write.
The established owned-library validator remains the source/text contract.
"""
import hashlib,json,re
from me3_assistant.result_delivery import verbatim_passage

def _owned_note(registry,alias,tools):
    origin=registry.reads.get(alias);spec=next((s for s in registry.config['servers'] if origin and s['id']==origin[0]),{})
    return bool(origin and origin[1] in tools and spec.get('module')=='me3_productivity.server' and spec.get('identity')=='me3')

def _note_passage(registry,item,data,completed):
    alias=item['name'];args=item['args']
    if not _owned_note(registry,alias,{'read_note'}):return None
    for key in completed:
        old_alias,_=json.loads(key)
        if not _owned_note(registry,old_alias,{'list_notes','search_notes'}):return None
    if not isinstance(data,dict):return None
    row=data.get('row');offset=args.get('body_offset',0);limit=args.get('max_body_chars',800);expected=args.get('expected_revision')
    if (data.get('read_only') is not True or data.get('delivery')!='verbatim' or data.get('verification_source')!='fresh_productivity_sqlite_connection'
        or not isinstance(row,dict) or row.get('kind')!='note' or row.get('state') not in {'active','archived'}
        or not isinstance(args.get('note_id'),str) or re.fullmatch('[0-9a-f]{32}',args['note_id']) is None or row.get('id')!=args['note_id']
        or type(row.get('revision')) is not int or row['revision']<1 or type(data.get('expected_revision')) is not int or data['expected_revision']!=row['revision'] or expected is not None and (type(expected) is not int or expected!=row['revision'])
        or type(offset) is not int or offset<0 or offset and expected is None or type(limit) is not int or not 1<=limit<=800
        or type(data.get('body_offset')) is not int or data['body_offset']!=offset or not isinstance(row.get('body'),str) or len(row['body'])>limit
        or any(ord(c)<32 and c not in '\n\t' for c in row['body'])
        or type(data.get('body_characters')) is not int or data['body_characters']<offset+len(row['body'])
        or not isinstance(data.get('body_sha256'),str) or re.fullmatch('[0-9a-f]{64}',data['body_sha256']) is None):return None
    end=offset+len(row['body']);total=data['body_characters']
    if data.get('body_complete') is not (end==total) or data.get('next_body_offset')!=(end if end<total else None):return None
    if offset==0 and end==total and hashlib.sha256(row['body'].encode()).hexdigest()!=data['body_sha256']:return None
    return row['body'] if row['body'].strip() else 'There is no text in this saved note window to read aloud.'

def passage_source(registry,alias):
    return 'owned_productivity_note_read' if _owned_note(registry,alias,{'read_note'}) else 'owned_library_read'

def verified_passage(registry,item,data,completed,*,single_tool_batch,prior_observations,pending_review=False,unresolved_effects=False):
    if (single_tool_batch is not True or pending_review or unresolved_effects
        or len(completed)!=prior_observations or item.get('name') not in registry.reads):return None
    args=item.get('args')
    if not isinstance(args,dict) or args.get('delivery')!='verbatim':return None
    if _owned_note(registry,item['name'],{'read_note'}):return _note_passage(registry,item,data,completed)
    passage=verbatim_passage(registry,item['name'],args,data,completed)
    # Preserve the original validated passage boundaries for display/copy.
    # Blank/image-only source pages retain the validator's truthful notice.
    return data['text'] if passage is not None and data['text'].strip() else passage
