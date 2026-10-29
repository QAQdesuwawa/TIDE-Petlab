"""Stable participant provenance over the original numeric semantic contract."""
import json
class ParticipantSemanticCodec:
 def __init__(self,base,*,owner_ref,human_ref):
  self.base=base;self.owner_ref=owner_ref;self.human_ref=human_ref
  self.request_version=base.request_version+':participant-source-v1'
 def __getattr__(self,name):return getattr(self.base,name)
 def build_request(self,*,event,recall_hint):
  system,user=self.base.build_request(event=event,recall_hint=recall_hint)
  bindings={'当前角色':self.owner_ref,'人类参与者':self.human_ref,'实际说话或行动者':event.actor_ref,'实际对象':list(getattr(self.base,'visible_target_refs',event.target_refs))}
  system+='\n【参与者归属合同】\n'+json.dumps(bindings,ensure_ascii=False,separators=(',',':'))
  system+='\n事件方向描述接收和自身输出的位置，实际参与者类型与身份沿本栏及本次事实。原数值合同中current_npc是当前角色位置，user是本次关系对端位置；它们的实际参与者ID分别由本事件绑定，不由这种别名推断人类身份。NPC来信属于peer_input，其对端披露属于该NPC来源，真人偏好证据资格仍由来源合同独立决定。'
  heading='【当前已发生事件】\n'
  if heading in user:
   prefix,raw=user.rsplit(heading,1)
   facts,end=json.JSONDecoder().raw_decode(raw);suffix=raw[end:]
   facts['实际说话或行动者']=event.actor_ref
   facts['参与者身份']=bindings
   for row,c in zip(facts.get('内容',()),event.contents):row['对象']=list(c.recipient_refs)
   user=prefix+heading+json.dumps(facts,ensure_ascii=False)+suffix
  return system,user


_FRAME_V1='reference_frame.delivery_scope 取 eligible（当前角色与用户）、third_party（用户与第三方）或 unresolved（对象未确定）。'
_FRAME_V2='reference_frame.delivery_scope 取 eligible（当前角色与本次关系对端）、third_party（叙述涉及这对参与者以外的人）或 unresolved（对象未确定）。user表示本次关系对端，current_npc表示当前角色，third_party表示这对参与者以外的人。eligible在此记录关系归属，行动资格仍由下游计算。'


class BoundParticipantSemanticCodec(ParticipantSemanticCodec):
 """V2 uses the same resolved pair as numerical settlement, not a human alias."""
 def __init__(self,base,*,owner_ref,human_ref,counterparty_ref):
  super().__init__(base,owner_ref=owner_ref,human_ref=human_ref)
  if not isinstance(counterparty_ref,str) or not counterparty_ref or counterparty_ref==owner_ref:
   raise ValueError('participant relation counterparty invalid')
  self.counterparty_ref=counterparty_ref
  self.request_version=base.request_version+':participant-pair-source-v2'
 def build_request(self,*,event,recall_hint):
  if event.owner_ref!=self.owner_ref:raise ValueError('participant owner differs from event')
  from .web_observation import is_observation
  if event.event_kind=='peer_input' and not is_observation(event) and event.actor_ref!=self.counterparty_ref:
   raise ValueError('participant counterpart differs from actual received source')
  system,user=self.base.build_request(event=event,recall_hint=recall_hint)
  if system.count(_FRAME_V1)!=1:raise ValueError('participant reference contract differs')
  system=system.replace(_FRAME_V1,_FRAME_V2,1)
  bindings={'当前角色':self.owner_ref,'关系对端':self.counterparty_ref,'人类参与者':self.human_ref,
   '实际说话或行动者':event.actor_ref,'实际对象':list(getattr(self.base,'visible_target_refs',event.target_refs)),
   '归属标签':{'current_npc':self.owner_ref,'user':self.counterparty_ref,'third_party':'当前这对参与者以外的人','unresolved':'对象未确定'}}
  system+='\n【参与者归属合同】\n'+json.dumps(bindings,ensure_ascii=False,separators=(',',':'))
  system+='\n量测对象是当前角色对本次关系对端的新增作用；实际行动者、原表达对象与可见受众各自沿事实保留。归属标签按上述参与者ID解析，人类身份仅由人类参与者一栏给出。数值、词元和关系叙述共同使用这一个关系对象。'
  heading='【当前已发生事件】\n'
  if heading in user:
   prefix,raw=user.rsplit(heading,1);facts,end=json.JSONDecoder().raw_decode(raw);suffix=raw[end:]
   facts['实际说话或行动者']=event.actor_ref;facts['参与者身份']=bindings
   for row,c in zip(facts.get('内容',()),event.contents):row['对象']=list(c.recipient_refs)
   user=prefix+heading+json.dumps(facts,ensure_ascii=False)+suffix
  return system,user
