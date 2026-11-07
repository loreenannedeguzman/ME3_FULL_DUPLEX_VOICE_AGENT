"""Fixed OAuth metadata/refresh HTTPS only; no Tk, inference or retries."""
import asyncio
import json
from urllib.parse import urlencode,urlsplit
import aiohttp
from . import auth

DISCOVERY=auth.ISSUER+'/.well-known/openid-configuration'
LIMIT=262144
class HTTPError(ValueError):pass
def need(value,code):
    if not value:raise HTTPError(code)
def trusted_url(url,kind='fixed'):
    try:u=urlsplit(url);port=u.port
    except (ValueError,TypeError):raise HTTPError('endpoint_rejected') from None
    need(isinstance(url,str) and u.scheme=='https' and not u.username and not u.password and port is None and not u.query and not u.fragment,'endpoint_rejected')
    if kind=='jwks':need(u.netloc=='auth.openai.com' and u.path.startswith('/') and len(u.path)<=256,'endpoint_rejected')
    else:need(url in {DISCOVERY,auth.TOKEN_ENDPOINT},'endpoint_rejected')
    return url
def decode_json(raw):
    need(isinstance(raw,bytes) and len(raw)<=LIMIT,'metadata_response_size_rejected')
    def unique(pairs):
        result={}
        for key,value in pairs:need(key not in result,'duplicate_JSON_key');result[key]=value
        return result
    try:
        value=json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _:(_ for _ in ()).throw(HTTPError('invalid_JSON_number')))
        json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8',errors='strict')
    except HTTPError:raise
    except Exception:raise HTTPError('response_JSON_rejected') from None
    need(isinstance(value,dict),'response_object_required');return value
class HTTPS:
    async def _request(self,url,kind='fixed',form=None):
        trusted_url(url,kind);headers={'Accept':'application/json'};data=None
        if form is not None:
            need(url==auth.TOKEN_ENDPOINT and isinstance(form,dict),'token_form_endpoint_rejected')
            headers['Content-Type']='application/x-www-form-urlencoded';data=urlencode(form).encode('ascii')
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20,connect=8,sock_read=12),trust_env=False) as session:
                async with session.request('POST' if form is not None else 'GET',url,data=data,headers=headers,allow_redirects=False) as response:
                    need(response.status==200,'HTTP_'+str(response.status)+'_no_retry')
                    need(response.content_type=='application/json','response_content_type_rejected')
                    need(response.content_length is None or response.content_length<=LIMIT,'metadata_response_size_rejected')
                    raw=bytearray()
                    async for chunk in response.content.iter_chunked(8192):
                        raw.extend(chunk);need(len(raw)<=LIMIT,'metadata_response_size_rejected')
                    return decode_json(bytes(raw))
        except (HTTPError,auth.AuthError):raise
        except Exception:raise HTTPError('HTTPS_connection_failed_no_retry') from None
    def request(self,*args,**kwargs):return asyncio.run(self._request(*args,**kwargs))
    def post(self,url,form):return self.request(url,form=form)
    def discovery(self):
        metadata=self.request(DISCOVERY)
        need(metadata.get('issuer')==auth.ISSUER and metadata.get('authorization_endpoint')==auth.AUTHORIZE and metadata.get('token_endpoint')==auth.TOKEN_ENDPOINT,'discovery_endpoints_rejected')
        keys=self.request(trusted_url(metadata.get('jwks_uri'),'jwks'),kind='jwks')
        need(isinstance(keys.get('keys'),list) and 1<=len(keys['keys'])<=16,'trusted_JWKS_required');return keys
