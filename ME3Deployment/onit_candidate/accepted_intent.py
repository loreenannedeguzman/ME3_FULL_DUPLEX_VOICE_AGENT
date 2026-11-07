"""Host presentation of actual Onit-selected, validated task batches. No planner."""
from dataclasses import dataclass,field

@dataclass(frozen=True)
class Intent:
    goal:str
    generation:int
    text:str
    issuer:object=field(repr=False,compare=False)

class AcceptedIntent:
    def __init__(self,engine,generation,goal,sink):
        self.engine=engine;self.generation=generation;self.goal=goal;self.sink=sink
        self.issuer=object();self.receipt=None;self.used=False
    def current(self):
        e=self.engine;gateway=e.gateway
        # Successful native cleanup sets stop before queued PCM finishes. The
        # same completed producer can retain an already-playing control only;
        # queue admission still drops any unstarted control when units exist.
        live=e.current(self.generation) or (getattr(self.sink,'terminal_kind',None)=='FULL_COMPLETE' and getattr(e,'generation',None)==self.generation)
        return (live and (gateway is None or not gateway.actions.pending and not gateway.blockers())
            and self.sink.current(self.sink.identity))
    def valid(self,receipt):
        return type(receipt) is Intent and receipt is self.receipt and receipt.issuer is self.issuer and self.current()
    def label(self,batch):
        r=self.engine.registry;operations=[]
        for item in batch:
            alias=item['name'];key=r.reads.get(alias)
            if not key or key[0] not in r.sessions:return None
            _,spec=r.sessions[key[0]]
            if spec.get('identity')!='me3' or spec.get('role')!='utility':return None
            if spec.get('module')=='me3_assistant.research_server' and key[1] in {'search_web','read_news_source'}:operations.append(('news' if key[1]=='read_news_source' else 'search',None))
            elif spec.get('module')=='me3_assistant.research_server' and key[1] in {'find_weather_locations','read_current_weather'}:operations.append(('weather',None))
            elif spec.get('module')=='me3_assistant.research_server' and key[1] in {'search_wikipedia','read_wikipedia_article'}:operations.append(('wiki',None))
            elif spec.get('module')=='me3_google_calendar.server' and key[1] in {'list_google_calendar_events','read_google_calendar_event'}:operations.append(('calendar',None))
            elif spec.get('module')=='me3_assistant.music_server' and key[1] in {'list_tracks','search_tracks','get_playback_status','get_queue','list_playlists','read_playlist'}:operations.append(('local_music',None))
            elif spec.get('module')=='me3_assistant.workspace_server' and getattr(r,'session_grant',{}).get('workspace_access') is True and key[1] in {'list_workspace_files','read_workspace_file'}:
                operations.append(('files_read',item['args'].get('path') if key[1]=='read_workspace_file' else None))
            elif spec.get('module')=='me3_assistant.workspace_server' and alias in r.workspace_operations and getattr(r,'session_grant',{}).get('workspace_access') is True and key[1] in {'write_workspace_file','run_workspace_command'}:
                operations.append(('file_write' if key[1]=='write_workspace_file' else 'command',item['args'].get('path')))
            elif spec.get('module')=='me3_assistant.workspace_server' and key[1]=='manage_workspace_runtime' and alias in r.workspace_operations and getattr(r,'session_grant',{}).get('workspace_access') is True:
                request=item['args']['request'];command=request['command']
                if command not in {'start','stop'}:return None
                operations.append((command,request.get('service_id')))
            else:return None # reviews/accounts/native context/casual chat never create a cue.
        if operations and all(x[0]=='stop' for x in operations):
            n=len(set(x[1] for x in operations))
            return "I'll try to stop those services." if n>1 else "I'll try to stop that service."
        if operations and all(x[0]=='start' for x in operations):return "I'll work on getting that running."
        if operations and all(x[0]=='search' for x in operations):return "I'll check that."
        if operations and all(x[0]=='news' for x in operations):return "I'll check the news."
        if operations and all(x[0]=='weather' for x in operations):return "I'll check the weather."
        if operations and all(x[0]=='wiki' for x in operations):return "I'll look that up."
        if operations and all(x[0]=='calendar' for x in operations):return "I'll check your calendar."
        if operations and all(x[0]=='local_music' for x in operations):return "I'll check your local music."
        if operations and all(x[0]=='files_read' for x in operations):
            return "I'll check the saved file." if len(operations)==1 and operations[0][1] is not None else "I'll check the saved files."
        if operations and all(x[0]=='file_write' for x in operations):
            return "I'll work on those files." if len(set(x[1] for x in operations))>1 else "I'll work on that file."
        if operations and all(x[0]=='command' for x in operations):return "I'll work through that task."
        return None
    async def offer(self,batch):
        # Called only after Engine's actual exact-response binding and existing
        # whole-batch validation. No model prose, user-text router or raw deltas.
        if self.used or not self.current():return False
        label=self.label(batch)
        if label is None:return False
        self.used=True;self.receipt=Intent(self.goal,self.generation,label,self.issuer)
        accepted=await self.sink.offer_task_intent(self.receipt,self.valid)
        if accepted and getattr(self.engine,'events',None) is not None:
            self.engine.events.emit('onit_task_intent_accepted',generation=self.sink.owner_generation,goal_id=self.goal,text=label,substantive_answer=False,action_authority=False,additional_model_requests=0)
        return accepted
