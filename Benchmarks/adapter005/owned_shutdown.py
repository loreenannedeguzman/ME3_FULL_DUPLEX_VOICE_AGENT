"""Deadline observation of explicitly owned tasks; cancellation is not proof.

Never waits for a cancellation-resistant coroutine after the deadline. Pending
tasks remain identifiable to the owner, which must supervise its own process.
"""
import asyncio
def completed_ok(task):
 if not task.done() or task.cancelled():return False
 try:task.result();return True
 except BaseException:return False
async def observe(tasks,deadline):
 tasks=set(tasks)
 if not tasks:return set(),set()
 return await asyncio.wait(tasks,timeout=max(0,deadline-asyncio.get_running_loop().time()))
def cancel_pending(tasks):
 for task in tasks:
  if not task.done():task.cancel()
class OwnedJobs:
 def __init__(self):self.tasks=set()
 def start(self,coro):
  task=asyncio.create_task(coro);self.tasks.add(task)
  def retrieve(done):
   if not done.cancelled():done.exception() # observe failures, never silently adopt them
  task.add_done_callback(retrieve);return task
 def pending(self):return {t for t in self.tasks if not t.done()}
