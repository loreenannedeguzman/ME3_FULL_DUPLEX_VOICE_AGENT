"""Native metadata loading cannot execute an operation or widen permissions."""
import copy,sys,tempfile,unittest
from pathlib import Path
from task_mcp.client import ROOT
from onit_candidate.catalog import Catalog,NAME
from onit_candidate.protocol import CandidateError
from onit_candidate.checks import DECL,FixtureRegistry,FixtureTransport,Events,Budget,response,native
from onit_candidate.engine import Engine
class CatalogChecks(unittest.TestCase):
    def test_loading_only_reveals_exact_discovered_schema(self):
        catalog=Catalog([DECL]);self.assertEqual([d['name'] for d in catalog.declarations()],[NAME])
        result=catalog.load({'names':[DECL['name']]})
        self.assertEqual(result['executed_operations'],0);self.assertIn(DECL,result['_schemas'])
        self.assertEqual(DECL['parameters'],next(d['parameters'] for d in result['_schemas'] if d['name']==DECL['name']))
    def test_unknown_private_names_are_unavailable_metadata_never_loaded(self):
        for names in [['commit_item'],['propose_ungranted_action'],['soundcloud_volume']]:
            catalog=Catalog([DECL]);result=catalog.load({'names':names})
            self.assertEqual(result['status'],'some_operation_schemas_unavailable')
            self.assertEqual(result['unavailable_count'],1);self.assertEqual(result['executed_operations'],0)
            self.assertEqual(catalog.loaded,set());self.assertEqual([d['name'] for d in result['_schemas']],[NAME])
    def test_mixed_lookup_loads_only_exact_permitted_name_without_aliasing(self):
        catalog=Catalog([DECL]);result=catalog.load({'names':[DECL['name'],'commit_item']})
        self.assertEqual(catalog.loaded,{DECL['name']});self.assertEqual(result['unavailable_count'],1)
        self.assertEqual(result['executed_operations'],0);self.assertEqual(set(catalog.all),{DECL['name']})
    def test_duplicate_malformed_oversized_and_private_argument_shapes_fail_closed(self):
        for names in [[DECL['name'],DECL['name']],['../commit_item'],['a'*65],['Bad Name'],[],['tool_'+str(i) for i in range(5)]]:
            catalog=Catalog([DECL])
            with self.assertRaises(Exception):catalog.load({'names':names})
            self.assertEqual(catalog.loaded,set())
    def test_schema_load_mixed_with_an_operation_is_rejected_as_whole_batch(self):
        catalog=Catalog([DECL])
        with self.assertRaises(CandidateError):catalog.validate([dict(name=NAME,args={'names':[DECL['name']]}),dict(name=DECL['name'],args={'value':7})])
        self.assertEqual(catalog.loaded,set())
@unittest.skipUnless(sys.platform=='linux','Pinned native Onit/Pi dependency environment required.')
class NativeCatalogChecks(unittest.IsolatedAsyncioTestCase):
    async def test_original_loop_handles_unknown_metadata_then_completes_without_effect(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='onit-catalog-missing-') as tmp:
            folder=Path(tmp);r=FixtureRegistry();budget=Budget()
            t=FixtureTransport(response(calls=[native(NAME,{'names':['soundcloud_volume','commit_item']})]),response('That operation is unavailable here. Please choose a supported player volume.'))
            e=Engine(ROOT,r,t,budget,Events());result=await e.answer('Look up the available operation before responding.',folder/'work',folder/'evidence',lazy_tools=True)
            self.assertEqual(result['answer'],'That operation is unavailable here. Please choose a supported player volume.')
            self.assertEqual((r.calls,budget.used,t.calls),(0,2,2));self.assertEqual(result['observations'][0]['result']['executed_operations'],0)
            self.assertIsNone(e.task)
    async def test_unavailable_metadata_does_not_authorize_a_later_private_call(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='onit-catalog-private-') as tmp:
            folder=Path(tmp);r=FixtureRegistry();budget=Budget()
            t=FixtureTransport(response(calls=[native(NAME,{'names':['commit_item']})]),response(calls=[native('commit_item',{'value':7})]))
            e=Engine(ROOT,r,t,budget,Events())
            with self.assertRaises(CandidateError):await e.answer('Inspect capability metadata.',folder/'work',folder/'evidence',lazy_tools=True)
            self.assertEqual(r.calls,0);self.assertIsNone(e.task)
    async def test_original_loop_loads_schema_then_calls_real_fixture_read_with_dynamic_native_tools(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='onit-catalog-native-') as tmp:
            folder=Path(tmp);captured=[];r=FixtureRegistry();budget=Budget()
            class T(FixtureTransport):
                async def request(self,*a,**kw):captured.append(copy.deepcopy(kw['json']));return await super().request(*a,**kw)
            t=T(response(calls=[native(NAME,{'names':[DECL['name']]})]),response(calls=[native()]),response('The loaded fixture result is seven.'))
            e=Engine(ROOT,r,t,budget,Events());result=await e.answer('Read the fixture.',folder/'work',folder/'evidence',lazy_tools=True)
            self.assertEqual(result['answer'],'The loaded fixture result is seven.');self.assertEqual((r.calls,budget.used,t.calls),(1,3,3))
            self.assertEqual([d['function']['name'] for d in captured[0]['tools']],[NAME])
            self.assertEqual({d['function']['name'] for d in captured[1]['tools']},{DECL['name'],NAME})
            self.assertFalse(any('_schemas' in m.get('content','') for m in captured[1]['messages'] if isinstance(m.get('content'),str)))
if __name__=='__main__':unittest.main(verbosity=2)
