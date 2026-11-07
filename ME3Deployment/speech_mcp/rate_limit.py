"""Single-agent TTS pacing; persists timestamps only across diagnostic/live starts."""
import asyncio,json,math,os,time
from pathlib import Path
class TtsRateLimiter:
    def __init__(self,path,emit,clock=time.time,sleep=asyncio.sleep):
        self.path=Path(path);self.emit=emit;self.clock=clock;self.sleep=sleep;self.lock=asyncio.Lock()
    async def acquire(self):
        async with self.lock:
            while True:
                now=self.clock();recent=[]
                if self.path.exists():
                    if self.path.stat().st_size>4096:raise ValueError('invalid_rate_state')
                    rows=json.loads(self.path.read_text())
                    if not isinstance(rows,list) or len(rows)>10 or any(type(x) not in (float,int) or not math.isfinite(x) or x>now+61 for x in rows):raise ValueError('invalid_rate_state_or_clock')
                    recent=sorted(x for x in rows if now-x<61)
                if len(recent)<3:
                    self.path.parent.mkdir(parents=True,exist_ok=True)
                    temporary=self.path.with_suffix('.'+str(os.getpid())+'.tmp')
                    temporary.write_text(json.dumps(recent+[now]),encoding='utf-8')
                    os.replace(temporary,self.path)
                    return
                delay=max(.01,recent[0]+61-now)
                self.emit('tts_rate_wait',wait_seconds=round(delay,3),limit_requests=3,window_seconds=61)
                await self.sleep(delay)
