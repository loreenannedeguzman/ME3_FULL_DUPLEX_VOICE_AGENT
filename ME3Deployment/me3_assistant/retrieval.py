"""Bounded lexical ranking adapted from Baby Onit's Okapi BM25.

Upstream: sibyl-oracles/baby-onit, df8c5be0ae0dbe9af2130bb889df1a0f68b49c90,
baby_onit.py::_tokens/_bm25. Apache-2.0; see third_party/baby-onit.
ME3 changes: Unicode tokens, per-document counters, stable ties, exact offsets,
no global file index, no filesystem/network/model access or stale cache reuse.
"""
import math,re,unicodedata
from collections import Counter

STOP=frozenset('a an and are as at be by for from has have in is it its of on or that the to was were will with'.split())

def tokens(text):
    return [t for t in re.findall(r'[^\W_]+',unicodedata.normalize('NFKC',text).casefold()) if t not in STOP]

def chunks(text,size=1200,overlap=150):
    """Return original character offsets, never reconstructed or normalized text."""
    start=0
    while start<len(text):
        end=min(start+size,len(text))
        if end<len(text):
            boundary=max(text.rfind('\n',start+size//2,end),text.rfind(' ',start+size//2,end))
            if boundary>start:end=boundary+1
        yield start,text[start:end]
        if end==len(text):break
        start=max(start+1,end-overlap)

def bm25(corpus,query):
    """Return positive (score, original index) hits in deterministic rank order."""
    docs=[Counter(tokens(d)) for d in corpus];terms=set(tokens(query));n=len(docs)
    avg=sum(sum(d.values()) for d in docs)/max(n,1)
    if not terms or not avg:return []
    df=Counter(t for d in docs for t in d)
    result=[]
    for i,d in enumerate(docs):
        length=sum(d.values());score=0.0
        for term in terms&d.keys():
            count=d[term]
            score+=math.log(1+(n-df[term]+.5)/(df[term]+.5))*count*2.5/(count+1.5*(.25+.75*length/avg))
        if score>0:result.append((score,i))
    return sorted(result,key=lambda hit:(-hit[0],hit[1]))

def ranked_documents(documents,query,limit):
    """One exact excerpt per file. Exact phrases precede additional lexical hits."""
    phrase=re.compile(re.escape(query.strip()),re.IGNORECASE);rows=[];seen=set()
    for name,text,version in documents:
        match=phrase.search(text)
        if match:
            start=max(0,match.start()-45)
            rows.append(dict(document_id=name,sha256=version,match_offset=match.start(),excerpt_offset=start,excerpt=text[start:start+160],match_kind='literal_phrase'))
            seen.add(name)
    corpus=[];owners=[]
    for name,text,version in documents:
        if name in seen:continue
        for offset,chunk in chunks(text):corpus.append(chunk);owners.append((name,text,version,offset))
    query_terms=set(tokens(query))
    for score,i in bm25(corpus,query):
        name,text,version,offset=owners[i]
        if name in seen:continue
        chunk=corpus[i]
        # Scan original words to preserve source offsets despite NFKC/casefold.
        matches=[m for m in re.finditer(r'[^\W_]+',chunk) if set(tokens(m.group()))&query_terms]
        if not matches:continue
        match_offset=offset+matches[0].start();start=max(0,match_offset-45)
        matched=sorted(query_terms&set(tokens(chunk)))
        rows.append(dict(document_id=name,sha256=version,match_offset=match_offset,excerpt_offset=start,excerpt=text[start:start+160],match_kind='lexical_bm25',score=round(score,6),matched_terms=matched))
        seen.add(name)
        if len(rows)>=limit:break
    return rows[:limit]
