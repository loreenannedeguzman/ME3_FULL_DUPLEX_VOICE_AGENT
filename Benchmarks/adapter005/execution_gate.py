"""Explicit course rig reset/readiness adaptation, no shell reset command.

Each main/clean execution gets a fresh normal session. Closing a completed
session is a reset, never replay of a failed execution. Root must qualify the
real socket/launcher boundary before using this adapter for collection.
"""
import json,threading,time

def guarded_rig(base,*,source_revision,contract_sha256,maximum_executions,timeout=90):
 if type(maximum_executions) is not int or not 1<=maximum_executions<=450:raise ValueError('finite_execution_count_required')
 if not 0<timeout<=90:raise ValueError('bounded_readiness_wait_required')
 class GuardedRig(base):
  def __init__(self,*args,**kwargs):
   super().__init__(*args,**kwargs);self._me3_changed=threading.Condition();self._me3_ready=None
   self._me3_previous=None;self._me3_executions=0;self._me3_failed=False;self.me3_sessions=[];self._me3_end_sent=None;self._me3_end_ack=None
  def _handler(self,conn):
   owner=self
   class Connection:
    def __getattr__(self,name):return getattr(conn,name)
    def __iter__(self):
     for message in conn:
      marker=None
      if isinstance(message,str):
       try:marker=json.loads(message)
       except ValueError:pass
      if type(marker) is dict and marker.get('event')=='me3_ready':
       expected={'event','session','source_revision','contract_sha256','generation','physical_audio'}
       if (set(marker)!=expected or marker['source_revision']!=source_revision or marker['contract_sha256']!=contract_sha256
        or marker['generation']!=0 or marker['physical_audio'] is not False or type(marker['session']) is not str
        or len(marker['session'])!=32 or any(c not in '0123456789abcdef' for c in marker['session'])):
        owner._me3_failed=True;conn.close(1008,'ME3 readiness binding failed');raise ValueError('source_bound_readiness_marker_required')
       with owner._me3_changed:
        if owner._me3_ready is not None:raise ValueError('duplicate_me3_ready')
        owner._me3_ready=(self,marker['session']);owner._me3_changed.notify_all()
       continue
      if type(marker) is dict and marker.get('event')=='me3_end_session_ack':
       with owner._me3_changed:
        ready=owner._me3_ready
        if (set(marker)!={'event','session','source_revision','contract_sha256','writes_fenced'}
         or marker['source_revision']!=source_revision or marker['contract_sha256']!=contract_sha256
         or marker['writes_fenced'] is not True or ready is None or ready[0] is not self
         or marker['session']!=ready[1] or marker['session']!=owner._me3_end_sent or owner._me3_end_ack is not None):
         owner._me3_failed=True;conn.close(1008,'ME3 end acknowledgment binding failed');raise ValueError('exact_requested_end_ack_required')
        owner._me3_end_ack=(self,marker['session']);owner._me3_changed.notify_all()
       continue
      yield message
   proxy=Connection()
   try:super()._handler(proxy)
   finally:
    with self._me3_changed:
     if self._me3_ready is not None and self._me3_ready[0] is proxy:
      if self._me3_end_ack!=(proxy,self._me3_ready[1]):self._me3_failed=True
      self._me3_ready=None
     self._me3_changed.notify_all()
  def end_session(self):
   """Only a completed trial may end its exact bound assistant session."""
   with self._sess_lock:
    if self._sess is not None:raise ValueError('active_trial_cannot_be_reset')
   with self._me3_changed:
    connection=self.conn;ready=self._me3_ready
    if self._me3_failed or ready is None or ready[0] is not connection or ready[1]!=self._me3_previous or self._me3_end_sent is not None:raise ValueError('exact_completed_unused_session_required')
    self._me3_end_sent=ready[1]
    request=dict(event='me3_end_session',session=ready[1],source_revision=source_revision,contract_sha256=contract_sha256)
   try:
    connection.send(json.dumps(request,separators=(',',':')))
    deadline=time.monotonic()+timeout
    with self._me3_changed:
     while self._me3_end_ack!=ready or self.conn is connection:
      left=deadline-time.monotonic()
      if self._me3_failed or left<=0:raise ValueError('peer_end_ack_and_disconnect_unverified')
      self._me3_changed.wait(left)
   except BaseException:self._me3_failed=True;raise
  def run(self,script):
   if self._me3_failed or self._me3_executions>=maximum_executions:raise ValueError('benchmark_stopped_no_retry_or_extra_execution')
   try:
    if self._me3_previous is not None:
     self.end_session()
    deadline=time.monotonic()+timeout
    with self._me3_changed:
     while (self._me3_ready is None or self._me3_ready[1]==self._me3_previous or self.conn is not self._me3_ready[0]):
      left=deadline-time.monotonic()
      if self._me3_failed or left<=0:raise ValueError('fresh_greeting_tail_listening_barrier_unverified')
      self._me3_changed.wait(left)
     connection,identity=self._me3_ready
    self.playout.clear() # Remove only the completed greeting, before recording.
    self._me3_end_sent=None;self._me3_end_ack=None
    self._me3_previous=identity;self._me3_executions+=1;self.me3_sessions.append(identity)
    return super().run(script)
   except BaseException:self._me3_failed=True;raise
  def close(self):
   try:
    if self.conn is not None and not self._me3_failed:self.end_session()
   finally:super().close()
 return GuardedRig

def stop_on_first_error(runner_class):
 """Preserve the original failed trial and all remaining pending queue items."""
 original=runner_class._run_trial
 def run(self,plan,row,table):
  result=original(self,plan,row,table)
  if result.get('error'):
   for item in self.plan.items:
    if item.group==plan.group and item.clip_id==plan.clip.id and plan.trial_id.startswith('r%02d_'%item.round):item.status='failed'
   self._save_plan();self.log.event('me3_stop_first_error',trial=plan.trial_id,automatic_retry=False)
   raise RuntimeError('benchmark_first_error_retained_remaining_unstarted')
  return result
 if getattr(original,'_me3_no_retry',False):raise ValueError('one_stop_guard_required')
 run._me3_no_retry=True;runner_class._run_trial=run
