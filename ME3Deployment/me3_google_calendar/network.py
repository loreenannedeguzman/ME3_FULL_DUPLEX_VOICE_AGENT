"""Bounded Google-only HTTPS. No redirects, retries, raw-error logging or polling."""
import json,re,urllib.error,urllib.request
from urllib.parse import urlencode
from .auth import AuthError,TOKEN_URL,USERINFO_URL,JWKS_URL
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):return None
def _send(method,url,*,data=None,headers=None):
 request=urllib.request.Request(url,data=data,headers=headers or {},method=method)
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
 try:
  try:response=opener.open(request,timeout=15)
  except urllib.error.HTTPError as error:response=error
  with response:
   if response.geturl()!=url:raise AuthError('google_calendar_redirect_rejected')
   raw=response.read(1048577)
   if len(raw)>1048576:raise AuthError('google_calendar_response_too_large')
   try:body=json.loads(raw) if raw else {}
   except Exception:raise AuthError('google_calendar_response_invalid') from None
   if not isinstance(body,dict):raise AuthError('google_calendar_response_invalid')
   return {'status':response.status,'body':body}
 except AuthError:raise
 except Exception:raise AuthError('google_calendar_connection_failed_no_retry') from None
def oauth_request(method,url,*,form=None,headers=None):
 if (method,url) not in {('POST',TOKEN_URL),('GET',USERINFO_URL),('GET',JWKS_URL)}:raise AuthError('google_oauth_endpoint_rejected')
 data=urlencode(form).encode() if form is not None else None
 result=_send(method,url,data=data,headers={**(headers or {}),**({'Content-Type':'application/x-www-form-urlencoded'} if data is not None else {})})
 if result['status']!=200:raise AuthError('google_calendar_sign_in_or_refresh_rejected')
 return result['body']
class CalendarRequest:
 def __call__(self,method,path,query=None,json=None,headers=None):
  if method not in {'GET','POST','PATCH'} or re.fullmatch(r'/calendar/v3/calendars/primary/events(?:/[A-Za-z0-9_-]{1,1024})?',path) is None:raise AuthError('google_calendar_endpoint_rejected')
  allowed={'timeMin','timeMax','timeZone','maxResults','pageToken','singleEvents','orderBy','showDeleted','sendUpdates','fields','conferenceDataVersion'}
  if not isinstance(query or {},dict) or set(query or {})-allowed:raise AuthError('google_calendar_query_rejected')
  if method!='GET' and (query or {}).get('sendUpdates')!='none':raise AuthError('google_calendar_no_invitation_mode_required')
  if set(headers or {})-{'Authorization','If-Match','Accept'} or (headers or {}).get('Accept','application/json')!='application/json':raise AuthError('google_calendar_headers_rejected')
  if not isinstance((headers or {}).get('Authorization'),str) or not headers['Authorization'].startswith('Bearer '):raise AuthError('google_calendar_credentials_required')
  url='https://www.googleapis.com'+path
  if query:url+='?'+urlencode(query)
  data=None
  if json is not None:
   import json as codec
   data=codec.dumps(json,allow_nan=False,separators=(',',':')).encode()
   if len(data)>65536:raise AuthError('google_calendar_request_too_large')
  return _send(method,url,data=data,headers={**(headers or {}),**({'Content-Type':'application/json'} if data is not None else {})})
HTTPRequest=CalendarRequest
