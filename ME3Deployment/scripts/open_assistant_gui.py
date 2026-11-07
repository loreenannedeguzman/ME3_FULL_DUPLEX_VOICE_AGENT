"""Pi desktop entry: open the local GUI, starting standby only when absent."""
import argparse,http.cookiejar,json,shutil,socket,subprocess,sys,time,urllib.error,urllib.request
from html.parser import HTMLParser
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
URL='http://127.0.0.1:8765/'

def browser_command():
    browser=shutil.which('chromium')
    if not browser:raise RuntimeError('The installed Chromium browser could not be found.')
    profile=ROOT/'.tmp/gui-browser-profile'
    profile.mkdir(mode=0o700,parents=True,exist_ok=True)
    # Scope compatibility flags and profile to ME3. Keep Chromium sandboxing intact.
    return [browser,'--ozone-platform=x11','--user-data-dir='+str(profile),'--no-first-run','--app='+URL]

def running():
    # Ignore proxy environment variables for this strictly local readiness check.
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(URL,timeout=.7) as response:
            body=response.read(32768)
            # The deployed screen has no lab title. Require its fixed form,
            # application asset and session token markers, not a generic200.
            if all(marker in body for marker in (b'<title>ME3</title>',b'name="me3-csrf"',b'id="composer"',b'src="/app.js"')):return True
        raise RuntimeError('Port 8765 belongs to another application; it was not changed.')
    except urllib.error.HTTPError:
        raise RuntimeError('Port 8765 did not return the ME3 interface; it was not changed.') from None
    except (urllib.error.URLError,TimeoutError,ConnectionError):return False

class _CSRF(HTMLParser):
    def __init__(self):super().__init__();self.token=None
    def handle_starttag(self,tag,attrs):
        values=dict(attrs)
        if tag=='meta' and values.get('name')=='me3-csrf':self.token=values.get('content')

def new_conversation():
    # Only an explicit desktop launch flag requests this authenticated reset.
    # A refresh, server restart, recovery or plain browser open never clears.
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    with opener.open(URL,timeout=5) as response:html=response.read(32768).decode('utf-8')
    parser=_CSRF();parser.feed(html)
    if not isinstance(parser.token,str) or not 16<=len(parser.token)<=256:raise RuntimeError('ME3 session token unavailable; the conversation was preserved.')
    with opener.open(URL+'api/state',timeout=10) as response:state=json.load(response)
    if state.get('active') is not False or state.get('call',{}).get('held') is not False:raise RuntimeError('Stop the current ME3 conversation or end its call before starting a new one. Its history was preserved.')
    cid=state.get('conversation_id')
    if not isinstance(cid,str) or len(cid)!=32:raise RuntimeError('The normal ME3 conversation identity was unavailable; its history was preserved.')
    payload=json.dumps(dict(action='clear',expected_conversation_id=cid)).encode('utf-8')
    command=urllib.request.Request(URL+'api/command',data=payload,headers={'Content-Type':'application/json','Origin':URL.rstrip('/'),'X-ME3-CSRF':parser.token},method='POST')
    try:
        with opener.open(command,timeout=10) as response:result=json.load(response)
    except urllib.error.HTTPError as error:raise RuntimeError('ME3 could not archive and start a new conversation. Existing history was preserved; inspect its status.') from error
    if result.get('ok') is not True or result.get('conversation_id') in (None,cid):raise RuntimeError('A fresh ME3 conversation was not verified. Do not restart automatically.')
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');parser.add_argument('--new-conversation',action='store_true',help='Archive the stopped conversation and open a fresh one; used by the desktop icon.');args=parser.parse_args()
    if args.check:
        print(json.dumps({'root':str(ROOT),'gui_running':running(),'python_present':(ROOT/'.venv/bin/python').is_file(),'browser_opener':shutil.which('chromium'),'browser_backend':'x11','dedicated_profile':str(ROOT/'.tmp/gui-browser-profile'),'audio_opened':False,'provider_calls':0,'synthesis_calls':0}));return 0
    import fcntl
    (ROOT/'.tmp').mkdir(exist_ok=True)
    with (ROOT/'.tmp/gui-desktop-launch.lock').open('a') as lease:
        fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if not running():
            # Never kill or replace a listener merely because its readiness failed.
            with socket.socket() as probe:probe.bind(('127.0.0.1',8765))
            with (ROOT/'.tmp/gui-desktop.log').open('ab') as output:
                child=subprocess.Popen([str(ROOT/'.venv/bin/python'),'-B','-m','onit_candidate.gui','--port','8765'],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=output,stderr=output,start_new_session=True)
            end=time.monotonic()+10
            while time.monotonic()<end:
                if running():break
                if child.poll() is not None:raise RuntimeError('ME3 standby could not start. See .tmp/gui-desktop.log.')
                time.sleep(.2)
            else:raise RuntimeError('ME3 standby was not ready within ten seconds. No voice session was started.')
        if args.new_conversation:new_conversation()
    with (ROOT/'.tmp/gui-browser.log').open('ab') as output:
        browser=subprocess.Popen(browser_command(),cwd=ROOT,stdin=subprocess.DEVNULL,stdout=output,stderr=output,start_new_session=True)
    (ROOT/'.tmp/gui-browser-launch.pid').write_text(str(browser.pid))
    return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except (OSError,RuntimeError) as error:
        # Local desktop notification only; never send a message to another person.
        message=str(error) if isinstance(error,RuntimeError) else 'ME3 could not open safely. Check the local GUI launcher log.'
        if shutil.which('zenity'):subprocess.Popen(['zenity','--error','--title=ME3 Assistant','--text='+message],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        elif shutil.which('notify-send'):subprocess.Popen(['notify-send','ME3 Assistant',message],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        print(message,file=sys.stderr);raise SystemExit(1)
