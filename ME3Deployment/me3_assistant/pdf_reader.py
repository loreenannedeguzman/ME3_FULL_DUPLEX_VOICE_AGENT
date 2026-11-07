"""Read one PDF page through existing Poppler, in a bounded credential-free child."""
import hashlib,os,re,shutil,stat,subprocess,sys,tempfile,time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
MAX_PDF_BYTES=32*1024*1024
MAX_PDF_LIBRARY_BYTES=128*1024*1024
MAX_PDF_PAGES=4096
MAX_OUTPUT=131072

class PDFError(ValueError):pass

def available():
    return sys.platform=='linux' and Path('/proc/self/fd').is_dir() and bool(shutil.which('pdfinfo') and shutil.which('pdftotext'))

def _plain(info):return stat.S_ISREG(info.st_mode) and info.st_nlink==1

def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():raise PDFError('pdf_read_cancelled')

def _hash(f,cancel):
    value=hashlib.sha256()
    while block:=f.read(1024*1024):_check_cancel(cancel);value.update(block)
    _check_cancel(cancel);return value.hexdigest()

def _run(role,fd,page,deadline,cancel=None):
    _check_cancel(cancel)
    remaining=deadline-time.monotonic()
    if remaining<=0:raise PDFError('pdf_extraction_timeout')
    folder=ROOT/'.tmp';folder.mkdir(exist_ok=True)
    # Poppler stdout goes to an auto-deleted bounded file, never an evidence log.
    with tempfile.TemporaryFile(dir=folder,prefix='pdf-page-') as out:
        env={'PATH':os.environ.get('PATH',''),'LC_ALL':'C','LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1','PYTHONIOENCODING':'utf-8','XDG_CACHE_HOME':str(folder/'pdf-cache')}
        proc=subprocess.Popen([sys.executable,'-B','-m','me3_assistant.pdf_worker',role,str(fd),str(page)],cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.DEVNULL,pass_fds=(fd,))
        try:
            while True:
                _check_cancel(cancel);remaining=deadline-time.monotonic()
                if remaining<=0:raise PDFError('pdf_extraction_timeout')
                try:code=proc.wait(timeout=min(.05,remaining));break
                except subprocess.TimeoutExpired:continue
        except BaseException:
            if proc.poll() is None:proc.kill();proc.wait(timeout=1)
            raise
        if code:raise PDFError('pdf_password_or_extraction_failed')
        if os.fstat(out.fileno()).st_size>MAX_OUTPUT:raise PDFError('pdf_page_text_limit')
        out.seek(0);raw=out.read(MAX_OUTPUT+1)
    try:return raw.decode('utf-8').replace('\r\n','\n').replace('\r','\n')
    except UnicodeError:raise PDFError('pdf_text_encoding_failed') from None

def read_page(root,name,page,expected_sha256=None,cancel=None):
    _check_cancel(cancel)
    if not available():raise PDFError('pdf_extractor_unavailable_requires_Pi_Poppler')
    root=Path(root);path=root/name;directory=None
    try:
        before=path.lstat()
        if path.is_symlink() or not _plain(before) or path.resolve().parent!=root:raise PDFError('document_link_or_type_rejected')
        directory=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=directory)
        with os.fdopen(fd,'rb') as f:
            info=os.fstat(f.fileno())
            if not _plain(info) or (info.st_dev,info.st_ino)!=(before.st_dev,before.st_ino):raise PDFError('document_changed')
            if info.st_size>MAX_PDF_BYTES:raise PDFError('pdf_size_limit')
            version=_hash(f,cancel);f.seek(0)
            if expected_sha256 is not None and version!=expected_sha256:raise PDFError('document_version_changed')
            deadline=time.monotonic()+3.5
            metadata=_run('info',f.fileno(),page,deadline,cancel);match=re.search(r'^Pages:\s+(\d+)\s*$',metadata,re.MULTILINE)
            if not match:raise PDFError('pdf_page_count_unavailable')
            count=int(match[1])
            if not 1<=count<=MAX_PDF_PAGES:raise PDFError('pdf_page_count_limit')
            if page>count:raise PDFError('pdf_page_outside_document')
            text=_run('text',f.fileno(),page,deadline,cancel)
            if any(ord(c)<32 and c not in '\n\t' for c in text):raise PDFError('pdf_text_invalid')
            after=os.fstat(f.fileno());current=path.lstat()
            stamp=lambda x:(x.st_dev,x.st_ino,x.st_size,x.st_mtime_ns)
            if stamp(info)!=stamp(after) or stamp(info)!=stamp(current) or not _plain(current) or path.is_symlink() or path.resolve().parent!=root:raise PDFError('document_changed')
            f.seek(0)
            if _hash(f,cancel)!=version:raise PDFError('document_changed')
            return text,version,count
    except (FileNotFoundError,NotADirectoryError):raise PDFError('document_not_found') from None
    except OSError:raise PDFError('pdf_document_unavailable') from None
    finally:
        if directory is not None:os.close(directory)
