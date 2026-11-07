"""Bounded local tag reading and search. No decoder, URL, path input or writes."""
import json,re,unicodedata
from pathlib import Path

TAG_LIMIT=262144

def clean(value,limit=96):
    return ''.join(c for c in value if c.isprintable()).strip()[:limit]

def text_frame(data):
    if not data:return ''
    codec={0:'latin-1',1:'utf-16',2:'utf-16-be',3:'utf-8'}.get(data[0])
    if codec is None:return ''
    try:return clean(data[1:].decode(codec).split('\0',1)[0])
    except UnicodeError:return ''

def syncsafe(data):
    if len(data)!=4 or any(x&128 for x in data):raise ValueError('invalid_tag_size')
    return sum(x<<(7*(3-i)) for i,x in enumerate(data))

def metadata(f,suffix):
    """Read ID3v2.3/2.4, ID3v1 and FLAC comments; unsupported tags stay unknown."""
    tags={};source=[];f.seek(0);header=f.read(10)
    if suffix=='.mp3' and header[:3]==b'ID3':
        version=header[3]
        try:size=syncsafe(header[6:10])
        except ValueError:size=TAG_LIMIT+1
        # Unsynchronised/extended/compressed forms are deliberately not guessed.
        if version in {3,4} and not header[5]&0xC0 and size<=TAG_LIMIT:
            block=f.read(size);pos=0
            while pos+10<=len(block):
                frame=block[pos:pos+10];name=frame[:4];pos+=10
                if name==bytes(4):break
                if not re.fullmatch(b'[A-Z0-9]{4}',name):break
                try:n=syncsafe(frame[4:8]) if version==4 else int.from_bytes(frame[4:8],'big')
                except ValueError:break
                if n<=0 or pos+n>len(block):break
                key={b'TIT2':'title',b'TPE1':'artist',b'TALB':'album'}.get(name)
                if key and frame[8:10]==bytes(2):
                    value=text_frame(block[pos:pos+n])
                    if value:tags[key]=value
                pos+=n
            if tags:source.append('ID3v2')
    if suffix=='.mp3':
        f.seek(0,2)
        if f.tell()>=128:
            f.seek(-128,2);tail=f.read(128)
            if tail[:3]==b'TAG':
                for key,data in [('title',tail[3:33]),('artist',tail[33:63]),('album',tail[63:93])]:
                    value=clean(data.split(b'\0',1)[0].decode('latin-1'))
                    if value and key not in tags:tags[key]=value
                source.append('ID3v1')
    if suffix=='.flac' and header[:4]==b'fLaC':
        f.seek(4);remaining=TAG_LIMIT
        while remaining>=4:
            head=f.read(4)
            if len(head)!=4:break
            n=int.from_bytes(head[1:],'big');remaining-=4
            if n>remaining:break
            data=f.read(n);remaining-=n
            if len(data)!=n:break
            if head[0]&127==4:
                try:
                    pos=4+int.from_bytes(data[:4],'little');count=int.from_bytes(data[pos:pos+4],'little');pos+=4
                    if pos>len(data) or count>256:break
                    for _ in range(count):
                        if pos+4>len(data):raise ValueError()
                        size=int.from_bytes(data[pos:pos+4],'little');pos+=4
                        if size>len(data)-pos:raise ValueError()
                        item=data[pos:pos+size].decode('utf-8');pos+=size
                        key,sep,value=item.partition('=');key=key.casefold()
                        if sep and key in {'title','artist','album'} and clean(value):tags[key]=clean(value)
                    if tags:source.append('FLAC_VORBIS_COMMENT')
                except (ValueError,UnicodeError):pass
            if head[0]&128:break
    return tags,source

def describe(catalog,row):
    f,version=catalog.open(row['track_id'])
    try:
        tags,sources=metadata(f,Path(row['track_id']).suffix.lower())
        now=__import__('os').fstat(f.fileno())
        if (now.st_size,now.st_mtime_ns)!=(version['size'],version['mtime_ns']):raise ValueError('music_file_changed')
    finally:f.close()
    return {**row,'title':tags.get('title',clean(Path(row['track_id']).stem)),
            'artist':tags.get('artist'),'album':tags.get('album'),
            'metadata_source':sources or ['FILENAME_TITLE_ONLY'],
            'artist_known':bool(tags.get('artist'))}

def normalized(text):
    return ' '.join(''.join(c for c in unicodedata.normalize('NFKD',text.casefold()) if not unicodedata.combining(c)).split())

def page(rows,limit,offset,total,available=True):
    if type(limit) is not int or not 1<=limit<=25 or type(offset) is not int or offset<0:raise ValueError('music_pagination_invalid')
    result=[]
    for row in rows[offset:offset+limit]:
        # The shared registry checks JSON serialized size including Unicode escapes.
        if len(json.dumps(result+[row]))>9500:break
        result.append(row)
    end=offset+len(result)
    return dict(available=available,tracks=result,total_tracks=total,matched_tracks=len(rows),next_offset=end if end<len(rows) else None,read_only=True)

def search(catalog,query,field='any',limit=20,offset=0):
    if not isinstance(query,str) or not 1<=len(query.strip())<=120 or not all(c.isprintable() for c in query):raise ValueError('music_query_invalid')
    if field not in {'any','title','artist','album'}:raise ValueError('music_search_field_invalid')
    inventory=catalog.inventory();tokens=normalized(query).split();rows=[]
    for row in inventory['tracks']:
        item=describe(catalog,row)
        fields=[item.get(k) or '' for k in (['title','artist','album','track_id'] if field=='any' else [field])]
        haystack=normalized(' '.join(fields))
        if all(token in haystack for token in tokens):rows.append(item)
    return page(rows,limit,offset,len(inventory['tracks']),inventory['available'])
