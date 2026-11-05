"""Writer audience field, bound to the same frozen permission used by memory delivery."""
import json,hashlib
from framework.model_api import model_data_fingerprint as fp,thaw_model_data
from model_packages.desktop_pet_v1 import audience

VERSION='petlab-audience-delivery-v1'

def instructions(bound,*,automatic_approach=True,owner_cognition=False,thought_enabled=True):
 b=audience.validate(bound)
 return (('\n当前输入与回忆是你自己获知的经历，原文附带的受众说明谁曾知道它；本轮是你新的表达与行动，可结合人设、关系和这些经历选择向谁说什么。现场对象是可以交谈或走近的人；视线、走近对象与话语受众分别选择。' if owner_cognition else '')+'\n本轮子项可见范围：'+json.dumps({'speech可选接收者':b['allowed_speech'],
  '动作与表情的现场观察者':b['physical_observers'],**({'内心思考的拥有者':b['owner']} if thought_enabled else {})},ensure_ascii=False,separators=(',',':'))+
  '\n输出增加 audience 对象，其中 speech 是本句话希望交付的参与者ID数组，可选其中一人、多人或全部。speech 为 null 时该数组为空。动作与表情按实际现场可见性投递'+('，thought仅自己可见。' if thought_enabled else '。')+('单人私语由本地落实走近；多人接收范围仅决定交付权限。移动由独立动作绑定一个明确对象，多段移动按所选行动计划执行。' if automatic_approach=='single-target' else '限定话语的靠近和实际交付由本地执行。' if automatic_approach else 'audience指定话语的接收范围；身体移动按本轮独立行动计划执行。'))

def reserve(bound,*,automatic_approach=True,owner_cognition=False,thought_enabled=True):return len(instructions(bound,automatic_approach=automatic_approach,owner_cognition=owner_cognition,thought_enabled=thought_enabled))

def augment(delivery,bound,*,budget_chars,automatic_approach=True,owner_cognition=False,thought_enabled=True):
 b=audience.validate(bound);d=thaw_model_data(delivery)
 if 'audience_delivery' in d:raise ValueError('audience delivery duplicated')
 d.pop('fingerprint',None)
 d['system']+=instructions(b,automatic_approach=automatic_approach,owner_cognition=owner_cognition,thought_enabled=thought_enabled)
 if len(d['system'])+len(d['user'])>budget_chars:raise ValueError('audience delivery exceeds reserved budget')
 d['budget_chars']=budget_chars
 d['prompt_fingerprint']=hashlib.sha256((d['system']+'\0'+d['user']).encode()).hexdigest()
 d['audience_delivery']={'version':VERSION,'envelope':b}
 return {**d,'fingerprint':fp(d)}

def split_output(text,bound):
 value=json.loads(text)
 if not isinstance(value,dict) or 'audience' not in value:raise ValueError('missing audience choice')
 choice=value.pop('audience')
 # The only selectable audience lane in this version is speech. Preserve the
 # wire response in the provider receipt and normalize its unambiguous list
 # representation before the same identity/permission/presence checks.
 if isinstance(choice,list):choice={'speech':choice}
 refs=audience.select(choice,bound)
 if (value.get('speech') is not None)!=bool(refs):raise ValueError('audience differs from speech presence')
 return json.dumps(value,ensure_ascii=False),{'speech':refs}

def verify_contents(contents,choice,bound):
 """Final check must run after local sequence/whisper compilation too."""
 items=tuple(contents)
 if audience.apply(items,choice,bound)!=items:raise ValueError('candidate audience differs from sealed choice')
 return items

def canonical_payload(request,payload):
 """Validate new scopes, then restore old scopes solely for structural verification."""
 from dataclasses import replace
 from model_packages.desktop_pet_v1.events import Content
 if request.control.get('audience_contract')!=VERSION:raise ValueError('unknown audience contract')
 b=audience.validate(request.readonly_facts['audience_envelope'])
 if b['owner']!=request.subject_ref:raise ValueError('audience owner differs')
 declared=request.readonly_facts.get('participant_destination',{})
 from petlab_app.writer_visibility import speech_audience
 if b['allowed_speech']!=speech_audience(declared,request.readonly_facts.get('memory_visibility')):raise ValueError('audience permission differs from memory delivery')
 raw=thaw_model_data(payload)
 if request.control.get('audience_motion_contract'):
  from model_packages.desktop_pet_v1.audience_motion import unwrap_verified
  raw=unwrap_verified(request,raw)
 choice=raw.pop('audience',None)
 items=tuple(Content.from_record(c) for c in raw['contents'])
 verify_contents(items,choice,b)
 canonical=[]
 for c in items:
  refs=(b['owner'],) if c.kind=='thought' else tuple(declared['audience'])
  if c.content_id=='whisper:approach':refs=tuple(request.readonly_facts['whisper_context']['public_audience'])
  canonical.append(replace(c,recipient_refs=refs).to_record())
 raw['contents']=canonical
 return raw
