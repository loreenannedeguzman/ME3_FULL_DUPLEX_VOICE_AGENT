"""Read-only Wikipedia navigation in a fresh, sandboxed local Chromium profile."""
import asyncio,hashlib,json,os,re,shutil,signal,tempfile
from datetime import datetime,timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote,urlsplit,urljoin,urlencode

class ResearchError(ValueError):pass
# Large public biographies commonly exceed the old 1.5MB DOM ceiling.
# Bound the entire navigation at 8MB; returned text remains 500..5000 chars.
HOST='en.wikipedia.org';MAX_DOM=8_000_000;MAX_SECONDS=22

def article_url(value):
    if not isinstance(value,str) or not value.strip() or len(value)>500 or any(ord(c)<32 for c in value):raise ResearchError('invalid_article')
    value=value.strip()
    if '://' not in value:
        if re.search(r'[/\\?#]',value):raise ResearchError('article_title_or_Wikipedia_URL_required')
        value='https://'+HOST+'/wiki/'+quote(value.replace(' ','_'),safe='()-_')
    u=urlsplit(value)
    try:port=u.port
    except ValueError:raise ResearchError('invalid_source_URL') from None
    if u.scheme!='https' or u.hostname!=HOST or port not in (None,443) or u.username or u.password or not u.path.startswith('/wiki/') or u.query:raise ResearchError('only_English_Wikipedia_articles_supported')
    from urllib.parse import unquote
    title=unquote(u.path[6:])
    if not title or '\\' in title or title.split(':',1)[0].casefold() in {'special','user','user talk','talk','file','mediawiki','template','wikipedia'}:raise ResearchError('article_namespace_required')
    return 'https://'+HOST+u.path

