"""Source-owned speech address; receiving a public utterance is not its target."""
import json


class SpeechAddressCodec:
    def __init__(self,base,context):
        self.base=base;self.context=context
        self.visible_target_refs=tuple(context['target_refs'])
        self.speech_address_refs=self.visible_target_refs
        self.request_version=base.request_version+':speech-address-v1'

    def __getattr__(self,name):return getattr(self.base,name)

    def build_request(self,*,event,recall_hint):
        system,user=self.base.build_request(event=event,recall_hint=recall_hint)
        context={'实际说话者':event.actor_ref,'原话对象':list(self.visible_target_refs),
                 '可见受众':self.context['speech_audience'],'本次观察者':event.owner_ref,
                 '接收方式':self.context['delivery_scope']}
        heading=next((h for h in ('【本次量测对象】\n','【当前已发生事件】\n') if h in user),None)
        if heading is None:raise ValueError('speech address requires structured speech facts')
        prefix,raw=user.rsplit(heading,1)
        facts,end=json.JSONDecoder().raw_decode(raw)
        facts['参与者归属']=context
        facts['实际说话或行动者']=event.actor_ref
        for row in facts.get('内容',()):
            row['对象']=list(self.visible_target_refs)
            row['可见受众']=self.context['speech_audience']
        return system,prefix+heading+json.dumps(facts,ensure_ascii=False)+raw[end:]
