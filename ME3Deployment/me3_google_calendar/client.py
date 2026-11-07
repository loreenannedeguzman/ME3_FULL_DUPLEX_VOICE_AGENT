"""Injected synchronous HTTPS transport; never selects account/calendar/URL from tools.

request(method,path,query,json,headers) must join ONLY ORIGIN, prohibit redirects,
bound timeout/response bytes, and make exactly one HTTP attempt. No default HTTP
implementation or credential-file reader is supplied by this backend.
"""
import re
from .domain import ACCOUNT,CalendarError,need,event_id,project,stamp,text,etag

ORIGIN='https://www.googleapis.com'
EVENTS='/calendar/v3/calendars/primary/events'

class CalendarClient:
    def __init__(self,request,credentials):
        need(callable(request) and callable(credentials),'injected_calendar_HTTP_and_verified_credentials_required')
        self._request=request;self._credentials=credentials
    def _call(self,method,eid=None,*,query=None,body=None,match=None,authorize):
        need(callable(authorize) and authorize() is True,'current_owner_calendar_authorization_required')
        path=EVENTS+('/'+event_id(eid) if eid is not None else '')
        try:
            bridge=self._credentials()
            need(isinstance(bridge,dict) and set(bridge)=={'account_email','headers'} and bridge['account_email']==ACCOUNT,'fixed_calendar_account_required')
            headers=bridge['headers'];need(isinstance(headers,dict) and set(headers)=={'Authorization'},'private_calendar_bearer_required')
            token=headers.get('Authorization');need(isinstance(token,str) and re.fullmatch(r'Bearer [\x21-\x7e]{1,8192}',token),'private_calendar_bearer_required')
            headers={**headers,'Accept':'application/json'}
            if match is not None:headers['If-Match']=etag(match)
            need(authorize() is True,'current_owner_calendar_authorization_required')
            response=self._request(method,path,dict(query or {}),body,headers)
        except CalendarError:raise
        except BaseException as error:
            if isinstance(error,(KeyboardInterrupt,SystemExit)):raise
            raise CalendarError('calendar_transport_unverified_no_retry') from None
        need(isinstance(response,dict) and set(response)=={'status','body'} and type(response['status']) is int,'calendar_HTTP_contract_invalid')
        if response['status'] not in {200,201}:raise CalendarError('calendar_HTTP_rejected_no_retry',http_status=response['status'])
        need(authorize() is True,'current_owner_calendar_authorization_required')
        need(isinstance(response['body'],dict),'calendar_HTTP_body_invalid')
        return response['body']
    def list_events(self,time_min,time_max,timezone,limit=20,page_token=None,*,authorize):
        lo,a=stamp(time_min,timezone);hi,b=stamp(time_max,timezone)
        need(0<b-a<=366*86400 and type(limit) is int and 1<=limit<=25,'bounded_calendar_list_required')
        query=dict(timeMin=lo,timeMax=hi,timeZone=timezone,maxResults=limit,singleEvents='true',orderBy='startTime',showDeleted='false')
        if page_token is not None:query['pageToken']=text(page_token,2048,empty=False)
        value=self._call('GET',query=query,authorize=authorize)
        rows=value.get('items',[]);need(isinstance(rows,list) and len(rows)<=limit,'bounded_calendar_page_required')
        token=value.get('nextPageToken');need(token is None or isinstance(token,str) and 0<len(token)<=2048,'calendar_page_token_invalid')
        brief=[]
        for value in rows:
            snapshot=project(value)
            brief.append({k:snapshot[k] for k in ('id','etag','status','summary','start','end','recurring','has_attendees','has_conference','event_type')}|{'details_omitted':True})
        return dict(events=brief,next_page_token=token,details_omitted=True,full_details_require='read_google_calendar_event',read_only=True,content_is_untrusted=True,calendar='primary',account=ACCOUNT,verification_source='fresh_google_calendar_HTTP')
    def read_event(self,event,*,authorize):
        value=project(self._call('GET',event,authorize=authorize));need(value['id']==event,'calendar_response_identity_mismatch');return value
    def _write(self,intent,*,authorize):
        body=dict(intent['after']);query={'sendUpdates':'none','conferenceDataVersion':0}
        if intent['command']=='create':return self._call('POST',query=query,body=body,authorize=authorize)
        body.pop('id')
        return self._call('PATCH',intent['target_id'],query=query,body=body,match=intent['expected_etag'],authorize=authorize)
