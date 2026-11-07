"""Pi-owned Google desktop OAuth, fixed primary-account binding, no secret logs."""
import base64,hashlib,json,math,os,re,secrets,stat,time
from contextlib import contextmanager
from dataclasses import dataclass,field
from pathlib import Path
from urllib.parse import urlencode,parse_qs

ACCOUNT='owner@example.invalid'
SCOPES=frozenset({'openid','https://www.googleapis.com/auth/userinfo.email','https://www.googleapis.com/auth/calendar.events.owned'})
AUTH_URL='https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL='https://oauth2.googleapis.com/token'
USERINFO_URL='https://openidconnect.googleapis.com/v1/userinfo'
JWKS_URL='https://www.googleapis.com/oauth2/v3/certs'
class AuthError(ValueError):pass
def need(v,code='google_calendar_authorization_rejected'):
 if not v:raise AuthError(code)
def bounded(v,n=16384):return isinstance(v,str) and 0<len(v)<=n and not any(ord(c)<32 for c in v)
def parse(raw):
 def pairs(items):
  out={}
  for k,v in items:need(k not in out,'duplicate_google_setup_field');out[k]=v
  return out
 try:
  need(isinstance(raw,bytes) and len(raw)<=65536,'bounded_google_setup_file_required')
  value=json.loads(raw,object_pairs_hook=pairs);need(isinstance(value,dict));return value
 except AuthError:raise
 except Exception:raise AuthError('google_setup_file_invalid') from None
def client_config(raw):
 v=parse(raw);need(set(v)=={'installed'} and isinstance(v['installed'],dict),'google_desktop_client_JSON_required')
 c=v['installed'];cid=c.get('client_id');secret=c.get('client_secret')
 need(bounded(cid,250) and re.fullmatch(r'[A-Za-z0-9_-]+\.apps\.googleusercontent\.com',cid),'google_desktop_client_id_required')
 need(bounded(secret,512),'google_desktop_client_secret_required')
 # Never use URLs/redirects supplied by an imported client file.
 return dict(version=1,client_id=cid,client_secret=secret)
def file_ok(s):
 need(stat.S_ISREG(s.st_mode) and stat.S_IMODE(s.st_mode)==0o600 and s.st_uid==os.geteuid() and s.st_nlink==1 and s.st_size<=65536,'private_google_calendar_file_required')
