"""Sign in with ChatGPT public-client core. No HTTP, browser or inference here.

post_json(url, form_dict) is injected by the owner: one bounded form-encoded
HTTPS POST, no redirects/retries and a bounded JSON object. Never log its input
or output. JWKS must come from the owner's fixed trusted OIDC metadata fetch.
"""
import base64,errno,hashlib,json,math,os,re,secrets,stat,threading,time,uuid
from contextlib import contextmanager
from dataclasses import dataclass,field
from pathlib import Path
from urllib.parse import parse_qs,urlencode,urlsplit
import jwt

ISSUER='https://auth.openai.com'
AUTHORIZE=ISSUER+'/api/accounts/authorize'
TOKEN_ENDPOINT=ISSUER+'/api/accounts/oauth/token'
RESOURCE='https://api.openai.com/v1'
SCOPES=('openid','profile','email','offline_access','resource.invoke','chatgpt.tokens.use.direct')
INITIAL_CLIENT='dynamic_agent_client'
AUTH_STORE_LOCK_TIMEOUT=1.0
class AuthError(ValueError):pass
def need(condition,code):
    if not condition:raise AuthError(code)
def secret(value,code='invalid_token'):
    need(isinstance(value,str) and 1<=len(value)<=32768 and all(33<=ord(c)<=126 for c in value),code);return value
def client(value):
    need(isinstance(value,str) and value!=INITIAL_CLIENT and re.fullmatch(r'[A-Za-z0-9_.:-]{1,256}',value) is not None,'issued_client_required');return value
def now_number(value):
    need(type(value) in {int,float} and math.isfinite(value) and value>=0,'invalid_clock');return value
def unique(pairs):
    out={}
    for k,v in pairs:
        need(k not in out,'duplicate_json_key');out[k]=v
    return out
def scopes(value):
    need(isinstance(value,str) and 0<len(value)<=8192,'invalid_granted_scopes')
    values=value.split();need(len(values)<=64 and len(set(values))==len(values) and all(re.fullmatch(r'[A-Za-z0-9:._/-]{1,128}',v) for v in values),'invalid_granted_scopes');return sorted(values)
def callback_uri(value):
    try:u=urlsplit(value);port=u.port
    except (TypeError,ValueError):raise AuthError('invalid_loopback_callback') from None
    need(isinstance(value,str) and u.scheme=='http' and u.hostname=='127.0.0.1' and u.netloc=='127.0.0.1:'+str(port) and type(port) is int and 1024<=port<=65535 and u.path=='/auth/callback' and not u.query and not u.fragment and not u.username and not u.password,'invalid_loopback_callback')
    return value
