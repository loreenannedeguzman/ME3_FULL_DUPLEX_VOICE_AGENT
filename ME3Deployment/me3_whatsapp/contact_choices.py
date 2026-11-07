"""Fresh current-session offered contacts for Onit; no directory reads or authority."""
import copy,re,time
from .guard import Rejected
from .recipients import inspection_choice,LOOKUP_SECONDS,VERIFIED_CONTACT_SECONDS
class ContactChoices:
 def __init__(self,owner,clock=time.monotonic):self.owner=owner;self.clock=clock;self.rows=[];self.expires=0;self.selected=None;self.selected_expires=0
 def observe(self,result,started):
  rows=result.get('matches')
  if not isinstance(rows,list) or len(rows)>8:raise Rejected('bounded_contact_choices_required')
  selected=[]
  for i,row in enumerate(rows,1):
   if not isinstance(row,dict) or not re.fullmatch('[0-9a-f]{32}',row.get('selection_id','')) or not isinstance(row.get('label'),str) or not 1<=len(row['label'])<=200 or row.get('position')!=i:raise Rejected('verified_contact_choices_required')
   if row.get('phone') is not None and not re.fullmatch(r'\+[1-9][0-9]{6,14}',row['phone']):raise Rejected('contact_number_shape_required')
   selected.append(copy.deepcopy(row))
  if len({r['selection_id'] for r in selected})!=len(selected):raise Rejected('distinct_contact_handles_required')
  same=[r['selection_id'] for r in selected]==[r['selection_id'] for r in self.rows]
  expiry=started+LOOKUP_SECONDS
  self.expires=min(self.expires,expiry) if same and self.rows else expiry;self.rows=selected
  if not same:
   self.selected=selected[0]['selection_id'] if len(selected)==1 and selected[0].get('identity_verified') is True else None
   self.selected_expires=started+VERIFIED_CONTACT_SECONDS if self.selected else 0
 def snapshot(self):
  lookup_available=bool(self.rows) and self.clock()<self.expires
  remembered=next((copy.deepcopy(r) for r in self.rows if r['selection_id']==self.selected and r.get('identity_verified') is True and r.get('phone')),None)
  selected=remembered if remembered and self.clock()<self.selected_expires else None
  return dict(available=lookup_available or selected is not None,selected_verified_contact=selected,matches=copy.deepcopy(self.rows) if lookup_available else [],remembered_contact=remembered,remembered_contact_is_send_authority=False,selected_verification_seconds_remaining=max(0,int(self.selected_expires-self.clock())) if selected else 0,source='current_session_host_observed_WhatsApp_results',positions='Original displayed result order; short-lived offered rows are separate from the already chosen verified person.',selection_is_not_message_or_call_authorization=True)
 def check_inspection(self,request,selection_id,reference_text=None):
  snapshot=self.snapshot()
  if not snapshot['available']:raise Rejected('fresh_owned_recipient_selection_required')
  return inspection_choice(request,snapshot['matches'],selection_id,reference_text)
 def verified(self,result):
  if result.get('identity_verified') is not True:return
  for row in self.rows:
   if row['selection_id']==result.get('selection_id'):
    if row['label']!=result.get('label') or not re.fullmatch(r'\+[1-9][0-9]{6,14}',result.get('phone','')):raise Rejected('verified_contact_result_changed')
    already=self.selected==row['selection_id'] and row.get('identity_verified') is True
    row.update(phone=result['phone'],identity_verified=True);self.selected=row['selection_id']
    if not already:self.selected_expires=self.clock()+VERIFIED_CONTACT_SECONDS
    return
  raise Rejected('unoffered_contact_readback')
