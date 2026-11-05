"""Package meaning of an accepted internal intention, not a performed action."""
from .intention_facts import validate


def formation_text(facts):
    validate(facts)
    if not facts:
        raise ValueError('intention requires accepted formation')
    return '形成了回应的意图，具体话语和动作尚未编排。'


class IntentionSemanticCodec:
    """Keep the original numerical contract; clarify the measured occurrence."""
    def __init__(self, base):
        self.base = base
        self.request_version = base.request_version + ':cx67-intention-formation-v1'

    def __getattr__(self, name):
        return getattr(self.base, name)

    def build_request(self, *, event, recall_hint):
        system, user = self.base.build_request(event=event, recall_hint=recall_hint)
        system += ('\n【六七包私有意图形成】\n'
                   '本次已经发生的是角色内部形成回应意图。具体计划、说话、动作和对外披露各自以其后续实际证据为准。'
                   '量测对象是这次内部形成相对于此前已结算状态的新增作用；触发它的外界事件已经结算。'
                   '意图形成的来源证据只支持这层事实，具体内心台词尚未生成。')
        return system, user
