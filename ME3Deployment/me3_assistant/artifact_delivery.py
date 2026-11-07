"""Owner-click artifact delivery. No planner, host evaluation, writes or network.

The GUI authenticates/gates requests before calling this backend. Download bytes
are attachments; generated HTML/JavaScript belongs only in an opaque sandboxed
iframe, never the ME3 document. This browser preview blocks external subresources
and network APIs, not JavaScript self-navigation of its own isolated frame.
"""
import io,json,posixpath,re,zipfile
from html import escape
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import unquote,urlsplit
from me3_assistant.workspace import Workspace,WorkspaceError,digest,relative

PREVIEW_CSP="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; media-src 'none'; font-src 'none'; frame-src 'none'; worker-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
PREVIEW_SANDBOX='allow-scripts'
TYPES={'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8','.json':'application/json; charset=utf-8','.svg':'image/svg+xml','.csv':'text/csv; charset=utf-8'}
ALLOWED=set('html head body title main header footer nav section article aside div span p br hr pre code blockquote ul ol li dl dt dd h1 h2 h3 h4 h5 h6 strong em b i u s small sub sup mark table caption thead tbody tfoot tr th td colgroup col a button label input textarea select option optgroup fieldset legend details summary figure figcaption time canvas progress meter'.split())
DROP_CONTENT={'iframe','object','embed','svg','math','noscript','template','audio','video'}
VOID={'br','hr','input','col'}
ATTRS={'class','id','title','role','lang','dir','colspan','rowspan','scope','type','value','placeholder','checked','selected','disabled','readonly','multiple','open','datetime','name','for','min','max','step','rows','cols','width','height'}

def need(value,reason):
 if not value:raise WorkspaceError(reason)
def manifest(files):return digest(json.dumps({name:digest(raw) for name,raw in sorted(files.items())},sort_keys=True).encode())
def css(text):
 # CSP is the enforcement layer; strip URL constructs as a separate defense.
 text=re.sub(r'/\*.*?\*/','',text,flags=re.S);text=re.sub(r'@import\s+[^;{}]*(?:;|$)','',text,flags=re.I)
 text=re.sub(r'url\s*\([^)]*\)','none',text,flags=re.I)
 return text.replace('<','\\3c ').replace('>','\\3e ')
def javascript(text):return re.sub(r'</script',r'<\\/script',text,flags=re.I)
def local_reference(entry,value,files,suffix):
 need(isinstance(value,str) and len(value)<=240,'preview_local_reference_required');parsed=urlsplit(value)
 need(not parsed.scheme and not parsed.netloc and not parsed.query and not parsed.fragment and not value.startswith('/') and '\\' not in value,'preview_external_reference_blocked')
 path=posixpath.normpath(posixpath.join(str(PurePosixPath(entry).parent),unquote(parsed.path)))
 relative(path);need(path.endswith(suffix) and path in files,'preview_local_asset_missing');return path

class StaticHTML(HTMLParser):
 def __init__(self,entry,files):
  super().__init__(convert_charrefs=True);self.entry=entry;self.files=files;self.output=[];self.suppressed=[];self.style=False;self.script=False;self.used={entry};self.scripts=0;self.blocked_scripts=0;self.blocked_assets=0
 def handle_starttag(self,tag,attrs):
  tag=tag.lower()
  if self.suppressed:
   if tag in DROP_CONTENT:self.suppressed.append(tag)
   return
  if tag in DROP_CONTENT:
   if tag!='embed':self.suppressed.append(tag)
   return
  values=dict(attrs)
  if tag=='script':
   kind=values.get('type','');kind=kind if kind in {'','text/javascript','application/javascript','module','application/json'} else 'text/plain';opening='<script'+(' type="'+kind+'"' if kind else '')+'>'
   if 'src' in values:
    try:path=local_reference(self.entry,values['src'],self.files,'.js');text=self.files[path].decode('utf-8');self.used.add(path);self.output.append(opening+javascript(text)+'</script>');self.scripts+=1
    except (WorkspaceError,UnicodeError,ValueError):self.blocked_scripts+=1
    self.suppressed.append('script');return
   self.script=True;self.scripts+=1;self.output.append(opening);return
  if tag=='style':self.style=True;self.output.append('<style>');return
  if tag=='link':
   if values.get('rel','').lower()=='stylesheet':
    try:path=local_reference(self.entry,values.get('href'),self.files,'.css');text=self.files[path].decode('utf-8');self.used.add(path);self.output.append('<style>'+css(text)+'</style>')
    except (WorkspaceError,UnicodeError,ValueError):self.blocked_assets+=1
   return
  if tag not in ALLOWED:return
  safe=[]
  for key,value in attrs:
   key=key.lower()
   if key=='style' and value is not None:safe.append(('style',css(value)))
   elif re.fullmatch('on[a-z]+',key) and isinstance(value,str) and len(value)<=2000:safe.append((key,value))
   elif (key in ATTRS or key.startswith('aria-') or key.startswith('data-')) and not key.startswith('on') and (value is None or len(value)<=2000):safe.append((key,value))
  if tag=='button':safe=[(k,v) for k,v in safe if k!='type']+[('type','button')]
  self.output.append('<'+tag+''.join(' '+escape(k,quote=True)+('="'+escape(v,quote=True)+'"' if v is not None else '') for k,v in safe)+'>')
 def handle_startendtag(self,tag,attrs):
  self.handle_starttag(tag,attrs)
  if tag.lower() not in VOID:self.handle_endtag(tag)
 def handle_endtag(self,tag):
  tag=tag.lower()
  if self.suppressed:
   if tag==self.suppressed[-1]:self.suppressed.pop()
   return
  if tag=='script':self.script=False;self.output.append('</script>')
  elif tag=='style':self.style=False;self.output.append('</style>')
  elif tag in ALLOWED and tag not in VOID:self.output.append('</'+tag+'>')
 def handle_data(self,data):
  if not self.suppressed:self.output.append(javascript(data) if self.script else css(data) if self.style else escape(data))
 def handle_entityref(self,name):self.handle_data('&'+name+';')
 def handle_charref(self,name):self.handle_data('&#'+name+';')

