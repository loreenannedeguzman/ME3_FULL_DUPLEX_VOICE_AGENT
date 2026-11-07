"""Offline OAuth/JWT/security checks; no HTTP/browser/account/provider requests."""
import copy,importlib.util,json,os,secrets,stat,sys,tempfile,threading,time,unittest
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs,urlencode,urlsplit
from unittest.mock import patch
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('me3_plan_auth',HERE.parent/'me3_openai/auth.py');a=importlib.util.module_from_spec(spec);sys.modules[spec.name]=a;spec.loader.exec_module(a)
NOW=int(time.time());CID='oaiapp_fixture_1';URI='http://127.0.0.1:1455/auth/callback'
KEY=rsa.generate_private_key(public_exponent=65537,key_size=2048)
JWK=jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key(),as_dict=True);JWK.update(kid='fixture-key',alg='RS256',use='sig')
JWKS={'keys':[JWK]}
def signed(pending=None,**change):
    payload={'iss':a.ISSUER,'aud':CID,'sub':'fixture-subject','exp':NOW+3600,'iat':NOW,'email':'owner@example.invalid','name':'Fixture Owner'}
    if pending:payload['nonce']=pending.nonce
    payload.update(change);return jwt.encode(payload,KEY,algorithm='RS256',headers={'kid':'fixture-key'})
def response(pending,**change):
    value={'id_token':signed(pending),'access_token':'fixture-access','refresh_token':'fixture-refresh','token_type':'Bearer','scope':' '.join(a.SCOPES),'expires_in':3600};value.update(change);return value
def callback(pending,**change):
    value={'state':pending.state,'code':'fixture-code','client_id':CID};value.update(change);return urlencode(value)
class Memory(a.AuthStore):
    """Tests shared OAuth logic without claiming Windows has POSIX protections."""
    def __init__(self):
        self._host='urn:uuid:00000000-0000-4000-8000-000000000001';self._instance=secrets.token_hex(32);self._thread=threading.Lock();self.values={};self.at=NOW;self.clock=lambda:self.at
    @contextmanager
    def _locked(self):
        a.need(self._thread.acquire(blocking=False),'auth_store_busy')
        try:yield None
        finally:self._thread.release()
    def _read(self,fd,name):return copy.deepcopy(self.values.get(name))
    def _put(self,fd,name,value):self.values[name]=copy.deepcopy(value)
    def accounts(self):
        with self._locked() as fd:return [self._summary(self._record(fd,v['client_id'])) for v in self.values.values()]

