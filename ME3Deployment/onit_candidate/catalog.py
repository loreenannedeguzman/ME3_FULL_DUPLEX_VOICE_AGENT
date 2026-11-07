"""Schema discovery only. The sole planner chooses tools; this runs no action."""
import copy
from jsonschema import Draft202012Validator
from onit_candidate.protocol import CandidateError
from onit_candidate.native_harness import NAMES as NATIVE_NAMES
NAME='load_operation_schemas'
HOST_CONTROLS=frozenset({'finish_conversation','hold_reading','resume_previous_reply',
    'check_last_action','cancel_pending_action','repeat_pending_review'})
WORKSPACE_SCHEMAS=frozenset('workspace__'+name for name in (
    'list_workspace_files','read_workspace_file','write_workspace_file',
    'run_workspace_command','list_workspace_runs','read_workspace_run',
    'workspace_capabilities','manage_workspace_runtime'))
PUBLIC_RESEARCH_SCHEMAS=frozenset('research__'+name for name in (
    'search_web','search_wikipedia','read_wikipedia_article',
    'find_weather_locations','read_current_weather',
    'list_news_sources','read_news_source'))

PUBLIC_LIBRARY_SCHEMAS=frozenset({'library__search_documents'})

PUBLIC_DAILY_SCHEMAS=frozenset({'propose_google_calendar','propose_productivity'} |
    {'gcalendar__'+name for name in ('google_calendar_context','list_google_calendar_events','read_google_calendar_event')} |
    {'productivity__'+name for name in ('productivity_context','list_tasks','read_task',
        'list_events','read_event','list_notes','read_note','search_notes','planning_context')})

def eager_schema_names(declarations):
    """Fixed schema presentation, intersected with this task's actual grant.

    This loads no schema, executes no operation, infers no intent and adds no
    declaration. Other permitted operations remain discoverable in Catalog.
    """
    return {d['name'] for d in declarations
        if d['name'] in NATIVE_NAMES|HOST_CONTROLS|WORKSPACE_SCHEMAS|PUBLIC_RESEARCH_SCHEMAS|PUBLIC_LIBRARY_SCHEMAS|PUBLIC_DAILY_SCHEMAS}

class Catalog:
    def __init__(self,declarations,*,eager=()):
        self.all={d['name']:copy.deepcopy(d) for d in declarations};self.loaded=set()
        if NAME in self.all:raise CandidateError('reserved_catalog_name')
        if not set(eager)<=set(self.all):raise CandidateError('eager_schema_not_permitted')
        self.loaded.update(eager)
    @staticmethod
    def description(declaration):
        text=declaration['description'].split('. ')[0][:140];schema=declaration['parameters']
        values=schema.get('properties',{}).get('command',{}).get('enum')
        if isinstance(values,list) and all(isinstance(x,str) for x in values):
            values=set(values)
            for branch in schema.get('allOf',[]):
                scope=branch.get('properties',{}).get('command',{}).get('enum')
                if isinstance(scope,list):values.intersection_update(scope)
            text+=' Commands: '+', '.join(sorted(values))+'.'
        return text
    def directory(self):
        description='Look up 1-4 exact operation names and load only permitted schemas. Metadata only: no execution or permission grant. Unknown names return unavailable metadata, never an alias or action. Use the supplied exact names; reply directly or clarify when the needed operation is unavailable. Available operations: '
        remaining=[(n,d) for n,d in sorted(self.all.items()) if n not in self.loaded]
        description+='; '.join(n+': '+self.description(d) for n,d in remaining) if remaining else 'All permitted operations are loaded.'
        return dict(name=NAME,description=description,parameters=dict(type='object',properties={'names':dict(type='array',items=dict(type='string',pattern='^[a-z][a-z0-9_]{0,63}$',maxLength=64),minItems=1,maxItems=4,uniqueItems=True)},required=['names'],additionalProperties=False))
    def declarations(self):return [copy.deepcopy(self.all[n]) for n in sorted(self.loaded)]+[self.directory()]
    def validate(self,calls):
        if len(calls)!=1 or calls[0].get('name')!=NAME:raise CandidateError('schema_loading_must_be_separate')
        Draft202012Validator(self.directory()['parameters']).validate(calls[0].get('args'))
    def load(self,args):
        self.validate([dict(name=NAME,args=args)])
        permitted=[name for name in args['names'] if name in self.all]
        missing=len(args['names'])-len(permitted)
        self.loaded.update(permitted)
        # Child removes this trusted transport metadata before the tool result
        # enters model history, so schemas aren't repeated in both places.
        result=dict(loaded_names=sorted(self.loaded),executed_operations=0,_schemas=self.declarations())
        if missing:
            result.update(status='some_operation_schemas_unavailable',unavailable_count=missing,
                guidance='Only exact permitted schemas were loaded. Unknown names grant no capability and were not executed. Use the supplied permitted tools or explain the limitation and clarify. Never guess an alias, retry an action or claim an outcome without verified evidence.')
        return result