class ArtifactDelivery:
 def __init__(self,workspace):self.workspace=workspace
 def listing(self):
  files=self.workspace.files()
  return dict(ok=True,workspace_sha256=manifest(files),files=[dict(path=name,bytes=len(raw),sha256=digest(raw),previewable=name.lower().endswith('.html')) for name,raw in sorted(files.items())],total_files=len(files),total_bytes=sum(map(len,files.values())),preview_mode='interactive_html_css_js',JavaScript_execution=True)
 def download(self,path,expected_sha256):
  relative(path);files=self.workspace.files();need(path in files,'artifact_not_found');raw=files[path];need(isinstance(expected_sha256,str) and expected_sha256==digest(raw),'artifact_version_changed')
  return dict(body=raw,filename=PurePosixPath(path).name,content_type=TYPES.get(PurePosixPath(path).suffix.lower(),'text/plain; charset=utf-8'),sha256=digest(raw),attachment=True)
 def project_zip(self,expected_workspace_sha256,directory=''):
  relative(directory,directory=True);files=self.workspace.files();need(expected_workspace_sha256==manifest(files),'artifact_workspace_changed');prefix=directory+'/' if directory else '';selected={name:raw for name,raw in files.items() if name.startswith(prefix)};need(selected,'artifact_project_not_found')
  output=io.BytesIO()
  with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as archive:
   for name,raw in sorted(selected.items()):
    item=zipfile.ZipInfo(name);item.compress_type=zipfile.ZIP_DEFLATED;item.external_attr=0o100600<<16;archive.writestr(item,raw)
  raw=output.getvalue();need(len(raw)<=2*1024*1024,'artifact_zip_bound')
  return dict(body=raw,filename=(PurePosixPath(directory).name if directory else 'ME3-artifacts')+'.zip',content_type='application/zip',sha256=digest(raw),workspace_sha256=expected_workspace_sha256,attachment=True)
 def preview(self,path,expected_workspace_sha256):
  relative(path);need(path.lower().endswith('.html'),'static_HTML_preview_required');files=self.workspace.files();need(expected_workspace_sha256==manifest(files),'artifact_workspace_changed');need(path in files,'artifact_not_found')
  try:source=files[path].decode('utf-8')
  except UnicodeError:raise WorkspaceError('artifact_UTF8_required') from None
  parser=StaticHTML(path,files);parser.feed(source);parser.close()
  html='<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="'+escape(PREVIEW_CSP,quote=True)+'"><meta name="referrer" content="no-referrer">'+''.join(parser.output)
  need(len(html.encode())<=2*1024*1024,'artifact_preview_bound')
  return dict(ok=True,path=path,workspace_sha256=expected_workspace_sha256,html=html,sandbox=PREVIEW_SANDBOX,csp=PREVIEW_CSP,mode='interactive_html_css_js',JavaScript_execution=True,included_scripts=parser.scripts,blocked_scripts=parser.blocked_scripts,blocked_stylesheets=parser.blocked_assets,assets=[dict(path=name,sha256=digest(files[name])) for name in sorted(parser.used)],external_subresources=False,notice='Sandboxed HTML/CSS/JavaScript preview. External assets, network APIs, forms, popups and parent access are blocked. JavaScript can navigate its own preview frame. Backend services and unbundled module imports are unavailable; download the complete project source.')
