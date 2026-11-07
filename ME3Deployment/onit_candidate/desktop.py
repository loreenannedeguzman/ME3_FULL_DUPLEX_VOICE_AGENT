"""Normal ME3 Conversation launcher; exactly one Onit GUI, no legacy fallback."""
import argparse,http.cookiejar,json,socket,subprocess,time,urllib.error,urllib.request
from pathlib import Path
from task_mcp.client import ROOT
from onit_candidate.identity import verify_candidate
from me3_assistant.guard import verify_sources
from scripts.open_assistant_gui import browser_command,new_conversation
URL='http://127.0.0.1:8765/'

def running(opener=None):
    opener=opener or urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    try:
        with opener.open(URL,timeout=1) as response:
            body=response.read(32768)
        if not all(s in body for s in [b'<title>ME3</title>',b'name="me3-csrf"',b'id="composer"',b'src="/app.js"']):raise RuntimeError('Port8765_is_not_the_ME3_interface')
        with opener.open(URL+'api/state',timeout=6) as response:state=json.load(response)
        if state.get('engine')!='full Onit candidate':raise RuntimeError('Port8765_has_a_different_planner_no_automatic_replacement')
        return True
    except urllib.error.HTTPError:raise RuntimeError('ME3_GUI_HTTP_error_no_automatic_replacement') from None
    except (urllib.error.URLError,TimeoutError,ConnectionError):return False

def ensure_standby():
    if running():return
    with socket.socket() as probe:probe.bind(('127.0.0.1',8765))
    with (ROOT/'.tmp/gui-desktop-onit.log').open('ab') as output:
        child=subprocess.Popen([str(ROOT/'.venv/bin/python'),'-B','-m','onit_candidate.gui','--port','8765','--warm-floor','--validated-delivery'],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=output,stderr=output,start_new_session=True)
    end=time.monotonic()+10
    while time.monotonic()<end:
        if running():return
        if child.poll() is not None:raise RuntimeError('ME3_standby_could_not_start')
        time.sleep(.2)
    raise RuntimeError('ME3_standby_not_ready_no_voice_session_started')

def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true');args=parser.parse_args(argv)
    verify_sources();verify_candidate(ROOT)
    if args.check:
        print(json.dumps(dict(url=URL,gui_running=running(),planner='full Onit',qualification='pending_remaining_gates',provider_calls=0,audio_opened=False)));return 0
    import fcntl
    (ROOT/'.tmp').mkdir(exist_ok=True)
    with (ROOT/'.tmp/gui-desktop-launch.lock').open('a') as lease:
        fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB);ensure_standby()
        new_conversation() # Explicit desktop-icon launch starts fresh; --check never resets.
    with (ROOT/'.tmp/gui-browser.log').open('ab') as output:
        browser=subprocess.Popen(browser_command(),cwd=ROOT,stdin=subprocess.DEVNULL,stdout=output,stderr=output,start_new_session=True)
    (ROOT/'.tmp/gui-browser-launch.pid').write_text(str(browser.pid));return 0

if __name__=='__main__':raise SystemExit(main())
