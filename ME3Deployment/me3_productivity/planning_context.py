"""One bounded read transaction of saved planning facts, never a planner.

No priorities, free-time slots, schedules or completion claims are invented.
Onit remains responsible for explaining/suggesting a plan from these facts.
"""
import json
from datetime import date,datetime,time,timedelta,timezone
from .store import DEFAULT_TIMEZONE,READ_SOURCE,date_only,zone,page_args

MAX_INPUT_RECORDS=1000
MAX_RESULT_BYTES=10500
CANDIDATES='''(kind='task' AND state='open') OR (kind='note' AND state='active')
 OR (kind='event' AND state='scheduled'
 AND julianday(start_at)<julianday(?) AND julianday(end_at)>julianday(?))'''
SQL='''SELECT id,kind,revision,state,title,substr(body,1,120) AS body_excerpt,
 length(body) AS body_characters,due_date,due_at,start_at,end_at,timezone,created_at,updated_at
 FROM records WHERE '''+CANDIDATES+''' ORDER BY kind,id LIMIT ?'''

def _stamp(value):
    if not isinstance(value,str):raise ValueError('saved_planning_instant_required')
    try:d=datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError:raise ValueError('saved_planning_instant_required') from None
    if d.tzinfo is None or d.utcoffset() is None:raise ValueError('saved_planning_instant_required')
    return d.astimezone(timezone.utc)

def planning_context(store,day,timezone_name=DEFAULT_TIMEZONE,limit=12,offset=0):
    page_args(limit,offset);day=date_only(day);tz=zone(timezone_name);local_date=date.fromisoformat(day)
    try:start=datetime.combine(local_date,time.min,tzinfo=tz);end=datetime.combine(local_date+timedelta(days=1),time.min,tzinfo=tz)
    except OverflowError:raise ValueError('bounded_planning_calendar_day_required') from None
    start_utc=start.astimezone(timezone.utc);end_utc=end.astimezone(timezone.utc)
    if end_utc<=start_utc:raise ValueError('nonexistent_local_calendar_day')
    # Midnight can be skipped by an IANA transition. Keep the correct UTC
    # boundary and display its real local instant, never a nonexistent time.
    start=start_utc.astimezone(tz);end=end_utc.astimezone(tz)
    with store.connect(read_only=True) as connection:
        connection.execute('BEGIN')
        groups=connection.execute('SELECT kind,state,count(*) AS n FROM records GROUP BY kind,state').fetchall()
        # Apply the requested day BEFORE the bounded scan. Otherwise unrelated
        # appointments can occupy the entire input window and hide today's
        # calendar/tasks. SQLite normalizes saved explicit offsets to UTC.
        invalid=connection.execute("SELECT 1 FROM records WHERE kind='event' AND state='scheduled' AND (julianday(start_at) IS NULL OR julianday(end_at) IS NULL OR julianday(end_at)<=julianday(start_at)) LIMIT 1").fetchone()
        if invalid is not None:raise ValueError('invalid_saved_event_interval')
        bounds=(end_utc.isoformat(),start_utc.isoformat())
        candidates=connection.execute('SELECT count(*) FROM records WHERE '+CANDIDATES,bounds).fetchone()[0]
        raw=[dict(row) for row in connection.execute(SQL,(*bounds,MAX_INPUT_RECORDS)).fetchall()]
    counts={kind:{} for kind in ('task','event','note')}
    for row in groups:
        if row['kind'] not in counts:raise ValueError('unknown_saved_productivity_kind')
        counts[row['kind']][row['state']]=row['n']
    available=sum(counts[k].get(s,0) for k,s in [('task','open'),('event','scheduled'),('note','active')])
    entries=[]
    for row in raw:
        item=dict(row);item['body_excerpt_complete']=len(item['body_excerpt'])==item['body_characters']
        item['content_is_untrusted_reference']=True
        if row['kind']=='event':
            a=_stamp(row['start_at']);b=_stamp(row['end_at'])
            if b<=a:raise ValueError('invalid_saved_event_interval')
            if not (a<end_utc and b>start_utc):continue
            item['classification']='scheduled_event_overlapping_day';sort=(0,a.isoformat())
        elif row['kind']=='task':
            if row['due_at']:
                due=_stamp(row['due_at'])
                item['classification']='overdue_instant' if due<start_utc else 'due_today_instant' if due<end_utc else 'future_open_task'
                sort=(1 if due<end_utc else 3,due.isoformat())
            elif row['due_date']:
                due=date_only(row['due_date'])
                item['classification']='overdue_date' if due<day else 'due_today_date' if due==day else 'future_open_task'
                sort=(1 if due<=day else 3,due)
            else:item['classification']='undated_open_task';sort=(2,'')
        elif row['kind']=='note':item['classification']='active_note_reference';sort=(4,'')
        else:raise ValueError('unknown_saved_productivity_kind')
        entries.append(((*sort,item['title'].casefold(),item['id']),item))
    entries=[item for _,item in sorted(entries,key=lambda pair:pair[0])]
    page=entries[offset:offset+limit]
    result=dict(ok=True,read_only=True,verification_source=READ_SOURCE,snapshot_consistency='one_fresh_read_transaction',day=day,timezone=timezone_name,day_start=start.isoformat(),day_end=end.isoformat(),
        record_state_counts=counts,entries=page,total_matching_examined=len(entries),next_offset=offset+len(page) if offset+len(page)<len(entries) else None,
        active_records_available=available,active_records_examined=len(raw),candidate_records_available=candidates,events_outside_day_excluded=available-candidates,input_record_limit=MAX_INPUT_RECORDS,input_truncated=candidates>len(raw),
        excerpt_limit=120,content_is_untrusted_reference=True,mutations_started=False,plan_generated=False,result_size_bound_applied=False,
        interpretation='Saved local records only. Date-only task deadlines have no invented clock time; undated tasks are not due today by implication. Event overlap uses a half-open local calendar day, including overnight events. Off-day calendar events are excluded before the input cap; input_truncated concerns only day-relevant events, open tasks and active notes. State counts are global saved-record counts, not per-project completion proof. Notes/details are excerpts; fetch exact current IDs/revisions with read_note/read_task/read_event before treating omitted content as known. Suggestions are not saved plans. No external calendar, reminder delivery, email, account or background activity is implied.')
    while len(json.dumps(result,ensure_ascii=False).encode('utf-8'))>MAX_RESULT_BYTES and page:
        page.pop();result['next_offset']=offset+len(page) if offset+len(page)<len(entries) else None
    if not page and entries[offset:]:raise ValueError('bounded_planning_result_required')
    result['result_size_bound_applied']=len(page)<min(limit,max(0,len(entries)-offset))
    return result
