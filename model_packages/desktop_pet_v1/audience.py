"""Per-content audience choices within a frozen delivery permission envelope."""
from dataclasses import replace
from framework.model_api import model_data_fingerprint as fp,thaw_model_data

VERSION='desktop-pet-content-audience-v1'

def envelope(*,owner,allowed_speech,physical_observers):
 for values in (allowed_speech,physical_observers):
  if not isinstance(values,(list,tuple)) or len(values)>16 or len(set(values))!=len(values):raise ValueError('invalid audience set')
  if any(not isinstance(x,str) or not x or x==owner for x in values):raise ValueError('invalid audience participant')
 if not isinstance(owner,str) or not owner:raise ValueError('missing audience owner')
 if not set(allowed_speech)<=set(physical_observers):raise ValueError('speech audience outside scene')
 row={'version':VERSION,'owner':owner,'allowed_speech':list(allowed_speech),'physical_observers':list(physical_observers)}
 return {**row,'fingerprint':fp(row)}

def validate(bound):
 b=thaw_model_data(bound)
 if b!=envelope(owner=b['owner'],allowed_speech=b['allowed_speech'],physical_observers=b['physical_observers']):raise ValueError('audience envelope changed')
 return b

def select(choice,bound):
 b=validate(bound)
 choice=thaw_model_data(choice)
 if not isinstance(choice,dict) or set(choice)!={'speech'}:raise ValueError('audience choice fields differ')
 refs=choice['speech']
 if not isinstance(refs,list) or len(set(refs))!=len(refs) or any(not isinstance(x,str) or x not in b['allowed_speech'] for x in refs):raise ValueError('speech recipients exceed sealed permission')
 return list(refs)

def apply(contents,choice,bound):
 b=validate(bound);speech=select(choice,b)
 has_speech=any(c.kind=='speech' for c in contents)
 if has_speech!=bool(speech):raise ValueError('speech audience differs from actual speech presence')
 result=[]
 for c in contents:
  if c.kind=='thought':
   if not c.private:raise ValueError('thought must remain private')
   recipients=(b['owner'],)
  elif c.kind=='speech':recipients=tuple(speech)
  elif c.kind in {'action','expression'}:recipients=tuple(b['physical_observers'])
  else:raise ValueError('unsupported outward content kind')
  result.append(replace(c,recipient_refs=recipients))
 return tuple(result)
