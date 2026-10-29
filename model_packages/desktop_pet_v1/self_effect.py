"""Current own-event effect with a verified causal parent, no state prompt."""
class SelfEffectCodec:
 def __init__(self,base,parent_text='',*,private_thought=False):
  self.base=base;self.parent_text=parent_text;self.private_thought=private_thought
  self.request_version=base.request_version+(':self-effect-v3-private-thought' if private_thought else ':self-effect-v2')
 def __getattr__(self,name):return getattr(self.base,name)
 def build_request(self,*,event,recall_hint):
  system,user=self.base.build_request(event=event,recall_hint=recall_hint)
  system+='\n【自身事件的增量口径】\n表达当前水平与本次造成的变化分开理解。已结算的触发事件描述角色在回应前已经经历的事实；当前实际说出或完成的内容才是本次量测对象。当前话语可能是在呈现已存在的害羞、需求、防御或压力；本次增量量测说出这句话带来的新增披露、进一步激活、缓解、设界或关系变化。已有需求的叙述沿原水平理解，有新增作用时按其实际方向和幅度量测。量测的是说出之后相对于说出之前的作用，而非从一句台词重新估计一份完整心理状态。回应中的语气姿态与实际关系行为分别理解；结合触发原话辨认实际的接纳、拒绝、澄清、索取或设界。掩饰、撒娇、害羞等表达风格以实际关系含义解释。L_self记录此次新增交出的信息；P_need记录此次让需求进一步未满足或得到满足的变化；V_rep记录此次新增或缓和的真实关系摩擦；E_env记录此次新增或缓和的互动压力。实际动作和表情是已执行的表现，心理水平沿上述增量含义理解；证据仍定位于本次事件正文。'
  if self.private_thought:
   from .semantic import RESOURCE_ROOT
   definition=RESOURCE_ROOT.joinpath('thought.md').read_text(encoding='utf-8')
   if definition not in system:system+='\n'+definition
  if self.parent_text:user+='\n【已结算的触发事件：来源参照】\n'+self.parent_text
  return system,user