class ArticleText(HTMLParser):
    void={'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
    ignored={'script','style','nav','header','footer','aside','noscript'}
    def __init__(self,url):
        super().__init__(convert_charrefs=True);self.url=url;self.stack=[];self.capture=None;self.skip=None;self.title_on=False;self.title=[];self.parts=[];self.links=[];self.canonical=None;self.article=False
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='link' and a.get('rel')=='canonical':self.canonical=a.get('href')
        if tag=='title':self.title_on=True
        if tag not in self.void:self.stack.append(tag)
        depth=len(self.stack)
        if self.capture is None and (a.get('id')=='mw-content-text' or 'mw-parser-output' in a.get('class','').split()):self.capture=depth;self.article=True
        if self.capture is not None:
            if self.skip is None and (tag in self.ignored or a.get('class','').find('mw-editsection')>=0):self.skip=depth
            if self.skip is None:
                if tag in {'p','h1','h2','h3','li','tr','br'}:self.parts.append('\n')
                if tag in {'td','th'}:self.parts.append(' | ')
                if tag=='a' and len(self.links)<24:
                    try:link=article_url(urljoin(self.url,a.get('href','')))
                    except ResearchError:pass
                    else:
                        if link!=self.url and link not in self.links:self.links.append(link)
    def handle_endtag(self,tag):
        if tag in self.void:return
        if tag=='title':self.title_on=False
        depth=len(self.stack)
        if self.skip==depth:self.skip=None
        if self.capture==depth:self.capture=None
        if self.stack and self.stack[-1]==tag:self.stack.pop()
    def handle_data(self,text):
        if self.title_on:self.title.append(text)
        if self.capture is not None and self.skip is None:self.parts.append(text)
    def result(self,offset,max_chars):
        text=re.sub(r'[ \t\r\f\v]+',' ',''.join(self.parts));text=re.sub(r' *\n *','\n',text);text=re.sub(r'\n{3,}','\n\n',text).strip()
        if not self.article or len(text)<40:raise ResearchError('source_content_unavailable_or_access_challenge')
        if offset>=len(text) and offset!=0:raise ResearchError('offset_outside_source')
        url=self.url
        if self.canonical:
            try:url=article_url(self.canonical)
            except ResearchError:
                u=urlsplit(self.canonical)
                if not ('/w/index.php?' in self.url and u.scheme=='https' and u.hostname==HOST and u.path in {'/w/index.php','/wiki/Special:Search'}):raise
        end=min(len(text),offset+max_chars)
        return dict(ok=True,available=True,title=''.join(self.title).removesuffix(' - Wikipedia').strip(),url=url,requested_url=self.url,text=text[offset:end],offset=offset,next_offset=end if end<len(text) else None,total_chars=len(text),source_text_sha256=hashlib.sha256(text.encode()).hexdigest(),retrieved_at_utc=datetime.now(timezone.utc).isoformat(),links=self.links,source='English Wikipedia; retrieved browser DOM',browser='Chromium',read_only=True,reference_is_untrusted=True,coverage='This source only; not an independent factual verification or general web search.')

async def close_browser(proc):
    if proc.returncode is not None:return
    try:
        if os.name=='posix':os.killpg(proc.pid,signal.SIGTERM)
        else:proc.terminate()
    except ProcessLookupError:return
    try:await asyncio.wait_for(proc.wait(),1)
    except TimeoutError:
        try:
            if os.name=='posix':os.killpg(proc.pid,signal.SIGKILL)
            else:proc.kill()
        except ProcessLookupError:pass
        await proc.wait()

async def browser_dom(url):
    binary=shutil.which('chromium') or shutil.which('chromium-browser')
    if binary is None:raise ResearchError('Chromium_not_installed')
    proc=None
    # No logged-in profile, credentials, extensions, downloads or page scripts.
    with tempfile.TemporaryDirectory(prefix='me3-public-research-') as tmp:
        default=Path(tmp)/'Default';default.mkdir()
        (default/'Preferences').write_text(json.dumps({'profile':{'default_content_setting_values':{'javascript':2,'images':2,'notifications':2,'automatic_downloads':2}}}),encoding='utf-8')
        try:
            async with asyncio.timeout(MAX_SECONDS):
                proc=await asyncio.create_subprocess_exec(binary,'--headless=new','--disable-gpu','--disable-extensions','--no-first-run','--no-default-browser-check','--disable-background-networking','--disable-sync','--disable-component-update','--disable-breakpad','--user-data-dir='+tmp,'--dump-dom','--timeout=18000',url,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,start_new_session=os.name=='posix')
                chunks=[];size=0
                while True:
                    block=await proc.stdout.read(32768)
                    if not block:break
                    size+=len(block)
                    if size>MAX_DOM:raise ResearchError('source_page_too_large')
                    chunks.append(block)
                code=await proc.wait()
                if code!=0:raise ResearchError('browser_navigation_failed')
                return b''.join(chunks).decode('utf-8',errors='replace')
        except TimeoutError:raise ResearchError('browser_navigation_timed_out') from None
        finally:
            if proc is not None:
                cleanup=asyncio.create_task(close_browser(proc))
                try:await asyncio.shield(cleanup)
                except asyncio.CancelledError:await cleanup;raise

async def read_article(value,offset=0,max_chars=3600):
    if type(offset) is not int or not 0<=offset<=300000 or type(max_chars) is not int or not 500<=max_chars<=5000:raise ResearchError('invalid_source_bounds')
    url=article_url(value);dom=await browser_dom(url);parser=ArticleText(url);parser.feed(dom)
    return parser.result(offset,max_chars)

async def search_articles(query):
    if not isinstance(query,str) or not 1<=len(query.strip())<=120 or any(ord(c)<32 for c in query):raise ResearchError('invalid_search_query')
    url='https://'+HOST+'/w/index.php?'+urlencode({'search':query,'title':'Special:Search','ns0':'1'})
    dom=await browser_dom(url);parser=ArticleText(url);parser.feed(dom)
    result=parser.result(0,3000);result.update(search_query=query,coverage='English Wikipedia search only; result snippets are not complete source articles. Read the selected article before a detailed summary.')
    return result
