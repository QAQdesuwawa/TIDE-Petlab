"""One codec wrapper for literal prior-event continuity, never prior numbers."""
class ContinuitySemanticCodec:
    def __init__(self,base):
        self.base=base
        self.request_version=base.request_version+':continuity-v1'
    def __getattr__(self,name):return getattr(self.base,name)
    def build_request(self,*,event,recall_hint):
        system,user=self.base.build_request(event=event,recall_hint=recall_hint)
        if recall_hint:
            user=user.replace('【已召回上下文】','【前次同类已发生事件：连续性参照】',1)
            system+='\n前次同类已发生事件只作连续性参照，当前已发生事件是本次量测对象。披露增量对应本次新交出的信息，重复表达按其此次实际作用量测；继续表达需求与新披露的信息分别沿各自含义理解。'
        return system,user
