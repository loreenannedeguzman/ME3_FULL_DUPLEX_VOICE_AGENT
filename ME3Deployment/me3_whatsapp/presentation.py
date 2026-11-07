"""Present exact completed contact reads; never choose tools, people or actions."""
import re
from .recipients import norm,inspection_choice
from .guard import Rejected

def simple_lookup(request,query):
 if not isinstance(request,str) or not isinstance(query,str) or not 2<=len(query)<=160:return False
 r=norm(request).rstrip('.?!');q=re.escape(norm(query))
 # Do not let an over-broad model query hide the rest of a compound goal.
 if re.search(r'\b(?:and|then|also|while|after|before|but|plus|or|send|message|text|call|phone|ring|calculate|explain|tell|play|stop|close)\b',r) or ';' in r:return False
 r=re.sub(r'^(?:(?:please|can you|could you|would you|will you|i want you to)\s+)+','',r)
 place=r'(?:my |the )?(?:whatsapp )?(?:contacts|contact list|directory)'
 return bool(re.fullmatch(r'(?:find|look up|lookup) (?:contact )?'+q+r'(?: (?:in|on|from) '+place+r')?',r) or re.fullmatch(r'search(?: '+place+r')? (?:for )?'+q,r))

def safe_label(value):
 return isinstance(value,str) and 1<=len(value)<=200 and all(c.isprintable() for c in value) and not any(c in value for c in '<>`{}[]')

def valid_rows(rows):
 if not isinstance(rows,list) or len(rows)>8:return False
 seen=set()
 for i,row in enumerate(rows,1):
  if not isinstance(row,dict) or type(row.get('position')) is not int or row['position']!=i or not isinstance(row.get('selection_id'),str) or not re.fullmatch('[0-9a-f]{32}',row['selection_id']) or not safe_label(row.get('label')) or type(row.get('identity_verified')) is not bool:return False
  phone=row.get('phone')
  if row['identity_verified']:
   if not isinstance(phone,str) or not re.fullmatch(r'\+[1-9][0-9]{6,14}',phone):return False
  elif phone is not None:return False
  if row['selection_id'] in seen:return False
  seen.add(row['selection_id'])
 return True

def contact_reply(request,name,args,result,choices):
 """None keeps the native planner loop; strings are exact read-only receipts."""
 if name not in {'whatsapp__search_contacts','whatsapp__inspect_contact'} or not isinstance(args,dict) or not isinstance(result,dict) or choices is None:return None
 if any(type(result.get(k)) is not int or result[k]!=0 for k in ['messages_sent','calls_started']):return None
 snapshot=choices.snapshot();rows=snapshot.get('matches')
 if not valid_rows(rows):return None
 if name=='whatsapp__search_contacts':
  if set(args)!={'query'} or result.get('source')!='current_visible_WhatsApp_directory' or result.get('query')!=args['query'] or not simple_lookup(request,args['query']):return None
  matches=result.get('matches')
  if not valid_rows(matches) or matches!=rows or (matches and snapshot.get('available') is not True):return None
  if not matches:return 'No matching WhatsApp contacts were found. No message or call has started.'
  lines=['Matching WhatsApp contacts:']
  for row in matches:lines.append(str(row['position'])+'. '+row['label']+(' - verified number '+row['phone'] if row['identity_verified'] else '')+'.')
  lines.append('Please choose the intended contact.' if len(matches)>1 else 'This is the matching contact.')
  if any(not r['identity_verified'] for r in matches):lines.append('Unverified phone numbers still need contact inspection.')
  return '\n'.join(lines)
 if set(args)!={'selection_id'} or snapshot.get('available') is not True:return None
 try:row=inspection_choice(request,rows,args['selection_id'])
 except Rejected:return None
 if result.get('identity_verified') is not True or row.get('identity_verified') is not True or any(result.get(k)!=row.get(k) for k in ['selection_id','label','phone']):return None
 return row['label']+' - WhatsApp number '+row['phone']+', verified from visible contact information. No message or call has started.'
