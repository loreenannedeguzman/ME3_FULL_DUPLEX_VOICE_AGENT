"""Pure bounded event projections. Calendar content is untrusted data."""
import hashlib,json,re
from datetime import date,datetime
from me3_productivity.store import instant,zone,date_only

ACCOUNT='owner@example.invalid'
CALENDAR='primary'
FIELDS={'summary','description','location','start_at','end_at','timezone'}

class CalendarError(ValueError):
    def __init__(self,code,http_status=None):
        self.code=code;self.http_status=http_status;super().__init__(code)

def need(value,code='google_calendar_contract_rejected'):
    if not value:raise CalendarError(code)

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()
def opaque(value):
    need(isinstance(value,str) and re.fullmatch('[0-9a-f]{32}',value),'opaque_calendar_identity_required');return value
def event_id(value):
    need(isinstance(value,str) and re.fullmatch('[A-Za-z0-9_-]{5,1024}',value),'exact_calendar_event_id_required');return value
def etag(value):
    need(isinstance(value,str) and 3<=len(value)<=256 and value.startswith('"') and value.endswith('"') and all(32<=ord(c)<127 for c in value),'exact_current_calendar_etag_required');return value
def text(value,limit,empty=True):
    need(isinstance(value,str) and len(value)<=limit and (empty or bool(value.strip())) and all(ord(c)>=32 or c in '\n\t' for c in value),'bounded_calendar_text_required');return value
def stamp(value,tz):
    try:return instant(value,tz)
    except (ValueError,TypeError):raise CalendarError('explicit_calendar_offset_and_timezone_required') from None

def timepart(value):
    need(isinstance(value,dict),'calendar_time_invalid')
    if 'date' in value:
        need(set(value)<={'date','timeZone'},'calendar_date_invalid')
        try:return {'date':date_only(value['date'])}
        except ValueError:raise CalendarError('calendar_date_invalid') from None
    need(set(value)<={'dateTime','timeZone'} and isinstance(value.get('dateTime'),str),'calendar_time_invalid')
    try:
        at=datetime.fromisoformat(value['dateTime'].replace('Z','+00:00'))
        need(at.tzinfo is not None and at.utcoffset() is not None,'calendar_time_invalid')
        result={}
        if value.get('timeZone') is not None:at=at.astimezone(zone(value['timeZone']));result['timeZone']=value['timeZone']
        result['dateTime']=at.isoformat(timespec='microseconds' if at.microsecond else 'seconds')
        return result
    except (ValueError,TypeError):raise CalendarError('calendar_time_invalid') from None

def project(value):
    need(isinstance(value,dict) and len(canonical(value).encode())<=131072,'bounded_calendar_event_required')
    eid=event_id(value.get('id'));tag=etag(value.get('etag'))
    status=value.get('status','confirmed');need(status in {'confirmed','tentative','cancelled'},'calendar_status_invalid')
    private=value.get('extendedProperties',{}).get('private',{}) if isinstance(value.get('extendedProperties',{}),dict) else None
    need(isinstance(private,dict) and len(private)<=40 and all(isinstance(k,str) and isinstance(v,str) and len(k)<=44 and len(v)<=1024 for k,v in private.items()),'calendar_private_properties_invalid')
    reminders=value.get('reminders',{'useDefault':True})
    need(isinstance(reminders,dict) and not set(reminders)-{'useDefault','overrides'} and type(reminders.get('useDefault',False)) is bool,'calendar_reminder_metadata_invalid')
    overrides=reminders.get('overrides',[])
    need(isinstance(overrides,list) and len(overrides)<=5 and all(isinstance(r,dict) and set(r)=={'method','minutes'} and r['method'] in {'email','popup'} and type(r['minutes']) is int and 0<=r['minutes']<=40320 for r in overrides),'calendar_reminder_metadata_invalid')
    reminders={'useDefault':reminders.get('useDefault',False),'overrides':overrides}
    organizer=value.get('organizer',{});need(isinstance(organizer,dict),'calendar_organizer_invalid')
    email=organizer.get('email');need(email is None or isinstance(email,str) and len(email)<=320,'calendar_organizer_invalid')
    own=organizer.get('self');need(own is None or type(own) is bool,'calendar_organizer_invalid')
    result=dict(id=eid,etag=tag,status=status,summary=text(value.get('summary',''),200),description=text(value.get('description',''),6000),location=text(value.get('location',''),1000),start=timepart(value['start']) if 'start' in value else None,end=timepart(value['end']) if 'end' in value else None,recurring=bool(value.get('recurrence') or value.get('recurringEventId') or value.get('originalStartTime')),has_attendees=bool(value.get('attendees') or value.get('attendeesOmitted')),has_conference=bool(value.get('conferenceData') or value.get('hangoutLink')),event_type=value.get('eventType','default'),private=private,reminders=reminders,organizer_email=email,organizer_self=own)
    need(isinstance(result['event_type'],str) and len(result['event_type'])<=50,'calendar_event_type_invalid')
    return result