def claims(id_token,client_id,jwks,clock,nonce=None):
    """Only supplied trusted RSA JWKS; no discovery, URL keys or algorithm choice."""
    try:
        secret(id_token);parts=id_token.split('.');need(len(parts)==3,'invalid_ID_token')
        for part in parts[:2]:json.loads(jwt.utils.base64url_decode(part).decode(),object_pairs_hook=unique,parse_constant=lambda _:(_ for _ in ()).throw(AuthError('invalid_ID_token_JSON')))
        header=jwt.get_unverified_header(id_token)
        need(header.get('alg')=='RS256' and isinstance(header.get('kid'),str) and 1<=len(header['kid'])<=256 and not any(k in header for k in ('crit','jku','x5u','enc')),'ID_token_header_rejected')
        need(isinstance(jwks,dict) and isinstance(jwks.get('keys'),list) and 1<=len(jwks['keys'])<=16,'trusted_JWKS_required')
        matches=[k for k in jwks['keys'] if isinstance(k,dict) and k.get('kid')==header['kid']]
        need(len(matches)==1,'ID_token_key_not_unique');key=matches[0]
        need(key.get('kty')=='RSA' and key.get('alg','RS256')=='RS256' and key.get('use','sig')=='sig' and key.get('key_ops',['verify'])==['verify'],'ID_token_key_rejected')
        public=jwt.PyJWK.from_dict(key,algorithm='RS256').key;need(2048<=public.key_size<=8192,'ID_token_key_size_rejected')
        value=jwt.decode(id_token,public,algorithms=['RS256'],audience=client_id,issuer=ISSUER,options={'require':['iss','aud','exp','sub'],'verify_exp':False,'verify_iat':False,'verify_nbf':False})
        need(value.get('iss')==ISSUER and (value.get('aud')==client_id or isinstance(value.get('aud'),list) and client_id in value['aud']),'ID_token_issuer_or_audience_rejected')
        now=now_number(clock())
        need(type(value['exp']) is int and value['exp']>now,'ID_token_expired')
        for name in ('nbf','iat'):
            if name in value:need(type(value[name]) is int and value[name]<=now+30,'ID_token_time_rejected')
        need(isinstance(value['sub'],str) and 1<=len(value['sub'])<=256 and not any(ord(c)<32 for c in value['sub']),'ID_token_subject_required')
        if isinstance(value['aud'],list) and len(value['aud'])>1:need(value.get('azp')==client_id,'ID_token_authorized_party_rejected')
        if 'azp' in value:need(value['azp']==client_id,'ID_token_authorized_party_rejected')
        if nonce is not None:need(isinstance(value.get('nonce'),str) and secrets.compare_digest(value['nonce'],nonce),'ID_token_nonce_mismatch')
        return value
    except AuthError:raise
    except Exception:raise AuthError('ID_token_validation_failed') from None

@dataclass(repr=False)
class PendingAuth:
    redirect_uri:str
    client_id:str|None
    host_id:str
    state:str=field(repr=False)
    nonce:str=field(repr=False)
    verifier:str=field(repr=False)
    created_at:float
    store_id:str=field(repr=False)
    hints:dict=field(default_factory=dict,repr=False)
    used:bool=False
    _lock:object=field(default_factory=threading.Lock,repr=False)
    @property
    def authorize_url(self):
        args={'client_id':self.client_id or INITIAL_CLIENT,'ext_agent_host_id':self.host_id,'response_type':'code','redirect_uri':self.redirect_uri,'scope':' '.join(SCOPES),'resource':RESOURCE,'state':self.state,'nonce':self.nonce,'code_challenge_method':'S256','code_challenge':base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode('ascii')).digest()).decode().rstrip('=')}
        if self.client_id is None:args['agent_name_hint']='ME3'
        else:args.update(self.hints)
        return AUTHORIZE+'?'+urlencode(args)
    def __repr__(self):return 'PendingAuth(used='+str(self.used)+', credentials=redacted)'

