"""Remove non-validating JSON Schema annotations only at schema locations."""
import copy

ANNOTATIONS={'title','default','examples','$comment'}
MAPS={'properties','patternProperties','$defs','definitions','dependentSchemas'}
SINGLE={'additionalProperties','additionalItems','contains','propertyNames','not','if','then','else','unevaluatedProperties','unevaluatedItems','contentSchema'}
ARRAY={'allOf','anyOf','oneOf','prefixItems'}

def schema(value):
    if not isinstance(value,dict):return copy.deepcopy(value)
    result={}
    for key,item in value.items():
        if key in ANNOTATIONS:continue
        if key in MAPS and isinstance(item,dict):result[key]={name:schema(child) for name,child in item.items()}
        elif key in SINGLE:result[key]=schema(item)
        elif key in ARRAY and isinstance(item,list):result[key]=[schema(child) for child in item]
        elif key=='items':result[key]=[schema(child) for child in item] if isinstance(item,list) else schema(item)
        else:result[key]=copy.deepcopy(item)
    return result

def declarations(tools):
    return [{**copy.deepcopy(tool),'parameters':schema(tool['parameters'])} for tool in tools]
