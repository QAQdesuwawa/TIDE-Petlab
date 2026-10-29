"""One external visible episode measured from the recipient's position."""
import json
from .semantic import (DesktopPetEvidenceSemanticCodec,RESOURCE_ROOT,CleanEvidenceMixin,
 EvidenceMagnitudeMixin,GroundedRecallCueMixin,AtomicGroundedRecallCueMixin)


class VisibleSemanticBase(DesktopPetEvidenceSemanticCodec):
 def __init__(self,*,target_refs):self.visible_target_refs=tuple(target_refs)
 def build_request(self,*,event,recall_hint):
  if event.event_kind!='peer_input':raise ValueError('visible peer measurement requires external input')
  system=RESOURCE_ROOT.joinpath('evidence_projection.md').read_text(encoding='utf8')
  system+='\n【观察到对方的可见表现】\n以下内容是同一来源行动中，本角色实际收到的话语、看到的表情和动作。以本角色作为接收者，整体量测这一次经历的即时影响。对方的实际表现与本角色自身感受分开理解；动作完成或中断及其原因按原事实理解。表达对象与能看见的人分别列出。'
  facts={'当前角色':event.owner_ref,'实际行动者':event.actor_ref,'对方表达对象':list(self.visible_target_refs),
   '内容':[{'类型':c.kind,'实际内容':c.description,'可见受众':list(c.recipient_refs)} for c in event.contents]}
  return system,'【观察到对方的实际内容】\n'+json.dumps(facts,ensure_ascii=False)


class ConstrainedVisibleSemanticCodec(EvidenceMagnitudeMixin,VisibleSemanticBase):
 request_version='desktop-pet-peer-visible-evidence-v1'
 located_reference_v2=True
 parse_event_response=CleanEvidenceMixin.parse_event_response
 local_calibration=True
 calibration_version='desktop-pet-joint-axis-constraint-v4'


class AtomicGroundedVisibleSemanticCodec(AtomicGroundedRecallCueMixin,GroundedRecallCueMixin,ConstrainedVisibleSemanticCodec):
 request_version='desktop-pet-grounded-peer-visible-v1'

class RecipientEffectVisibleSemanticCodec(ConstrainedVisibleSemanticCodec):
 """Bind numeric meaning to the receiver/source pair, preserving v1 replay."""
 request_version='desktop-pet-peer-visible-recipient-effect-v2'
 def build_request(self,*,event,recall_hint):
  system,user=super().build_request(event=event,recall_hint=recall_hint)
  system+='\n'+RESOURCE_ROOT.joinpath('recipient_effect_v2.md').read_text(encoding='utf8')
  relation={'受影响角色':event.owner_ref,'关系对端':event.actor_ref,
   '对方原表达对象':list(self.visible_target_refs),
   '双方关系证据':'本次可见内容中实际作用于上述双方的部分'}
  user+='\n【本次受影响关系】\n'+json.dumps(relation,ensure_ascii=False,separators=(',',':'))
  return system,user


class AtomicGroundedRecipientEffectVisibleSemanticCodec(AtomicGroundedRecallCueMixin,GroundedRecallCueMixin,RecipientEffectVisibleSemanticCodec):
 request_version='desktop-pet-grounded-peer-visible-recipient-effect-v2'

class ScopedRecipientVisibleSemanticCodec(ConstrainedVisibleSemanticCodec):
 """Bind numeric meaning to the receiver/source pair, preserving v1 replay."""
 request_version='desktop-pet-peer-visible-recipient-effect-v3'
 def build_request(self,*,event,recall_hint):
  system,user=super().build_request(event=event,recall_hint=recall_hint)
  old=RESOURCE_ROOT.joinpath('evidence_magnitude.md').read_text(encoding='utf8')
  if system.count(old)!=1:raise ValueError('visible semantic base prompt differs')
  system=system.replace(old,RESOURCE_ROOT.joinpath('recipient_effect_v3.md').read_text(encoding='utf8'),1)
  relation={'受影响角色':event.owner_ref,'关系对端':event.actor_ref,
   '对方原表达对象':list(self.visible_target_refs),
   '双方关系证据':'本次可见内容中实际作用于上述双方的部分'}
  user='【本次受影响关系】\n'+json.dumps(relation,ensure_ascii=False,separators=(',',':'))+'\n'+user
  return system,user


class AtomicGroundedScopedRecipientVisibleSemanticCodec(AtomicGroundedRecallCueMixin,GroundedRecallCueMixin,ScopedRecipientVisibleSemanticCodec):
 request_version='desktop-pet-grounded-peer-visible-recipient-effect-v3'