class Auth(unittest.TestCase):
    def setUp(self):self.store=Memory();self.calls=[]
    def complete(self,pending=None,reply=None,query=None):
        pending=pending or self.store.begin(URI);reply=reply if reply is not None else response(pending)
        def post(url,form):self.calls.append((url,copy.deepcopy(form)));return reply
        return self.store.complete(pending,query if query is not None else callback(pending),post,JWKS)
    def test_initial_PKCE_state_nonce_host_and_registration(self):
        p=self.store.begin(URI);q=parse_qs(urlsplit(p.authorize_url).query)
        self.assertEqual(q['client_id'],[a.INITIAL_CLIENT]);self.assertEqual(q['agent_name_hint'],['ME3']);self.assertEqual(q['redirect_uri'],[URI]);self.assertEqual(q['ext_agent_host_id'],[self.store.host_id]);self.assertEqual(q['code_challenge_method'],['S256'])
        import hashlib,base64
        self.assertEqual(q['code_challenge'],[base64.urlsafe_b64encode(hashlib.sha256(p.verifier.encode()).digest()).decode().rstrip('=')]);other=self.store.begin(URI)
        self.assertNotEqual((p.state,p.nonce,p.verifier),(other.state,other.nonce,other.verifier));self.assertNotIn(p.state,repr(p));self.assertNotIn(p.verifier,repr(p))
    def test_exact_loopback_callback(self):
        for uri in ('http://localhost:1455/auth/callback','https://127.0.0.1:1455/auth/callback','http://127.0.0.1:1455/callback',URI+'?x=1',URI+'#x','http://user@127.0.0.1:1455/auth/callback','http://127.0.0.1/auth/callback','http://127.0.0.1:80/auth/callback'):
            with self.subTest(uri=uri):self.assertRaises(a.AuthError,self.store.begin,uri)
    def test_success_exact_form_and_redacted_summary(self):
        p=self.store.begin(URI);summary=self.complete(p);self.assertTrue(summary['plan_enabled']);self.assertEqual(self.store.access_token(CID),'fixture-access')
        self.assertEqual(self.calls,[ (a.TOKEN_ENDPOINT,{'grant_type':'authorization_code','client_id':CID,'code':'fixture-code','code_verifier':p.verifier,'redirect_uri':URI,'resource':a.RESOURCE}) ])
        for output in (summary,self.store.accounts()):
            text=json.dumps(output);self.assertNotIn('fixture-access',text);self.assertNotIn('fixture-refresh',text);self.assertNotIn('id_token',text)
    def test_denied_scope_retains_identity_but_no_inference(self):
        p=self.store.begin(URI);summary=self.complete(p,response(p,scope='openid profile email'))
        self.assertFalse(summary['plan_enabled']);self.assertEqual(summary['subject'],'fixture-subject');self.assertRaises(a.AuthError,self.store.access_token,CID)
    def test_state_duplicate_fields_and_provider_error_no_exchange(self):
        for make in (lambda p:callback(p,state='wrong'),lambda p:callback(p)+'&state='+p.state,lambda p:callback(p)+'&code=other',lambda p:callback(p)+'&unexpected=1',lambda p:urlencode({'state':p.state,'error':'access_denied','error_description':'secret-not-logged'})):
            p=self.store.begin(URI)
            with self.assertRaises(a.AuthError) as caught:self.complete(p,query=make(p))
            self.assertNotIn('secret-not-logged',str(caught.exception));self.assertTrue(p.used)
        self.assertEqual(self.calls,[])
    def test_initial_requires_issued_client_and_single_attempt(self):
        p=self.store.begin(URI)
        self.assertRaises(a.AuthError,self.complete,p,query=urlencode({'state':p.state,'code':'code'}));self.assertEqual(self.calls,[])
        self.assertRaises(a.AuthError,self.complete,p);self.assertEqual(self.calls,[])
    def test_timeout_consumes_exactly_once_no_credential_write(self):
        p=self.store.begin(URI);attempts=[]
        def post(*args):attempts.append(1);raise TimeoutError('private response never logged')
        with self.assertRaises(a.AuthError) as caught:self.store.complete(p,callback(p),post,JWKS)
        self.assertEqual(str(caught.exception),'token_endpoint_failed_no_retry');self.assertEqual(len(attempts),1);self.assertEqual(self.store.values,{})
        self.assertRaises(a.AuthError,self.store.complete,p,callback(p),post,JWKS);self.assertEqual(len(attempts),1)
    def test_expired_or_foreign_pending_never_exchanges(self):
        p=self.store.begin(URI);self.store.at+=601;self.assertRaises(a.AuthError,self.complete,p)
        p=Memory().begin(URI);self.assertRaises(a.AuthError,self.complete,p);self.assertEqual(self.calls,[])
    def test_bad_JWT_claims_never_persist(self):
        for changes in ({'iss':'https://evil.invalid'},{'aud':'other-client'},{'exp':NOW},{'exp':True},{'nonce':'wrong'},{'sub':''},{'iat':NOW+60},{'nbf':NOW+60},{'aud':[CID,'other'],'azp':'other'}):
            p=self.store.begin(URI)
            with self.subTest(changes=changes):self.assertRaises(a.AuthError,self.complete,p,response(p,id_token=signed(p,**changes)))
        self.assertEqual(self.store.values,{})
    def test_bad_signature_unknown_key_and_algorithm_rejected(self):
        p=self.store.begin(URI);other=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        payload={'iss':a.ISSUER,'aud':CID,'exp':NOW+3600,'sub':'x','nonce':p.nonce}
        bad=jwt.encode(payload,other,algorithm='RS256',headers={'kid':'fixture-key'});self.assertRaises(a.AuthError,self.complete,p,response(p,id_token=bad))
        p=self.store.begin(URI);bad=jwt.encode({**payload,'nonce':p.nonce},'x'*32,algorithm='HS256',headers={'kid':'fixture-key'});self.assertRaises(a.AuthError,self.complete,p,response(p,id_token=bad))
        p=self.store.begin(URI);self.assertRaises(a.AuthError,self.store.complete,p,callback(p),lambda *args:response(p),{'keys':[dict(JWK,kid='unknown')]})
        p=self.store.begin(URI);self.assertRaises(a.AuthError,self.store.complete,p,callback(p),lambda *args:response(p),{'keys':[JWK,JWK]});self.assertEqual(self.store.values,{})
    def test_returning_same_client_new_nonce_and_identity(self):
        self.complete();old=copy.deepcopy(self.store.values);p=self.store.begin(URI,CID);q=parse_qs(urlsplit(p.authorize_url).query)
        self.assertEqual(q['client_id'],[CID]);self.assertNotIn('agent_name_hint',q);self.assertIn('id_token_hint',q)
        self.assertRaises(a.AuthError,self.complete,p,response(p,id_token=signed(p,sub='different-account')));self.assertEqual(self.store.values,old)
        p=self.store.begin(URI,CID);self.assertRaises(a.AuthError,self.complete,p,query=callback(p,client_id='oaiapp_other'));self.assertEqual(self.store.values,old)
        p=self.store.begin(URI,CID);reply=response(p);del reply['id_token'];self.assertRaises(a.AuthError,self.complete,p,reply);self.assertEqual(self.store.values,old)
        p=self.store.begin(URI,CID);query=urlencode({'state':p.state,'code':'new-code'});self.assertTrue(self.complete(p,query=query)['plan_enabled'])
    def test_malformed_grants_tokens_and_expiry_fail(self):
        for changes in ({'scope':None},{'expires_in':True},{'expires_in':0},{'token_type':None},{'token_type':'MAC'},{'access_token':'bad\r\nheader'},{'refresh_token':None}):
            p=self.store.begin(URI)
            with self.subTest(changes=changes):self.assertRaises(a.AuthError,self.complete,p,response(p,**changes))
        self.assertEqual(self.store.values,{})
    def test_refresh_rotation_optional_ID_and_exact_form_no_scope(self):
        self.complete();self.store.at=NOW+3560;old=self.store.values[self.store._name(CID)]
        reply={'access_token':'new-access','refresh_token':'new-refresh','token_type':'Bearer','expires_in':3600};calls=[]
        def post(url,form):calls.append((url,form));return reply
        summary=self.store.refresh(CID,post,JWKS);new=self.store.values[self.store._name(CID)]
        self.assertEqual(calls,[(a.TOKEN_ENDPOINT,{'grant_type':'refresh_token','client_id':CID,'refresh_token':'fixture-refresh','resource':a.RESOURCE})]);self.assertEqual(new['id_token'],old['id_token']);self.assertEqual(self.store.access_token(CID),'new-access');self.assertEqual(new['refresh_token'],'new-refresh');self.assertTrue(summary['plan_enabled'])
        self.store.refresh(CID,post,JWKS);self.assertEqual(len(calls),1,'serialized follower observes fresh replacement without another refresh')
    def test_refresh_bad_identity_and_http_failure_preserve_set(self):
        self.complete();self.store.at=NOW+3560;old=copy.deepcopy(self.store.values)
        self.assertRaises(a.AuthError,self.store.refresh,CID,lambda *args:response(None,id_token=signed(None,sub='other')),JWKS);self.assertEqual(self.store.values,old)
        self.assertRaises(a.AuthError,self.store.refresh,CID,lambda *args:{'error':'invalid_grant','error_description':'private'},JWKS);self.assertEqual(self.store.values,old)
    def test_busy_refresh_no_second_request(self):
        self.complete();self.store.at=NOW+3560;calls=[]
        with self.store._locked():self.assertRaises(a.AuthError,self.store.refresh,CID,lambda *args:calls.append(args),JWKS)
        self.assertEqual(calls,[])
    def test_expired_access_never_returned(self):
        self.complete();self.store.at=NOW+3600;self.assertRaises(a.AuthError,self.store.access_token,CID)
    def test_selected_model_exact_catalogue_private_account_bound(self):
        self.assertIsNone(self.store.selection());self.complete()
        choice=self.store.save_selection(CID,'gpt-fixture',['gpt-fixture','gpt-other']);self.assertEqual(choice,self.store.selection());self.assertEqual(choice['ext_agent_host_id'],self.store.host_id)
        self.assertNotIn('fixture-access',json.dumps(choice));old=copy.deepcopy(self.store.values)
        self.assertRaises(a.AuthError,self.store.save_selection,CID,'gpt-guessed',['gpt-fixture']);self.assertEqual(self.store.values,old)
        self.store.values['choice.json']['subject']='another-account';self.assertRaises(a.AuthError,self.store.selection)
    def test_model_selection_no_disabled_or_expired_credential_activation(self):
        p=self.store.begin(URI);self.complete(p,response(p,scope='openid profile email'))
        self.assertRaises(a.AuthError,self.store.save_selection,CID,'gpt-fixture',['gpt-fixture']);self.assertNotIn('choice.json',self.store.values)
        self.complete();self.store.at=NOW+3600;self.assertRaises(a.AuthError,self.store.save_selection,CID,'gpt-fixture',['gpt-fixture']);self.assertNotIn('choice.json',self.store.values)
    def test_storage_permission_link_predicate(self):
        store=self.store
        with patch.object(os,'geteuid',return_value=1000,create=True):
            good=type('Info',(),{'st_mode':stat.S_IFREG|0o600,'st_uid':1000,'st_nlink':1})()
            store._info(good)
            for change in ({'st_mode':stat.S_IFREG|0o644},{'st_uid':1001},{'st_nlink':2},{'st_mode':stat.S_IFLNK|0o600}):
                data={'st_mode':good.st_mode,'st_uid':1000,'st_nlink':1};data.update(change);self.assertRaises(a.AuthError,store._info,type('Info',(),data)())

