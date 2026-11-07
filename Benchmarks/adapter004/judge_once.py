"""One-attempt transport override; course prompts/scoring stay byte unchanged.

Disclosed deviation from pinned course judge.py's three-attempt HTTP retry loop.
No ping, fallback, sleep, automatic retry or request occurs at import/installation.
"""
import json,urllib.request
def install(judge_class,*,maximum_calls):
 if type(maximum_calls) is not int or not 1<=maximum_calls<=150:raise ValueError('finite_judge_cap_required')
 original=judge_class._post
 if getattr(original,'_me3_one_attempt',False):raise ValueError('one_judge_override_only')
 state={'attempts':0,'maximum':maximum_calls}
 def once(self,url,headers,body):
  if state['attempts']>=maximum_calls:raise ValueError('judge_allowance_exhausted_no_retry')
  state['attempts']+=1 # Charge even transport/parser failure, no refund.
  data=json.dumps(body).encode('utf-8')
  request=urllib.request.Request(url,data=data,headers={'Content-Type':'application/json',**headers})
  with urllib.request.urlopen(request,timeout=self.cfg.timeout) as response:
   return json.loads(response.read().decode('utf-8'))
 once._me3_one_attempt=True;judge_class._post=once
 return state
