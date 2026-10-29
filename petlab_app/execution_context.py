"""Optional executor metadata through one channel; registration has no effect."""
from contextlib import contextmanager
from framework.model_api import thaw_model_data

VERSION='petlab-execution-target-v1'


def validate(value):
 if (set(value)!={'version','target_ref','session_binding','authorization_ref'} or value['version']!=VERSION
     or any(not isinstance(value[k],str) or not value[k] for k in ('target_ref','session_binding','authorization_ref'))):
  raise ValueError('execution target context differs')
 return value


class ExecutionContexts:
 def set_execution_context(self,action_id,value,*,observer=None):
  value=thaw_model_data(validate(value))
  if not hasattr(self,'_execution_contexts'):self._execution_contexts={}
  old=self._execution_contexts.get(action_id)
  if old is not None and old!=value:raise ValueError('execution target changed')
  self._execution_contexts[action_id]=value
  if not hasattr(self,"_execution_observers"):self._execution_observers={}
  if observer is not None:self._execution_observers[action_id]=observer
 def clear_execution_context(self,action_id):
  getattr(self,'_execution_contexts',{}).pop(action_id,None)
  getattr(self,'_execution_observers',{}).pop(action_id,None)
 def execution_observer(self,action_id):return getattr(self,'_execution_observers',{}).get(action_id)
 def execution_context(self,action_id):
  value=getattr(self,'_execution_contexts',{}).get(action_id)
  return None if value is None else thaw_model_data(value)


@contextmanager
def bind(executor,action_id,*,target_ref,session_binding,authorization_ref,observer=None):
 setter=getattr(executor,'set_execution_context',None)
 if setter is None:
  yield
  return
 clearer=getattr(executor,'clear_execution_context',None)
 if clearer is None:raise ValueError('executor context lacks cleanup')
 setter(action_id,{'version':VERSION,'target_ref':target_ref,'session_binding':session_binding,'authorization_ref':authorization_ref},**({'observer':observer} if observer is not None else {}))
 try:yield
 finally:clearer(action_id)