class PrivateFilesystem(unittest.TestCase):
    def test_real_POSIX_storage_or_explicit_platform_refusal(self):
        if os.name!='posix':
            with self.assertRaisesRegex(a.AuthError,'private_storage_requires_POSIX'):a.AuthStore(HERE)
            return # This is Windows fail-closed proof, NOT a POSIX qualification.
        # Project evidence may intentionally be group-writable. Exercise actual
        # protections under a fresh private owner-home fixture, never chmod an
        # existing project/evidence/home directory to accommodate a test.
        with tempfile.TemporaryDirectory(prefix='me3-auth-fixture-',dir=Path.home()) as folder:
            data=Path(folder);self.assertEqual(stat.S_IMODE(data.stat().st_mode),0o700)
            store=a.AuthStore(data,clock=lambda:NOW);again=a.AuthStore(data,clock=lambda:NOW)
            self.assertEqual(store.host_id,again.host_id);self.assertTrue(store.host_id.startswith('urn:uuid:'))
            p=store.begin(URI);store.complete(p,callback(p),lambda *args:response(p),JWKS)
            store.save_selection(CID,'gpt-fixture',['gpt-fixture']);self.assertEqual(again.selection()['model_slug'],'gpt-fixture')
            self.assertEqual(stat.S_IMODE(store.directory.stat().st_mode),0o700)
            for f in store.directory.iterdir():self.assertEqual(stat.S_IMODE(f.stat().st_mode),0o600)
            old=(store.directory/store._name(CID)).read_bytes();p=store.begin(URI,CID)
            self.assertRaises(a.AuthError,store.complete,p,callback(p),lambda *args:response(p,id_token=signed(p,nonce='bad')),JWKS)
            self.assertEqual((store.directory/store._name(CID)).read_bytes(),old)
            store.clock=lambda:NOW+3560
            with store._locked():self.assertRaises(a.AuthError,again.refresh,CID,lambda *args:self.fail('must not refresh'),JWKS)
            account=store.directory/store._name(CID);link=data/'hardlink';os.link(account,link)
            self.assertRaises(a.AuthError,store.accounts);link.unlink();account.chmod(0o644);self.assertRaises(a.AuthError,store.accounts)
    def test_real_symlink_or_explicit_platform_refusal(self):
        if os.name!='posix':self.assertRaises(a.AuthError,a.AuthStore,HERE);return
        with tempfile.TemporaryDirectory(prefix='me3-auth-link-',dir=Path.home()) as folder:
            data=Path(folder);target=data/'target';target.mkdir(mode=0o700);(data/'openai-plan').symlink_to(target,target_is_directory=True)
            self.assertRaises(a.AuthError,a.AuthStore,data)

if __name__=='__main__':unittest.main(verbosity=2)