class AuthStore:
    """Owned project data/openai-plan only; strict POSIX filesystem or fail closed."""
    def __init__(self,data_root,clock=time.time):
        self.directory=Path(data_root).absolute()/'openai-plan';self.clock=clock;self._instance=secrets.token_hex(32);self._thread=threading.Lock()
        need('..' not in self.directory.parts,'invalid_private_storage_path')
        need(os.name=='posix' and os.open in os.supports_dir_fd,'private_storage_requires_POSIX')
        with self._locked() as fd:
            value=self._read(fd,'host.json')
            if value is None:
                need(not any(n.startswith('account-') for n in os.listdir(fd)),'existing_accounts_missing_host_ID')
                value={'ext_agent_host_id':'urn:uuid:'+str(uuid.uuid4())};self._put(fd,'host.json',value)
            self._host=self._host_value(value)
    def _host_value(self,value):
        try:
            need(isinstance(value,dict) and set(value)=={'ext_agent_host_id'},'invalid_host_ID')
            host=value['ext_agent_host_id'];need(isinstance(host,str) and host.startswith('urn:uuid:') and host=='urn:uuid:'+str(uuid.UUID(host[9:])),'invalid_host_ID');return host
        except (ValueError,TypeError,AttributeError):raise AuthError('invalid_host_ID') from None
    @property
    def host_id(self):return self._host
    def _info(self,info,directory=False):
        need((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink==1) and info.st_uid==os.geteuid() and info.st_mode&0o077==0,'private_storage_permissions_or_links')
    @contextmanager
    def _folder(self):
        fd=None
        try:
            fd=os.open(self.directory.anchor,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            for index,part in enumerate(self.directory.parts[1:]):
                final=index==len(self.directory.parts)-2
                if final:
                    try:os.mkdir(part,0o700,dir_fd=fd)
                    except FileExistsError:pass
                nxt=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=nxt
                info=os.fstat(fd)
                if final:self._info(info,True)
                else:need(info.st_uid in {0,os.geteuid()} and info.st_mode&0o022==0,'untrusted_storage_ancestor')
            yield fd
        except OSError:raise AuthError('private_storage_unavailable_or_link') from None
        finally:
            if fd is not None:os.close(fd)
    @contextmanager
    def _locked(self):
        # Status polling and the inference token worker share private records.
        # Wait only for local lock contention, within one total deadline; an
        # active refresh still owns the lock through its single token exchange.
        deadline=time.monotonic()+AUTH_STORE_LOCK_TIMEOUT
        need(self._thread.acquire(timeout=max(0,deadline-time.monotonic())),'auth_store_busy')
        try:
            with self._folder() as fd:
                import fcntl
                lockfd=os.open('session.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600,dir_fd=fd)
                try:
                    self._info(os.fstat(lockfd))
                    while True:
                        try:fcntl.flock(lockfd,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                        except OSError as exc:
                            if exc.errno not in (errno.EACCES,errno.EAGAIN):raise
                            remaining=deadline-time.monotonic()
                            if remaining<=0:raise AuthError('auth_store_busy') from None
                            time.sleep(min(0.01,remaining))
                    yield fd
                finally:os.close(lockfd)
        finally:self._thread.release()
    def _read(self,fd,name):
        try:handle=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        except FileNotFoundError:return None
        except OSError:raise AuthError('private_record_unavailable_or_link') from None
        try:
            info=os.fstat(handle);self._info(info);need(info.st_size<=131072,'private_record_size')
            with os.fdopen(handle,'rb',closefd=False) as stream:raw=stream.read(131073)
            need(len(raw)<=131072,'private_record_size')
            try:return json.loads(raw,object_pairs_hook=unique)
            except (ValueError,UnicodeError):raise AuthError('private_record_invalid') from None
        finally:os.close(handle)
    def _put(self,fd,name,value):
        self._read(fd,name) # Existing unsafe targets are never replaced.
        raw=json.dumps(value,allow_nan=False,ensure_ascii=False).encode();need(len(raw)<=131072,'private_record_size')
        temp='.auth-'+secrets.token_hex(16)
        handle=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
        try:
            with os.fdopen(handle,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
            os.replace(temp,name,src_dir_fd=fd,dst_dir_fd=fd);os.fsync(fd)
        finally:
            try:os.unlink(temp,dir_fd=fd)
            except FileNotFoundError:pass
    def _name(self,client_id):return 'account-'+hashlib.sha256(client(client_id).encode()).hexdigest()+'.json'
    def _record(self,fd,client_id):
        value=self._read(fd,self._name(client_id));need(isinstance(value,dict),'account_not_registered')
        need(value.get('client_id')==client_id and value.get('issuer')==ISSUER and value.get('ext_agent_host_id')==self.host_id and value.get('identity_verified') is True and isinstance(value.get('subject'),str) and isinstance(value.get('scopes'),list),'private_account_binding_invalid')
        return value
    def _summary(self,value):
        return {k:value[k] for k in ('client_id','issuer','subject','email','name','scopes','plan_enabled','access_expires_at','saved_at') if k in value}
    def accounts(self):
        with self._locked() as fd:
            result=[]
            for name in sorted(os.listdir(fd)):
                if re.fullmatch(r'account-[a-f0-9]{64}\.json',name):
                    value=self._read(fd,name);need(isinstance(value,dict) and name==self._name(value.get('client_id')),'private_account_binding_invalid');result.append(self._summary(self._record(fd,value['client_id'])))
            return result
    def begin(self,redirect_uri,client_id=None):
        callback_uri(redirect_uri);hints={}
        if client_id is not None:
            with self._locked() as fd:
                previous=self._record(fd,client(client_id))
                if previous.get('id_token'):hints['id_token_hint']=previous['id_token']
                if previous.get('email'):hints['login_hint']=previous['email']
        return PendingAuth(redirect_uri,client_id,self.host_id,secrets.token_urlsafe(32),secrets.token_urlsafe(32),secrets.token_urlsafe(48),now_number(self.clock()),self._instance,hints)
    def _exchange(self,post_json,form):
        try:value=post_json(TOKEN_ENDPOINT,form)
        except Exception:raise AuthError('token_endpoint_failed_no_retry') from None
        try:valid=isinstance(value,dict) and len(json.dumps(value,allow_nan=False))<=131072 and 'error' not in value
        except (ValueError,TypeError):valid=False
        need(valid,'token_endpoint_rejected_no_retry');return value
    def _tokens(self,response,client_id,jwks,nonce=None,previous=None):
        need(isinstance(response.get('token_type'),str) and response['token_type'].lower()=='bearer','token_type_rejected')
        access=secret(response.get('access_token'));expires=response.get('expires_in')
        need(type(expires) is int and 1<=expires<=86400,'invalid_token_expiry')
        granted=scopes(response['scope']) if 'scope' in response else (previous['scopes'] if previous else scopes(None))
        id_token=response.get('id_token')
        if id_token is not None:
            identity=claims(id_token,client_id,jwks,self.clock,nonce)
            if previous:need(identity['iss']==previous['issuer'] and identity['sub']==previous['subject'],'returning_account_identity_mismatch')
        else:
            need(previous is not None and nonce is None,'ID_token_required');identity={'sub':previous['subject'],'iss':previous['issuer'],'email':previous.get('email'),'name':previous.get('name')};id_token=previous.get('id_token')
        refresh=response.get('refresh_token',previous.get('refresh_token') if previous else None)
        if refresh is not None:secret(refresh)
        need('offline_access' not in granted or refresh is not None,'refresh_token_required')
        now=now_number(self.clock());value={'client_id':client_id,'issuer':ISSUER,'subject':identity['sub'],'ext_agent_host_id':self.host_id,'identity_verified':True,'access_token':access,'id_token':id_token,'refresh_token':refresh,'token_type':'Bearer','scopes':granted,'plan_enabled':set(SCOPES)<=set(granted),'saved_at':now,'access_expires_at':now+expires}
        for name,limit in (('email',320),('name',200)):
            item=identity.get(name)
            if isinstance(item,str) and 0<len(item)<=limit and not any(ord(c)<32 for c in item):value[name]=item
        return value
    def complete(self,pending,raw_query,post_json,trusted_jwks):
        need(isinstance(pending,PendingAuth) and pending.store_id==self._instance and pending.host_id==self.host_id,'pending_authorization_not_owned')
        with pending._lock:
            need(not pending.used,'authorization_attempt_already_consumed');pending.used=True
        need(0<=now_number(self.clock())-pending.created_at<=600,'authorization_attempt_expired')
        need(isinstance(raw_query,str) and len(raw_query)<=16384 and not raw_query.startswith(('http:','https:')),'invalid_callback_query')
        try:query=parse_qs(raw_query.removeprefix('?'),keep_blank_values=True,strict_parsing=True,max_num_fields=8)
        except ValueError:raise AuthError('invalid_callback_query') from None
        need(all(len(v)==1 for v in query.values()) and set(query)<={'code','state','client_id','scope','error','error_description','error_uri'} and isinstance(query.get('state',[''])[0],str) and secrets.compare_digest(query.get('state',[''])[0],pending.state),'callback_state_or_fields_rejected')
        if 'error' in query:raise AuthError('authorization_denied_no_exchange')
        code=secret(query.get('code',[None])[0],'authorization_code_required')
        issued=client(query.get('client_id',[pending.client_id])[0])
        if pending.client_id is not None:need(issued==pending.client_id,'returning_client_mismatch')
        with self._locked() as fd:
            previous=self._read(fd,self._name(issued))
            if previous is not None:previous=self._record(fd,issued)
            response=self._exchange(post_json,{'grant_type':'authorization_code','client_id':issued,'code':code,'code_verifier':pending.verifier,'redirect_uri':pending.redirect_uri,'resource':RESOURCE})
            value=self._tokens(response,issued,trusted_jwks,pending.nonce,previous)
            self._put(fd,self._name(issued),value);return self._summary(value)
    def refresh(self,client_id,post_json,trusted_jwks):
        with self._locked() as fd:
            previous=self._record(fd,client(client_id))
            if previous['access_expires_at']>now_number(self.clock())+60:return self._summary(previous)
            need(previous.get('refresh_token') is not None,'refresh_unavailable')
            response=self._exchange(post_json,{'grant_type':'refresh_token','client_id':client_id,'refresh_token':previous['refresh_token'],'resource':RESOURCE})
            value=self._tokens(response,client_id,trusted_jwks,previous=previous)
            self._put(fd,self._name(client_id),value);return self._summary(value)
    def access_token(self,client_id):
        """Private inference-only getter. Caller never serializes/logs the result."""
        with self._locked() as fd:
            value=self._record(fd,client(client_id))
            need(value.get('plan_enabled') is True and set(SCOPES)<=set(value['scopes']),'ChatGPT_plan_access_not_enabled')
            need(type(value.get('access_expires_at')) in {int,float} and value['access_expires_at']>now_number(self.clock()),'refresh_required')
            return secret(value.get('access_token'))
    def _model(self,value):
        need(isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}',value) is not None,'invalid_selected_model');return value
    def save_selection(self,client_id,model_slug,available_slugs):
        """Root supplies IDs from its authenticated actual model catalogue.

        This private setup choice does not alter a production planner/profile.
        """
        self._model(model_slug)
        need(isinstance(available_slugs,(list,tuple)) and 1<=len(available_slugs)<=512,'actual_model_catalogue_required')
        names=sorted(set(self._model(v) for v in available_slugs));need(model_slug in names,'selected_model_not_in_catalogue')
        with self._locked() as fd:
            record=self._record(fd,client(client_id))
            need(record.get('plan_enabled') is True and set(SCOPES)<=set(record['scopes']),'ChatGPT_plan_access_not_enabled')
            need(record.get('access_expires_at',0)>now_number(self.clock()),'refresh_required')
            selection={'client_id':client_id,'model_slug':model_slug,'ext_agent_host_id':self.host_id,'issuer':record['issuer'],'subject':record['subject'],'selected_at':now_number(self.clock()),'catalogue_sha256':hashlib.sha256(json.dumps(names,separators=(',',':')).encode()).hexdigest()}
            self._put(fd,'choice.json',selection);return dict(selection)
    def selection(self):
        with self._locked() as fd:
            value=self._read(fd,'choice.json')
            if value is None:return None
            need(isinstance(value,dict) and set(value)=={'client_id','model_slug','ext_agent_host_id','issuer','subject','selected_at','catalogue_sha256'},'private_selection_invalid')
            record=self._record(fd,client(value.get('client_id')));self._model(value.get('model_slug'))
            need(value.get('ext_agent_host_id')==self.host_id and value.get('issuer')==record['issuer'] and value.get('subject')==record['subject'] and isinstance(value.get('catalogue_sha256'),str) and re.fullmatch('[a-f0-9]{64}',value['catalogue_sha256']) is not None,'private_selection_binding_invalid')
            need(record.get('plan_enabled') is True and set(SCOPES)<=set(record['scopes']),'ChatGPT_plan_access_not_enabled')
            now_number(value['selected_at']);return dict(value)
