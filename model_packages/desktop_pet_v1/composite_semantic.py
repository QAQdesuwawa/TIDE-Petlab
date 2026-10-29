"""Versioned measurement of one actual composite episode, not visual state."""
import json
from .semantic import DesktopPetEvidenceSemanticCodec,RESOURCE_ROOT


class CompositeSemanticCodec(DesktopPetEvidenceSemanticCodec):
    request_version='desktop-pet-composite-evidence-v11'

    def build_request(self,*,event,recall_hint):
        if event.event_kind!='npc_output':raise ValueError('composite measurement requires actual own output')
        system=RESOURCE_ROOT.joinpath('evidence_projection.md').read_text(encoding='utf-8')
        system+='\n同一次行动的实际结果：以下话语、动作和可见表情共同构成一次经历，整体量测一次即时增量。已有心理水平由下游结算保存。表情描述是实际可见表达；素材编码仅用于渲染。自娱按自身活动理解，公开可见与面向用户沟通分别按内容事实判断。完成表示执行结束，打断表示部分发生后停止。停止原因及对象按实际描述理解，技术故障归执行环境，用户行为归相应交互来源。'
        rows=[{'类型':c.kind,'实际内容':c.description,'对象':list(c.recipient_refs)} for c in event.contents]
        user='【事件方向】\n当前角色自身已发生的事件\n【同一次行动的实际内容】\n'+json.dumps(rows,ensure_ascii=False)
        return system,user


from .semantic import CleanEvidenceMixin

class CleanCompositeSemanticCodec(CleanEvidenceMixin,CompositeSemanticCodec):
    request_version='desktop-pet-clean-composite-evidence-v14'


from .semantic import EvidenceMagnitudeMixin

class MagnitudeCompositeSemanticCodec(EvidenceMagnitudeMixin,CleanCompositeSemanticCodec):
    request_version='desktop-pet-composite-magnitude-v17'


class CalibratedCompositeSemanticCodec(MagnitudeCompositeSemanticCodec):
    request_version='desktop-pet-local-calibrated-composite-v20'
    local_calibration=True


class ConstrainedCompositeSemanticCodec(CalibratedCompositeSemanticCodec):
    request_version='desktop-pet-joint-constrained-composite-v23'
    calibration_version='desktop-pet-joint-axis-constraint-v4'


from .semantic import GroundedRecallCueMixin
class GroundedCompositeSemanticCodec(GroundedRecallCueMixin,ConstrainedCompositeSemanticCodec):
    request_version='desktop-pet-grounded-recall-composite-v26'


from .semantic import AtomicGroundedRecallCueMixin
class AtomicGroundedCompositeSemanticCodec(AtomicGroundedRecallCueMixin,GroundedCompositeSemanticCodec):
    request_version='desktop-pet-atomic-cue-composite-v29'
