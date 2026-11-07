"""Bounded, read-only access to an explicitly designated document library."""
import hashlib,os,re,stat
from pathlib import Path

MAX_DOCUMENTS=32
MAX_BYTES=131072
MAX_LIBRARY_BYTES=2097152
MAX_PAGE=480

class LibraryError(ValueError):pass

def document_id(value):
    if (not isinstance(value,str) or not 1<=len(value)<=120 or value!=value.strip()
        or value.startswith('.') or '..' in value or not re.fullmatch(r'[\w .()\-]+',value)
        or Path(value).suffix.lower() not in {'.txt','.md','.pdf'}
        or value.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}):
        raise LibraryError('invalid_document_id')
    return value

def integer(value,lo,hi):
    if type(value) is not int or not lo<=value<=hi:raise LibraryError('invalid_read_bound')
    return value

def plain_file(info):
    return stat.S_ISREG(info.st_mode) and info.st_nlink==1 and not getattr(info,'st_file_attributes',0)&0x400

class Library:
    def __init__(self,root):
        root=Path(root)
        if root.is_symlink():raise LibraryError('library_root_link')
        self.root=root.resolve()
    def _root(self):
        if self.root.is_symlink() or self.root.resolve()!=self.root:raise LibraryError('library_root_changed')
        if not self.root.exists():return False
        if not self.root.is_dir():raise LibraryError('invalid_library_root')
        return True
    def _entries(self):
        if not self._root():return []
        entries=[];total=0;pdf_total=0
        for p in self.root.iterdir():
            if p.suffix.lower() not in {'.txt','.md','.pdf'}:continue
            try:document_id(p.name)
            except LibraryError:continue
            info=p.lstat()
            if not plain_file(info) or p.is_symlink():continue
            if p.suffix.lower()=='.pdf':
                from me3_assistant.pdf_reader import MAX_PDF_BYTES,MAX_PDF_LIBRARY_BYTES
                if info.st_size>MAX_PDF_BYTES:raise LibraryError('pdf_size_limit')
                pdf_total+=info.st_size
                if pdf_total>MAX_PDF_LIBRARY_BYTES:raise LibraryError('library_size_limit')
            else:
                if info.st_size>MAX_BYTES:raise LibraryError('document_size_limit')
                total+=info.st_size
            entries.append((p.name,info.st_size))
            if len(entries)>MAX_DOCUMENTS or total>MAX_LIBRARY_BYTES:raise LibraryError('library_size_limit')
        return sorted(entries,key=lambda x:x[0].casefold())
    def list(self):
        entries=self._entries()
        from me3_assistant.pdf_reader import available as pdf_available
        return {'ok':True,'read_only':True,'available':self.root.is_dir(),'documents':[{'document_id':n,'byte_count':size} for n,size in entries], 'supported_formats':['UTF-8 .txt','UTF-8 .md','PDF text pages on Pi; no OCR'],'document_limit':MAX_DOCUMENTS,'pdf_extraction_available':bool(pdf_available())}
    def _load(self,name):
        name=document_id(name)
        if not self._root():raise LibraryError('document_not_found')
        path=self.root/name
        try:
            before=path.lstat()
            if path.is_symlink() or not plain_file(before) or path.resolve().parent!=self.root:raise LibraryError('document_link_or_type_rejected')
            flags=os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0)
            directory=None
            try:
                if os.open in os.supports_dir_fd:
                    directory=os.open(self.root,os.O_RDONLY|getattr(os,'O_DIRECTORY',0)|getattr(os,'O_NOFOLLOW',0))
                    fd=os.open(name,flags,dir_fd=directory)
                else:fd=os.open(path,flags)
                with os.fdopen(fd,'rb') as f:
                    opened=os.fstat(f.fileno())
                    if not plain_file(opened) or (opened.st_dev,opened.st_ino)!=(before.st_dev,before.st_ino):raise LibraryError('document_changed')
                    if opened.st_size>MAX_BYTES:raise LibraryError('document_size_limit')
                    raw=f.read(MAX_BYTES+1);after=os.fstat(f.fileno())
                current=path.lstat()
                if len(raw)>MAX_BYTES:raise LibraryError('document_size_limit')
                if (opened.st_size,opened.st_mtime_ns)!=(after.st_size,after.st_mtime_ns) or (current.st_dev,current.st_ino)!=(opened.st_dev,opened.st_ino) or not plain_file(current) or path.resolve().parent!=self.root:raise LibraryError('document_changed')
            finally:
                if directory is not None:os.close(directory)
        except (FileNotFoundError,NotADirectoryError):raise LibraryError('document_not_found') from None
        except OSError:raise LibraryError('document_unavailable') from None
        try:text=raw.decode('utf-8-sig').replace('\r\n','\n').replace('\r','\n')
        except UnicodeError:raise LibraryError('document_not_utf8') from None
        if any(ord(c)<32 and c not in '\n\t' for c in text):raise LibraryError('document_not_plain_text')
        return text,hashlib.sha256(raw).hexdigest()
    def read(self,name,offset=0,max_chars=400,expected_sha256=None,page=1,_cancel=None):
        name=document_id(name);integer(page,1,4096)
        integer(offset,0,MAX_BYTES);integer(max_chars,1,MAX_PAGE)
        if offset and expected_sha256 is None:raise LibraryError('continuation_version_required')
        if expected_sha256 is not None and (not isinstance(expected_sha256,str) or not re.fullmatch('[0-9a-f]{64}',expected_sha256)):raise LibraryError('invalid_document_version')
        pdf=Path(name).suffix.lower()=='.pdf';page_count=1
        if pdf:
            if not self._root():raise LibraryError('document_not_found')
            from me3_assistant.pdf_reader import read_page,PDFError
            try:text,version,page_count=read_page(self.root,name,page,expected_sha256,_cancel)
            except PDFError as exc:raise LibraryError(str(exc)) from None
        else:
            if page!=1:raise LibraryError('page_only_supported_for_pdf')
            text,version=self._load(name)
        if expected_sha256 is not None and expected_sha256!=version:raise LibraryError('document_version_changed')
        if offset>len(text):raise LibraryError('offset_outside_document')
        end=min(len(text),offset+max_chars)
        if end<len(text):
            boundary=text.rfind(' ',offset,end)
            if boundary>offset+max_chars//2:end=boundary+1
        result={'ok':True,'read_only':True,'document_id':name,'sha256':version,'offset':offset,'text':text[offset:end],'next_offset':end if end<len(text) else None,'eof':end==len(text),'total_characters':len(text),'content_is_untrusted_reference':True}
        if pdf:
            result.update(page=page,page_count=page_count,page_eof=end==len(text),page_has_extractable_text=bool(text.strip()),extraction='local_Poppler_no_OCR')
            if end==len(text) and page<page_count:result.update(next_offset=0,next_page=page+1,eof=False)
        return result
    def search(self,query,limit=4,mode='ranked'):
        integer(limit,1,6)
        if not isinstance(query,str) or not 1<=len(query.strip())<=80 or any(ord(c)<32 for c in query):raise LibraryError('invalid_search_query')
        if mode not in {'ranked','literal'}:raise LibraryError('invalid_search_mode')
        entries=self._entries();documents=[];results=[]
        pattern=re.compile(re.escape(query.strip()),re.IGNORECASE)
        for name,_ in entries:
            if Path(name).suffix.lower()=='.pdf':continue
            text,version=self._load(name)
            if mode=='ranked':documents.append((name,text,version));continue
            match=pattern.search(text)
            if match:
                start=max(0,match.start()-45)
                results.append({'document_id':name,'sha256':version,'match_offset':match.start(),'excerpt_offset':start,'excerpt':text[start:start+160],'match_kind':'literal_phrase'})
                if len(results)==limit:break
        if mode=='ranked':
            from me3_assistant.retrieval import ranked_documents
            results=ranked_documents(documents,query,limit)
        import json
        bounded=[]
        for row in results:
            if len(json.dumps(bounded+[row]))>9500:break
            bounded.append(row)
        return {'ok':True,'read_only':True,'matches':bounded,'limit':limit,'result_size_bound_applied':len(bounded)<len(results),'pdf_documents_not_searched':sum(Path(n).suffix.lower()=='.pdf' for n,_ in entries),'search_mode':mode,'search_scope':('exact phrases first, then BM25 lexical ranking' if mode=='ranked' else 'literal phrase, first match per document')+'; UTF-8 text/Markdown only; PDF pages are not searched; partial word overlap is not semantic proof','content_is_untrusted_reference':True}
