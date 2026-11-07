"""Turn-local, read-only schema discovery. Loading a schema cannot run its operation."""
import copy

NAME='load_operation_schemas'

class Selection:
    def __init__(self,tools):
        self.tools={d['name']:copy.deepcopy(d) for d in tools};self.loaded=set()
        if NAME in self.tools:raise ValueError('reserved_schema_loader_name')
    def directory(self):
        base=('Load1–4 permitted schemas before calling them; no operation executes. '
              'Select prerequisites together. run_goal_steps reveals authorized action schemas. '
              'Reply directly for conversation. ')
        if not self.loaded:
            base+='Available operation directory (summaries only): '+ '; '.join(
                name+': '+tool['description'].split('. ')[0][:180] for name,tool in sorted(self.tools.items()))
        else:base='Load other permitted schemas before calling them. Metadata only; no execution.'
        return {'name':NAME,'description':base,'parameters':{'type':'object','properties':{'names':{'type':'array','items':{'type':'string','enum':sorted(self.tools)},'minItems':1,'maxItems':4,'uniqueItems':True}},'required':['names'],'additionalProperties':False}}
    def declarations(self):
        return [copy.deepcopy(self.tools[n]) for n in sorted(self.loaded)]+[self.directory()]
    def load(self,names):
        if (not isinstance(names,list) or not 1<=len(names)<=4
            or any(not isinstance(n,str) or n not in self.tools for n in names)
            or len(set(names))!=len(names)):raise ValueError('operation_schema_not_permitted')
        self.loaded.update(names)
        # Parameters appear exactly once in the next permitted_operations.
        # The observation only acknowledges discovery, never execution/access.
        return {'schemas_available':sorted(self.loaded),'executed_operations':0,'source_content_obtained':False}
