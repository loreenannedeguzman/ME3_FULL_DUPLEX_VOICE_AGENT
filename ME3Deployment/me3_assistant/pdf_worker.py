"""Private bounded Poppler launcher. No shell, model, network client or credentials."""
import os,shutil,sys

def main():
    if sys.platform!='linux' or len(sys.argv)!=4:raise ValueError('pdf_worker_contract')
    role,fd,page=sys.argv[1],int(sys.argv[2]),int(sys.argv[3])
    if role not in {'info','text'} or fd<3 or not 1<=page<=4096:raise ValueError('pdf_worker_contract')
    import resource
    resource.setrlimit(resource.RLIMIT_CPU,(2,2))
    resource.setrlimit(resource.RLIMIT_AS,(256*1024*1024,256*1024*1024))
    resource.setrlimit(resource.RLIMIT_FSIZE,(131072,131072))
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    executable=shutil.which('pdfinfo' if role=='info' else 'pdftotext')
    if not executable:raise ValueError('pdf_tool_missing')
    path='/proc/self/fd/'+str(fd)
    args=[executable,path] if role=='info' else [executable,'-f',str(page),'-l',str(page),'-enc','UTF-8','-nopgbrk',path,'-']
    os.execv(executable,args)

if __name__=='__main__':main()
