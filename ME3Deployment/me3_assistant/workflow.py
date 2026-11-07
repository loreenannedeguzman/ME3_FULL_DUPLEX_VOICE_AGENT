"""Bounded model-selected goals; each effect keeps its own human confirmation.

Plans/results live only in session memory. Events contain step metadata, never
arguments, document text, confirmation tokens or provider credentials.
"""
import asyncio,copy,json,uuid
from jsonschema import Draft202012Validator
from me3_assistant.registry import remember_result

NAME='run_goal_steps'

def declaration(tools,actions=None):
    actions=set(tools) if actions is None else set(actions)
    choices=[{'type':'object','description':d['description'],
        'properties':{'tool':{'type':'string','enum':[name]},'arguments':copy.deepcopy(d['parameters'])},
        'required':['tool','arguments'],'additionalProperties':False} for name,d in sorted(tools.items()) if name in actions]
    # Read schemas are already advertised as ordinary functions. Avoid sending
    # them twice. Workflow still validates every nested read against its actual
    # discovered schema before ANY step runs, including earlier effects.
    reads=sorted(set(tools)-actions)
    if reads:choices.append({'type':'object','properties':{'tool':{'type':'string','enum':reads},'arguments':{'type':'object'}},'required':['tool','arguments'],'additionalProperties':False})
    return {'name':NAME,'description':
        'Run all requested steps in order,1–6. Resolve real IDs first. Controller checks permission/results. Failure stops later steps; replacement cancels pending work.',
        'parameters':{'type':'object','properties':{'steps':{'type':'array','minItems':1,'maxItems':6,
            'items':{'anyOf':choices}}},
            'required':['steps'],'additionalProperties':False}}

