"""Public web-search discovery through one fixed browser endpoint; no page fetch.

Selective Baby Onit web_search capability, implemented through ME3's existing
cancellable Chromium lifecycle. No provider SDK, keys, fallback or shell added.
"""
import hashlib,re
from datetime import datetime,timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit,parse_qs,urlencode,unquote
from me3_assistant.research import browser_dom,ResearchError

def public_result_url(value):
    if not isinstance(value,str) or len(value)>2000 or any(ord(c)<32 for c in value):raise ValueError('invalid_result_url')
    if value.startswith('//'):value='https:'+value
    u=urlsplit(value)
    if u.hostname in {'duckduckgo.com','html.duckduckgo.com'} and u.path.startswith('/l/'):
        value=parse_qs(u.query).get('uddg',[''])[0];u=urlsplit(value)
    try:port=u.port
    except ValueError:raise ValueError('invalid_result_url') from None
    host=(u.hostname or '').lower().rstrip('.')
    import ipaddress
    try:ip=ipaddress.ip_address(host)
    except ValueError:ip=None
    if (u.scheme!='https' or u.username or u.password or port not in (None,443) or not host or '.' not in host or host.endswith(('.local','.localhost','.internal','.test','.invalid','.example')) or host=='localhost' or ip is not None):raise ValueError('non_public_result_url')
    if any(ord(c)<32 for c in unquote(value)):raise ValueError('invalid_result_url')
    return value

class SearchResults(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.rows=[];self.current=None;self.capture=None;self.depth=0;self.challenge=False;self.no_results=False
    def handle_starttag(self,tag,attrs):
        a=dict(attrs);classes=a.get('class','').split()
        if 'anomaly' in a.get('id','') or 'challenge' in a.get('id',''):self.challenge=True
        if any(c=='no-results' or c.startswith('no-results__') for c in classes):self.no_results=True
        if tag=='a' and 'result__a' in classes:
            self.current=None
            try:url=public_result_url(a.get('href',''))
            except ValueError:pass
            else:
                if len(self.rows)<20:self.current=dict(title='',url=url,snippet='');self.rows.append(self.current)
            self.capture='title';self.depth=1
        elif self.capture and tag not in {'br','img','input','hr','meta','link'}:self.depth+=1
        elif 'result__snippet' in classes:self.capture='snippet';self.depth=1
    def handle_endtag(self,tag):
        if tag in {'br','img','input','hr','meta','link'}:return
        if self.capture:
            self.depth-=1
            if self.depth<=0:self.capture=None
    def handle_data(self,data):
        if self.capture and self.current:self.current[self.capture]+=data
    def result(self,query,limit):
        if self.challenge:raise ResearchError('web_search_access_challenge')
        rows=[];seen=set()
        for row in self.rows:
            row={k:re.sub(r'\s+',' ',v).strip() for k,v in row.items()}
            row['title']=row['title'][:240];row['snippet']=row['snippet'][:400]
            if row['title'] and row['url'] not in seen:rows.append(row);seen.add(row['url'])
        if not rows and not self.no_results:raise ResearchError('web_search_results_unavailable')
        return dict(ok=True,available=True,query=query,results=rows[:limit],source='DuckDuckGo public HTML search',retrieved_at_utc=datetime.now(timezone.utc).isoformat(),read_only=True,reference_is_untrusted=True,coverage='Search titles/links/snippets only; page content not fetched, claims not independently verified, no playback started.')

async def search_web(query,limit=4):
    if not isinstance(query,str) or not 1<=len(query.strip())<=160 or any(ord(c)<32 for c in query) or type(limit) is not int or not 1<=limit<=5:raise ResearchError('invalid_web_search_query')
    query=query.strip();url='https://html.duckduckgo.com/html/?'+urlencode({'q':query})
    dom=await browser_dom(url);parser=SearchResults();parser.feed(dom)
    result=parser.result(query,limit);result['source_dom_sha256']=hashlib.sha256(dom.encode()).hexdigest();return result
