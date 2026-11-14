"""Writer audience field, bound to the same frozen permission used by memory delivery."""
import json,hashlib
from framework.model_api import model_data_fingerprint as fp,thaw_model_data
from model_packages.desktop_pet_v1 import audience

VERSION='petlab-audience-delivery-v1'
DIRECT_VERSION='petlab-audience-delivery-v2-direct-target'

def instructions(bound,*,automatic_approach=True,owner_cognition=False,thought_enabled=True,version=VERSION,concise=False,schema_first=False):
 b=audience.validate(bound)
 if schema_first:
  return ('\n本轮子项可见范围：'+json.dumps({'speech可选接收者':b['allowed_speech']},ensure_ascii=False,separators=(',',':'))
   +'\nJSON必须有 "audience":{"speech":["听众ID"]}；把示意ID换成上方实际听众。没有台词时写 "audience":{"speech":[]}。'
   + ('"speech_to":["直接交谈的对象ID"] 从听众中选，旁听者不用列入；可填 null。' if version==DIRECT_VERSION else '')
   + ('只给一位同伴听的私语会在靠近后播放。' if automatic_approach=='single-target' else ''))
 if concise:
  return ('\n本轮子项可见范围：'+json.dumps({'speech可选接收者':b['allowed_speech']},ensure_ascii=False,separators=(',',':'))
   +'\n用 audience.speech 选实际能听见这段话的人；没有话时填空数组。'
   + ('speech_to 从听众中选直接说给谁，旁听者不用列入；可填 null。' if version==DIRECT_VERSION else '')
   + ('只给一位同伴听的私语会在靠近后播放。' if automatic_approach=='single-target' else '')
   + '动作和表情由现场可见范围决定。')
 return (('\n当前输入与回忆是你自己获知的经历，原文附带的受众说明谁曾知道它；本轮是你新的表达与行动，可结合人设、关系和这些经历选择向谁说什么。现场对象是可以交谈或走近的人；视线、走近对象与话语受众分别选择。' if owner_cognition else '')+'\n本轮子项可见范围：'+json.dumps({'speech可选接收者':b['allowed_speech'],
  '动作与表情的现场观察者':b['physical_observers'],**({'内心思考的拥有者':b['owner']} if thought_enabled else {})},ensure_ascii=False,separators=(',',':'))+
  '\n输出增加 audience 对象，其中 speech 是本句话希望交付的参与者ID数组，可选其中一人、多人或全部。speech 为 null 时该数组为空。动作与表情按实际现场可见性投递'+('，thought仅自己可见。' if thought_enabled else '。')+('单人私语由本地落实走近；多人接收范围仅决定交付权限。移动由独立动作绑定一个明确对象，多段移动按所选行动计划执行。' if automatic_approach=='single-target' else '限定话语的靠近和实际交付由本地执行。' if automatic_approach else 'audience指定话语的接收范围；身体移动按本轮独立行动计划执行。')
  + ('\nspeech_to 是这句话直接说给谁的参与者ID数组，从 audience.speech 里选；其余听众可以旁听。没有台词时填 null。' if version==DIRECT_VERSION else ''))

def reserve(bound,**options):return len(instructions(bound,**options))

def augment(delivery,bound,*,budget_chars,version=VERSION,**options):
 b=audience.validate(bound);d=thaw_model_data(delivery)
 if 'audience_delivery' in d:raise ValueError('audience delivery duplicated')
 d.pop('fingerprint',None)
 instruction=instructions(b,version=version,**options)
 heading='【输出格式】\n'
 if options.get('schema_first') and heading in d['system']:
  before,sep,after=d['system'].rpartition(heading)
  d['system']=before+sep+instruction.lstrip('\n')+'\n'+after
 else:d['system']+=instruction
 if len(d['system'])+len(d['user'])>budget_chars:raise ValueError('audience delivery exceeds reserved budget')
 d['budget_chars']=budget_chars
 d['prompt_fingerprint']=hashlib.sha256((d['system']+'\0'+d['user']).encode()).hexdigest()
 d['audience_delivery']={'version':version,'envelope':b}
 return {**d,'fingerprint':fp(d)}

def split_output(text,bound,*,version=VERSION,allow_single_target=False):
 value=json.loads(text)
 if not isinstance(value,dict):raise ValueError('missing audience choice')
 if 'audience' not in value and (version!=DIRECT_VERSION or value.get('speech',False) is not None
       or value.get('speech_segments')):raise ValueError('missing audience choice')
 choice=value.pop('audience',{'speech':[]})
 # The only selectable audience lane in this version is speech. Preserve the
 # wire response in the provider receipt and normalize its unambiguous list
 # representation before the same identity/permission/presence checks.
 if isinstance(choice,list):choice={'speech':choice}
 refs=audience.select(choice,bound)
 if (value.get('speech') is not None)!=bool(refs):raise ValueError('audience differs from speech presence')
 selected={'speech':refs}
 if version==DIRECT_VERSION:
  direct=value.pop('speech_to',None)
  if direct is not None:
   if allow_single_target and isinstance(direct,str):direct=[direct]
   selected['speech_to']=direct_targets(direct,refs)
 return json.dumps(value,ensure_ascii=False),selected

def direct_targets(direct,recipients):
 if (not isinstance(direct,(list,tuple)) or not direct or
     any(not isinstance(ref,str) or ref not in recipients for ref in direct)
     or len(set(direct))!=len(direct)):
  raise ValueError('direct speech targets exceed delivered speech audience')
 return list(direct)

def visible_targets(default,direct,items):
 speeches=[row for row in items if row['kind']=='speech']
 if direct is None or not speeches:return list(default)
 return direct_targets(direct,set.intersection(*(set(row['recipient_refs']) for row in speeches)))

def verify_contents(contents,choice,bound):
 """Final check must run after local sequence/whisper compilation too."""
 items=tuple(contents)
 if audience.apply(items,choice,bound)!=items:raise ValueError('candidate audience differs from sealed choice')
 return items

def canonical_payload(request,payload):
 """Validate new scopes, then restore old scopes solely for structural verification."""
 from dataclasses import replace
 from model_packages.desktop_pet_v1.events import Content
 if request.control.get('audience_contract') not in {VERSION,DIRECT_VERSION}:raise ValueError('unknown audience contract')
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