class PrivateStore:
 def __init__(self,root):self.root=Path(root).absolute()
 @contextmanager
 def directory(self,create=False):
  need(os.name=='posix','google_calendar_Pi_private_storage_required')
  fd=None
  try:
   need(self.root==self.root.resolve(),'direct_google_calendar_root_required')
   fd=os.open(self.root.anchor,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
   for name in [*self.root.parts[1:],'data']:
    next_fd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=next_fd
   info=os.fstat(fd);need(info.st_uid==os.geteuid() and not info.st_mode&0o022,'owned_google_calendar_data_directory_required')
   if create:
    try:os.mkdir('google-calendar',0o700,dir_fd=fd)
    except FileExistsError:pass
   next_fd=os.open('google-calendar',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=next_fd
   info=os.fstat(fd);need(info.st_uid==os.geteuid() and stat.S_IMODE(info.st_mode)==0o700,'private_google_calendar_directory_required')
   yield fd
  except AuthError:raise
  except OSError:raise AuthError('google_calendar_private_storage_unavailable') from None
  finally:
   if fd is not None:os.close(fd)
 def read_fd(self,fd,name,optional=False):
  need(name in {'client.json','tokens.json'})
  try:handle=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
  except FileNotFoundError:
   if optional:return None
   raise AuthError('google_calendar_sign_in_required') from None
  try:
   a=os.fstat(handle);file_ok(a);raw=os.read(handle,65537);b=os.fstat(handle);current=os.stat(name,dir_fd=fd,follow_symlinks=False)
   sig=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_size,s.st_mtime_ns,s.st_nlink,s.st_uid)
   need(sig(a)==sig(b)==sig(current),'google_calendar_private_file_changed');return parse(raw)
  finally:os.close(handle)
 def write_fd(self,fd,name,value):
  need(name in {'client.json','tokens.json'})
  try:file_ok(os.stat(name,dir_fd=fd,follow_symlinks=False))
  except FileNotFoundError:pass
  raw=(json.dumps(value,separators=(',',':'),allow_nan=False)+'\n').encode();need(len(raw)<=65536)
  temporary='.'+name+'.'+secrets.token_hex(16)+'.tmp';handle=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
  try:
   with os.fdopen(handle,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
   os.replace(temporary,name,src_dir_fd=fd,dst_dir_fd=fd);os.fsync(fd)
  finally:
   try:os.unlink(temporary,dir_fd=fd)
   except FileNotFoundError:pass
 @contextmanager
 def locked(self,create=False):
  import fcntl
  with self.directory(create) as fd:
   handle=os.open('.auth.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600,dir_fd=fd)
   try:
    file_ok(os.fstat(handle));fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB);yield fd
   except BlockingIOError:raise AuthError('google_calendar_authorization_busy') from None
   finally:os.close(handle)
def claims_identity(v,client_id,nonce=None,now=None):
 need(isinstance(v,dict) and v.get('iss') in {'accounts.google.com','https://accounts.google.com'})
 aud=v.get('aud');need(aud==client_id or isinstance(aud,list) and client_id in aud)
 if isinstance(aud,list) and len(aud)>1:need(v.get('azp')==client_id)
 need(v.get('email_verified') is True and isinstance(v.get('email'),str) and v['email'].casefold()==ACCOUNT,'google_calendar_wrong_account')
 need(bounded(v.get('sub'),255),'google_calendar_identity_unverified')
 if nonce is not None:need(isinstance(v.get('nonce'),str) and secrets.compare_digest(v['nonce'],nonce),'google_calendar_nonce_rejected')
 now=time.time() if now is None else now
 need(type(v.get('exp')) in {int,float} and math.isfinite(v['exp']) and v['exp']>now and type(v.get('iat')) in {int,float} and math.isfinite(v['iat']) and v['iat']<=now+60)
 return v['sub']
def verify_id(token,client_id,nonce,http):
 import jwt
 try:
  h=jwt.get_unverified_header(token);need(h.get('alg')=='RS256' and bounded(h.get('kid'),200))
  keys=http('GET',JWKS_URL).get('keys');need(isinstance(keys,list) and len(keys)<=30)
  key=[k for k in keys if k.get('kid')==h['kid'] and k.get('kty')=='RSA'];need(len(key)==1)
  v=jwt.decode(token,jwt.PyJWK.from_dict(key[0]).key,algorithms=['RS256'],audience=client_id,issuer=['accounts.google.com','https://accounts.google.com'],options={'require':['iss','aud','sub','exp','iat']})
  claims_identity(v,client_id,nonce);return v
 except AuthError:raise
 except Exception:raise AuthError('google_calendar_ID_token_rejected') from None
def validate_record(v,c):
 need(isinstance(v,dict) and set(v)=={'version','account_email','subject','client_id','scopes','access_token','refresh_token','expires_at'})
 need(type(v['version']) is int and v['version']==1 and v['account_email']==ACCOUNT and v['client_id']==c['client_id'] and bounded(v['subject'],255),'google_calendar_binding_invalid')
 need(isinstance(v['scopes'],list) and set(v['scopes'])==SCOPES and len(v['scopes'])==len(SCOPES),'google_calendar_scopes_invalid')
 need(bounded(v['access_token']) and bounded(v['refresh_token']) and type(v['expires_at']) in {int,float} and math.isfinite(v['expires_at']))
 return v
def redirect_ok(value):
 need(isinstance(value,str) and re.fullmatch(r'http://127\.0\.0\.1:[1-9][0-9]{0,4}/oauth2/callback',value) is not None,'google_calendar_loopback_redirect_required')
 need(0<int(value.split(':')[2].split('/')[0])<=65535,'google_calendar_loopback_redirect_required')
@dataclass(repr=False)
class Pending:
 client:dict=field(repr=False)
 redirect:str
 state:str=field(default_factory=lambda:secrets.token_urlsafe(32),repr=False)
 verifier:str=field(default_factory=lambda:secrets.token_urlsafe(48),repr=False)
 nonce:str=field(default_factory=lambda:secrets.token_urlsafe(32),repr=False)
 started:float=field(default_factory=time.monotonic)
 consumed:bool=False
 def url(self):
  redirect_ok(self.redirect)
  challenge=base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).decode().rstrip('=')
  return AUTH_URL+'?'+urlencode(dict(client_id=self.client['client_id'],redirect_uri=self.redirect,response_type='code',scope=' '.join(sorted(SCOPES)),state=self.state,nonce=self.nonce,code_challenge=challenge,code_challenge_method='S256',access_type='offline',prompt='consent select_account',login_hint=ACCOUNT))
 def callback(self,query):
  redirect_ok(self.redirect)
  need(not self.consumed and 0<=time.monotonic()-self.started<=300,'google_calendar_login_expired_or_consumed')
  need(isinstance(query,str) and len(query)<=8192)
  try:v=parse_qs(query,keep_blank_values=True,strict_parsing=True)
  except ValueError:raise AuthError('google_calendar_callback_invalid') from None
  need(all(len(x)==1 for x in v.values()) and set(v)<={'code','state','scope','authuser','prompt','error','error_description','error_uri','iss'})
  need('state' in v and isinstance(v['state'][0],str) and secrets.compare_digest(v['state'][0],self.state),'google_calendar_callback_state_rejected')
  self.consumed=True;need('error' not in v,'google_calendar_consent_not_granted')
  need(bounded(v.get('code',[''])[0],4096),'google_calendar_callback_code_required');return v['code'][0]
class Credentials:
 def __init__(self,root,*,http=None,clock=time.time,verify=verify_id):
  self.store=PrivateStore(root);self.clock=clock;self.verify=verify
  if http is None:
   from .network import oauth_request
   http=oauth_request
  self.http=http
 def configured(self):
  try:
   with self.store.directory() as fd:c=self.store.read_fd(fd,'client.json');validate_record(self.store.read_fd(fd,'tokens.json'),c)
   return True
  except (AuthError,OSError,KeyError,TypeError):return False
 def import_client(self,raw,*,replace=False):
  c=client_config(raw)
  with self.store.locked(True) as fd:
   old=self.store.read_fd(fd,'client.json',True)
   need(old is None or old==c or replace is True,'google_calendar_client_replacement_requires_confirmation')
   self.store.write_fd(fd,'client.json',c)
  return {'configured_client':True,'account_email':ACCOUNT}
 def begin(self,redirect):
  redirect_ok(redirect)
  with self.store.directory() as fd:c=self.store.read_fd(fd,'client.json')
  return Pending(c,redirect)
 def _record(self,response,c,*,pending=None,previous=None):
  need(isinstance(response,dict) and response.get('token_type','').casefold()=='bearer' and bounded(response.get('access_token')),'google_calendar_token_exchange_rejected')
  granted=set(response.get('scope','').split()) if 'scope' in response else (set(previous['scopes']) if previous else set())
  # Google may canonicalize the email alias; never accept extra Drive/Gmail scopes.
  granted={'https://www.googleapis.com/auth/userinfo.email' if x=='email' else x for x in granted}
  need(granted==SCOPES,'google_calendar_requested_scopes_not_granted')
  expiry=response.get('expires_in');need(type(expiry) in {int,float} and math.isfinite(expiry) and 0<expiry<=86400)
  if pending is not None:
   need(bounded(response.get('id_token')),'google_calendar_ID_token_required')
   claims=self.verify(response['id_token'],c['client_id'],pending.nonce,self.http)
   subject=claims_identity(claims,c['client_id'],pending.nonce,now=self.clock())
  else:
   identity=self.http('GET',USERINFO_URL,headers={'Authorization':'Bearer '+response['access_token']})
   need(identity.get('email_verified') is True and isinstance(identity.get('email'),str) and identity['email'].casefold()==ACCOUNT and identity.get('sub')==previous['subject'],'google_calendar_refresh_identity_changed')
   subject=previous['subject']
  refresh=response.get('refresh_token') or (previous['refresh_token'] if previous else None);need(bounded(refresh),'google_calendar_offline_access_required')
  value=dict(version=1,account_email=ACCOUNT,subject=subject,client_id=c['client_id'],scopes=sorted(SCOPES),access_token=response['access_token'],refresh_token=refresh,expires_at=self.clock()+expiry)
  return validate_record(value,c)
 def finish(self,pending,query):
  need(isinstance(pending,Pending),'google_calendar_owned_login_required')
  redirect_ok(pending.redirect)
  code=pending.callback(query)
  with self.store.locked() as fd:
   c=self.store.read_fd(fd,'client.json');need(c==pending.client,'google_calendar_client_changed')
   response=self.http('POST',TOKEN_URL,form=dict(client_id=c['client_id'],client_secret=c['client_secret'],code=code,code_verifier=pending.verifier,redirect_uri=pending.redirect,grant_type='authorization_code'))
   value=self._record(response,c,pending=pending);self.store.write_fd(fd,'tokens.json',value)
  return {'connected':True,'account_email':ACCOUNT,'calendar_id':'primary'}
 def headers(self):
  with self.store.locked() as fd:
   c=self.store.read_fd(fd,'client.json');value=validate_record(self.store.read_fd(fd,'tokens.json'),c)
   if value['expires_at']<=self.clock()+60:
    response=self.http('POST',TOKEN_URL,form=dict(client_id=c['client_id'],client_secret=c['client_secret'],refresh_token=value['refresh_token'],grant_type='refresh_token'))
    value=self._record(response,c,previous=value);self.store.write_fd(fd,'tokens.json',value)
   return {'account_email':ACCOUNT,'headers':{'Authorization':'Bearer '+value['access_token']}}
