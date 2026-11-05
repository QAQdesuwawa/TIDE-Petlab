"""Serial contact I/O with coalesced samples; no Tk or model work here."""
from collections import deque
import threading
from .contact_capture import ContactCapture

class ContactWorker:
 def __init__(self,*args):
  self.capture=ContactCapture(*args)
  self.queue=deque();self.condition=threading.Condition();self.closing=False;self.error=None
  self.thread=threading.Thread(target=self._run,name='pet-contact-archive',daemon=False);self.thread.start()
 def _put(self,kind,args):
  with self.condition:
   if self.error is not None or self.closing:return
   if kind=='sample' and self.queue and self.queue[-1][0]=='sample' and self.queue[-1][1][:2]==args[:2]:
    old=self.queue[-1];self.queue[-1]=(kind,old[1],args)
   elif len(self.queue)>=256:
    self.error=RuntimeError('接触记录积压，已停止接触采集；已封存记录保留。')
   else:self.queue.append((kind,args,None))
   self.condition.notify()
 def sample(self,*args):self._put('sample',args)
 def cut(self,at,*,ended=False):self._put('cut',(at,ended))
 def close(self,at):
  with self.condition:
   if self.closing:return
   self.queue.append(('cut',(at,True),None));self.closing=True;self.condition.notify()
 def join(self):self.thread.join()
 def _run(self):
  while True:
   with self.condition:
    self.condition.wait_for(lambda:self.queue or self.closing or self.error is not None)
    if self.error is not None:return
    if not self.queue:return
    kind,args,last=self.queue.popleft()
   try:
    if kind=='sample':
     self.capture.sample(*args)
     if last is not None:self.capture.sample(*last)
    else:self.capture.cut(args[0],ended=args[1])
   except Exception as error:
    with self.condition:self.error=error;self.condition.notify_all()
    return
