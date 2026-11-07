import copy,itertools,unittest
from jsonschema import Draft202012Validator
from me3_assistant.schema_wire import schema,declarations

class ProjectionTests(unittest.TestCase):
 def test_property_and_literal_names_are_data_not_annotation_keywords(self):
  original={'type':'object','title':'irrelevant','properties':{'title':{'type':'string','default':'x'},'default':{'const':{'title':'keep','default':3}},'examples':{'enum':[{'title':'keep'}]}},'required':['title'],'additionalProperties':False}
  before=copy.deepcopy(original);projected=schema(original)
  self.assertEqual(original,before);self.assertNotIn('title',projected);self.assertIn('title',projected['properties']);self.assertEqual(projected['properties']['default']['const'],{'title':'keep','default':3})
  a,b=Draft202012Validator(original),Draft202012Validator(projected)
  for value in [{},{'title':'a'},{'title':3},{'title':'a','default':{'title':'keep','default':3}},{'title':'a','default':3},{'title':'a','unknown':True}]:self.assertEqual(a.is_valid(value),b.is_valid(value))
 def test_nested_schema_positions_preserve_constraints_and_definitions(self):
  original={'$defs':{'default':{'type':'string','minLength':2,'title':'hint'}},'anyOf':[{'$ref':'#/$defs/default'},{'type':'array','items':{'type':'integer','default':2},'minItems':1,'maxItems':2}],'description':'semantic explanation retained'}
  projected=schema(original);self.assertEqual(projected['description'],original['description']);self.assertIn('default',projected['$defs'])
  for value in ['','ab',True,None,[],[1],[1,2],[1,2,3],['1']]:self.assertEqual(Draft202012Validator(original).is_valid(value),Draft202012Validator(projected).is_valid(value))
 def test_declarations_do_not_change_names_descriptions_or_original(self):
  tools=[{'name':'read','description':'read only','parameters':{'type':'object','title':'x','properties':{'title':{'type':'string'}}}}];old=copy.deepcopy(tools);new=declarations(tools)
  self.assertEqual(tools,old);self.assertEqual(new[0]['description'],'read only');self.assertIn('title',new[0]['parameters']['properties']);self.assertNotIn('title',new[0]['parameters'])
