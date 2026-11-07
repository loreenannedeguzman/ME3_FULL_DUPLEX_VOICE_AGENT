"""Exact-reviewed writes and fresh, independently requested read-back. No replay."""
from .domain import CalendarError,need,opaque,etag,make_intent,digest,expected_projection,observed_projection,basic,owned

class Service:
    def __init__(self,client,journal):self.client=client;self.journal=journal
    def prepare(self,operation_id,owner,command,data=None,event_id=None,expected_etag=None,*,authorize):
        opaque(operation_id);opaque(owner);need(command in {'create','update','cancel'},'supported_calendar_command_required')
        need(data is None or isinstance(data,dict) and not set(data)-{'summary','description','location','start_at','end_at','timezone'},'explicit_supported_calendar_fields_required')
        if command=='cancel':need(data is None or data=={},'calendar_cancel_has_no_edit_fields')
        need(callable(authorize) and authorize() is True,'current_owner_calendar_authorization_required');before=None
        if command=='create':need(event_id is None and expected_etag is None,'new_calendar_event_has_no_target')
        else:
            etag(expected_etag);before=self.client.read_event(event_id,authorize=authorize)
            need(before['etag']==expected_etag,'stale_calendar_etag_prepare')
        intent=make_intent(operation_id,command,{} if data is None else data,before)
        need(authorize() is True,'current_owner_calendar_authorization_required')
        return self.journal.prepare(operation_id,owner,intent)
    def commit(self,operation_id,owner,token,expected_digest,confirmation,*,authorize):
        need(callable(authorize) and authorize() is True,'current_owner_calendar_authorization_required')
        intent=self.journal.claim(operation_id,owner,token,expected_digest,confirmation)
        try:
            # claim is committed/fsynced before credential or HTTP activity.
            self.client._write(intent,authorize=authorize)
        except BaseException as error:
            state='REJECTED_ETAG' if isinstance(error,CalendarError) and error.http_status==412 else 'UNCERTAIN'
            self.journal.outcome(operation_id,owner,expected_digest,state)
            if isinstance(error,(KeyboardInterrupt,SystemExit)):raise
            return dict(operation_id=operation_id,expected_digest=expected_digest,state=state,write_verified=False,automatic_retry=False)
        self.journal.outcome(operation_id,owner,expected_digest,'WRITE_ACKNOWLEDGED')
        return dict(operation_id=operation_id,expected_digest=expected_digest,state='WRITE_ACKNOWLEDGED',write_verified=False,automatic_retry=False)
    def verify(self,operation_id,owner,expected_digest,*,authorize):
        operation=self.journal.read_operation(operation_id,owner)
        need(operation['expected_digest']==expected_digest and operation['state'] in {'DISPATCHED','UNCERTAIN','WRITE_ACKNOWLEDGED','VERIFIED'},'claimed_calendar_operation_required_for_fresh_verify')
        intent=operation['intent'];current=self.client.read_event(intent['target_id'],authorize=authorize)
        owned(current)
        need(not current['recurring'] and not current['has_attendees'] and not current['has_conference'] and current['event_type']=='default','calendar_current_scope_changed')
        need(observed_projection(current)==expected_projection(intent['after']),'calendar_fresh_readback_mismatch_no_retry')
        receipt=dict(operation_id=operation_id,expected_digest=expected_digest,target_id=intent['target_id'],current_etag=current['etag'],current_after_digest=digest(observed_projection(current)),verified_after_digest=digest(expected_projection(intent['after'])),verification_source='fresh_google_calendar_HTTP_exact_operation_snapshot',read_only=True,write_verified=True,content_is_untrusted=True,automatic_retry=False)
        self.journal.outcome(operation_id,owner,expected_digest,'VERIFIED',receipt)
        return receipt
    def read_operation(self,operation_id,owner):return self.journal.read_operation(operation_id,owner)