def owned(snapshot):
    need(snapshot.get('organizer_email') in {None,ACCOUNT} and (snapshot.get('organizer_self') is True or snapshot.get('organizer_email')==ACCOUNT and snapshot.get('organizer_self') is None),'only_account_organized_calendar_events_mutable')

def basic(snapshot):
    owned(snapshot)
    need(snapshot['status'] in {'confirmed','tentative'} and not snapshot['recurring'] and not snapshot['has_attendees'] and not snapshot['has_conference'] and snapshot['event_type']=='default','only_basic_nonrecurring_no_invite_events_mutable')
    need(all(isinstance(snapshot[k],dict) and 'dateTime' in snapshot[k] for k in ('start','end')),'all_day_or_missing_time_mutation_not_supported')

def edits(data,before=None):
    need(isinstance(data,dict) and not set(data)-FIELDS and (before is None or bool(data)),'explicit_supported_calendar_fields_required')
    base={} if before is None else {k:before[k] for k in ('summary','description','location')}
    if before is None:need({'summary','start_at','end_at','timezone'}<=set(data),'explicit_calendar_summary_start_end_timezone_required')
    for key,limit in (('summary',200),('description',6000),('location',1000)):
        base[key]=text(data.get(key,base.get(key,'')),limit,empty=key!='summary')
    tz=data.get('timezone') or (before['start'].get('timeZone') if before else None)
    need(isinstance(tz,str),'explicit_calendar_timezone_required')
    try:zone(tz)
    except ValueError:raise CalendarError('explicit_calendar_timezone_required') from None
    def at(key,part):
        if key in data:return stamp(data[key],tz),tz
        previous=before[part];part_tz=data.get('timezone') or previous.get('timeZone') or tz
        # Google may represent an inherited instant as UTC while naming an
        # IANA display zone. Normalize that known instant; explicit user inputs
        # still pass strict offset/zone validation without reinterpretation.
        value=datetime.fromisoformat(previous['dateTime'].replace('Z','+00:00')).astimezone(zone(part_tz)).isoformat()
        return stamp(value,part_tz),part_tz
    (a,ta),start_tz=at('start_at','start');(b,tb),end_tz=at('end_at','end')
    need(0<tb-ta<=366*86400,'bounded_calendar_event_range_required')
    base.update(start={'dateTime':a,'timeZone':start_tz},end={'dateTime':b,'timeZone':end_tz},status='confirmed' if before is None else before['status'],reminders={'useDefault':False,'overrides':[]} if before is None else before['reminders'])
    return base

def expected_projection(body):
    # etag is a server version and is deliberately not predicted.
    return {k:body[k] for k in ('id','summary','description','location','start','end','status','extendedProperties','reminders')}

def observed_projection(snapshot):
    return {k:snapshot[k] for k in ('id','summary','description','location','start','end','status','reminders')}|{'extendedProperties':{'private':snapshot['private']}}

def make_intent(operation_id,command,data,before=None):
    opaque(operation_id);need(command in {'create','update','cancel'},'supported_calendar_command_required')
    if command=='create':need(before is None,'create_has_no_existing_target');target=hashlib.sha256(('me3-calendar:'+operation_id).encode()).hexdigest();after=edits(data)
    else:
        need(isinstance(before,dict),'fresh_calendar_snapshot_required');basic(before);target=before['id']
        if command=='cancel':need(data=={},'calendar_cancel_has_no_edit_fields');after={k:before[k] for k in ('summary','description','location','start','end','reminders')}|{'status':'cancelled'}
        else:after=edits(data,before)
    after['id']=target
    intent=dict(channel='google_calendar',account=ACCOUNT,calendar=CALENDAR,command=command,target_id=target,expected_etag=before['etag'] if before else None,before=before,after=after)
    # Marker digest excludes the marker itself; it binds the reviewed semantic
    # snapshot. Other private properties survive a patch unchanged.
    marker=digest(intent);private=dict(before['private']) if before else {}
    private.update(me3_operation=operation_id,me3_intent=marker)
    after['extendedProperties']={'private':private}
    need(len(canonical(intent).encode())<=24000,'calendar_intent_bound')
    return intent