class Workflow:
    def __init__(self,router,steps,schemas,completed):
        if not 1<=len(steps)<=6 or len(json.dumps(steps))>12000:raise ValueError('invalid_goal_size')
        seen=set()
        for step in steps:
            name=step['tool'];args=step['arguments']
            if name not in schemas:raise ValueError('goal_tool_not_permitted')
            Draft202012Validator(schemas[name]).validate(args)
            key=json.dumps([name,args],sort_keys=True)
            if key in seen and name in router.actions.adapters:raise ValueError('duplicate_goal_action')
            seen.add(key)
        self.router=router;self.steps=copy.deepcopy(steps);self.completed=completed
        self.states=['unstarted']*len(steps);self.results=[None]*len(steps)
        self.index=0;self.waiting=None;self.status='running';self.id=uuid.uuid4().hex
        self.emit('started',None)

    def emit(self,state,g):
        self.router.events.emit('assistant_goal_progress',generation=g,goal_id=self.id,
            state=state,ordinal=self.index+1 if self.index<len(self.steps) else len(self.steps),
            tool=self.steps[self.index]['tool'] if self.index<len(self.steps) else NAME)

    def stop(self,state,g):
        if self.status in {'completed','failed','canceled'}:return
        self.status=state
        for i in range(self.index,len(self.steps)):
            if self.states[i] not in {'verified','read_received','uncertain','execution_failed'}:self.states[i]=state if i==self.index else 'not_run'
        self.waiting=None;self.emit(state,g)

    def report(self):
        return {'status':self.status,'steps':[{'number':i+1,'tool':s['tool'],'status':state,
            'verification':'independent_readback' if state=='verified' else 'tool_response_only' if state=='read_received' else 'not_verified',
            **({'result':r} if state in {'read_received','execution_failed'} else {})}
            for i,(s,state,r) in enumerate(zip(self.steps,self.states,self.results))]}

    def failure(self,g,verified_reply=''):
        self.stop('failed',g)
        count=sum(x in {'verified','read_received'} for x in self.states)
        reply=(verified_reply+' ' if verified_reply else '')+f'The goal stopped at step {self.index+1}. {count} earlier steps completed. Later steps were not run; completed actions were not undone.'
        from me3_assistant.result_delivery import deliver_results
        return deliver_results(self.router.tasks,self.completed,reply)[0]

    async def advance(self,g,verified_reply=''):
        try:
            while self.index<len(self.steps):
                if not self.router.current(g):self.stop('canceled',g);return ''
                step=self.steps[self.index];name=step['tool'];args=step['arguments']
                if name in self.router.actions.adapters:
                    self.states[self.index]='preparing';self.emit('preparing',g)
                    reply=await self.router.actions.propose(name,args,g)
                    if not self.router.current(g):self.stop('canceled',g);return ''
                    p=self.router.actions.pending
                    completed=getattr(self.router.actions,'last_completed',None)
                    if p is None and completed and completed['action']==name and completed['generation']==g and completed['standing_routine'] and self.router.actions.journal.records[completed['operation_id']]['state']=='VERIFIED':
                        self.states[self.index]='verified';self.results[self.index]={'status':'independently_verified','verified_reply':reply};self.completed.clear();self.emit('verified',g);self.index+=1;verified_reply=reply;continue
                    if p is None:
                        self.stop('failed',g)
                        return (verified_reply+' ' if verified_reply else '')+f'Goal stopped at step {self.index+1}. '+reply+' Later steps were not run.'
                    prefix=(verified_reply+' ' if verified_reply else '')+f'Step {self.index+1} of {len(self.steps)}. '
                    prompt=prefix+reply
                    if len(prompt)>650:
                        self.router.actions.cancel();return self.failure(g,verified_reply)
                    p['prompt']=prompt # The entire delivered message is required, including prior verification.
                    self.waiting=p['operation_id'];self.states[self.index]='needs_confirmation'
                    self.status='waiting_confirmation';self.emit('needs_confirmation',g)
                    return prompt
                self.states[self.index]='reading';self.emit('reading',g)
                key=json.dumps([name,args],sort_keys=True)
                if key not in self.completed:
                    result=await self.router.tasks.read(name,g,args)
                    remember_result(self.router.tasks,self.completed,name,args,result)
                if not self.router.current(g):self.stop('canceled',g);return ''
                self.results[self.index]=self.completed[key]
                if name in getattr(self.router.tasks,'workspace_operations',set()) and name.endswith('__run_workspace_command') and self.results[self.index].get('status')!='completed':
                    self.states[self.index]='execution_failed';self.stop('failed',g)
                    # Return real failure data to reasoning for correction. Do not
                    # execute later steps or call a nonzero exit a completed goal.
                    return self.report()
                self.states[self.index]='read_received';self.emit('read_received',g)
                # Bound model context; don't truncate a tool result into apparent evidence.
                if len(json.dumps(self.report()))>24000:return self.failure(g,verified_reply)
                self.index+=1
            self.status='completed';self.emit('completed',g)
            return self.report()
        except asyncio.CancelledError:
            self.stop('canceled',g);raise
        except Exception:
            return self.failure(g,verified_reply) if self.router.current(g) else ''

    async def confirmed(self,operation,g,reply):
        if operation!=self.waiting:raise ValueError('goal_confirmation_identity_changed')
        record=self.router.actions.journal.records.get(operation,{})
        if record.get('state')!='VERIFIED':
            if self.router.actions.pending is not None:return reply
            if record.get('state')=='UNCERTAIN':self.states[self.index]='uncertain'
            self.stop('failed',g);return reply+' Remaining goal steps were not run.'
        if not self.router.current(g):self.stop('canceled',g);return ''
        self.states[self.index]='verified';self.emit('verified',g)
        self.results[self.index]={'status':'independently_verified','verified_reply':reply}
        # A state-changing step makes prior read caches stale. Older results
        # remain in this chronological report, never reused as post-action proof.
        self.completed.clear()
        self.index+=1;self.waiting=None;self.status='running'
        return await self.advance(g,verified_reply=reply)
