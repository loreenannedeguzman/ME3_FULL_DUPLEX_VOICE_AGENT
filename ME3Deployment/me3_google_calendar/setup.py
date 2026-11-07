"""Explicit owner-operated Google sign-in; never starts voice or reads events."""
import argparse,json,queue,secrets,threading,time,tkinter as tk,webbrowser
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
from tkinter import filedialog,messagebox
from urllib.parse import urlsplit
from .auth import ACCOUNT,AuthError,Credentials

def main():
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);args=p.parse_args()
 credentials=Credentials(args.root)
 window=tk.Tk();window.title('ME3 Google Calendar Setup');window.geometry('660x440')
 window.minsize(560,400);events=queue.Queue();busy=False;cancel=threading.Event()
 message=tk.StringVar(value='Select your Google Desktop OAuth client JSON to begin.')
 tk.Label(window,text='Connect Google Calendar to ME3',font=('TkDefaultFont',16,'bold')).pack(pady=(18,10))
 tk.Label(window,text='Account: '+ACCOUNT+'\nCalendar: primary only',wraplength=600).pack(pady=5)
 tk.Label(window,text='Your local calendar stays available. This setup authorizes access; it does not read events or change your calendar. Event changes in ME3 will require review and separate confirmation.',wraplength=590,justify='left').pack(padx=20,pady=8)
 tk.Label(window,text='In Google Cloud: enable Calendar API, configure Google Auth Platform for personal testing, add this account as a test user, then create a Desktop app OAuth client and download its JSON. Do not enter your Gmail password or App Password.',wraplength=590,justify='left').pack(padx=20,pady=8)
 tk.Label(window,textvariable=message,wraplength=590,justify='left').pack(padx=20,pady=10)
 row=tk.Frame(window);row.pack(pady=5)
 def set_busy(value):
  nonlocal busy
  busy=value
  for button in (select_button,login_button):button.configure(state='disabled' if value else 'normal')
 def select():
  filename=filedialog.askopenfilename(title='Select downloaded Desktop OAuth client JSON',filetypes=[('JSON files','*.json')])
  if not filename:return
  try:
   path=Path(filename)
   if not path.is_file() or path.stat().st_size>65536:raise AuthError('bounded_google_setup_file_required')
   raw=path.read_bytes()
   try:credentials.import_client(raw)
   except AuthError as error:
    if str(error)!='google_calendar_client_replacement_requires_confirmation':raise
    if not messagebox.askyesno('Replace Google client?','Replace ME3’s configured Google Calendar OAuth client? A fresh sign-in will be required.'):return
    credentials.import_client(raw,replace=True)
   message.set('Desktop client saved privately. Choose Sign in with Google.')
  except AuthError as error:message.set('Setup stopped: '+str(error).replace('_',' ')+'.')
  except Exception:message.set('Could not import that file. Choose the JSON downloaded for a Desktop app client.')
 def login():
  if busy:return
  set_busy(True);cancel.clear();message.set('Opening your browser on the Pi. Choose the specified account and review the Google consent screen.')
  def run():
   server=None
   try:
    result={};pending=None
    class Callback(BaseHTTPRequestHandler):
     def log_message(self,*args):pass
     def do_GET(self):
      parsed=urlsplit(self.path)
      expected='127.0.0.1:'+str(self.server.server_port)
      if self.headers.get('Host')!=expected or parsed.path!='/oauth2/callback' or parsed.fragment:
       self.send_error(404);return
      try:
       value=credentials.finish(pending,parsed.query);result.update(ok=True,value=value)
       raw=b'Google Calendar connected. You can close this tab and return to ME3 Setup.';status=200
      except Exception:
       result.update(ok=False);raw=b'Sign-in did not complete. Return to ME3 Setup for the status.';status=400
      self.send_response(status);self.send_header('Content-Type','text/plain; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','no-referrer');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    server=HTTPServer(('127.0.0.1',0),Callback);server.timeout=.25
    pending=credentials.begin('http://127.0.0.1:'+str(server.server_port)+'/oauth2/callback')
    if not webbrowser.open(pending.url(),new=1):raise AuthError('google_browser_open_failed')
    deadline=time.monotonic()+300
    while not result and not cancel.is_set() and time.monotonic()<deadline:server.handle_request()
    if result.get('ok'):events.put(('done','Google sign-in saved privately for '+ACCOUNT+'. Calendar API qualification is pending; no events were read or changed.'))
    elif result:events.put(('done','Sign-in stopped. Check the account, consent and Calendar API setup; no automatic retry ran.'))
    elif cancel.is_set():events.put(('done','Sign-in canceled.'))
    else:events.put(('done','Sign-in timed out. Start a new sign-in when ready.'))
   except AuthError as error:events.put(('done','Setup stopped: '+str(error).replace('_',' ')+'.'))
   except Exception:events.put(('done','Setup stopped before sign-in completed. No automatic retry ran.'))
   finally:
    if server is not None:server.server_close()
  threading.Thread(target=run,daemon=True).start()
 select_button=tk.Button(row,text='1. Select Desktop client JSON',command=select);select_button.pack(side='left',padx=8)
 login_button=tk.Button(row,text='2. Sign in with Google',command=login);login_button.pack(side='left',padx=8)
 tk.Button(window,text='Open Google Cloud setup',command=lambda:webbrowser.open('https://console.cloud.google.com/apis/library/calendar-json.googleapis.com',new=1)).pack(pady=8)
 def drain():
  try:
   while True:
    _,text=events.get_nowait();message.set(text);set_busy(False)
  except queue.Empty:pass
  window.after(100,drain)
 def close():cancel.set();window.destroy()
 window.protocol('WM_DELETE_WINDOW',close);drain();window.mainloop()
if __name__=='__main__':main()
