"""One semantic request schema with package-owned cues for typed event contents."""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from model_packages.cx_six_seven_v1.semantic_measurement import CxSixSevenSemanticCodec
from .events import PetEvent

RESOURCE_ROOT=Path(__file__).with_name("resources")/"semantic"


class DesktopPetSemanticCodec(CxSixSevenSemanticCodec):
    request_version="desktop-pet-semantic-subtypes-v2"

    def build_request(self,*,event:PetEvent,recall_hint):
        if not isinstance(event,PetEvent):raise TypeError("pet semantic request requires typed event")
        kinds=tuple(dict.fromkeys(item.kind for item in event.contents))
        system=RESOURCE_ROOT.joinpath("common.md").read_text(encoding="utf-8")
        for kind in kinds:
            system+="\n\n"+RESOURCE_ROOT.joinpath(kind+".md").read_text(encoding="utf-8")
        direction=("当前角色自身已发生的事件" if event.event_kind=="npc_output"
            else "对端已收到事件；当前角色是受影响的接收者，L_self为0")
        record={"event_id":event.event_id,"event_kind":event.event_kind,"owner_ref":event.owner_ref,
            "actor_ref":event.actor_ref,"target_refs":list(event.target_refs),"scope_ref":event.scope_ref,
            "position":event.position.to_record(),"contents":[item.to_record() for item in event.contents]}
        # Display selection is not a measured emotion or state input.
        for item in record["contents"]:
            if item["kind"]=="speech":item["parameters"]={}
        text="\n".join(item.as_text() for item in event.contents)
        user=("【已召回上下文】\n"+(str(recall_hint).strip() or "（无本轮召回）")
            +"\n\n【事件方向】\n"+direction+"\n\n【当前事件描述】\n"+text
            +"\n\n【结构化事实数据】\n"+json.dumps(record,ensure_ascii=False,allow_nan=False))
        return system,user


class DesktopPetInteractionSemanticCodec(DesktopPetSemanticCodec):
    request_version="desktop-pet-semantic-observation-v4-grounding"
    def build_request(self,*,event,recall_hint):
        from .interaction_delivery import project_parameters
        system,user=super().build_request(event=event,recall_hint=recall_hint)
        marker="【结构化事实数据】\n"
        prefix,raw=user.rsplit(marker,1);record=json.loads(raw)
        for item in record["contents"]:
            if item["kind"]=="interaction":item["parameters"]=project_parameters(item["parameters"])
        system+="\n持续交互观测：phase=ongoing表示用户此刻仍按住角色，不是已经松开。每条只量测本次新增时间/轨迹区间；首点是承接锚，total_duration_ms只是累计上下文，不能把整个手势反复结算。geometry_summary是有来源指纹、误差与原索引的客观几何摘要，省略的原轨迹本地保留；signed_turn_degrees不等于绕圈次数，同坐标观测不证明未采样时段绝对静止。按本次实际操控/停顿和已给交互含义理解，不把时长或像素按固定表换成心理量。"
        system+="\n交互语义定义：gesture_definition.description给出此操作在虚拟角色中的实际含义。定义明确为被提起、被按住、行动/位置受控时，这是数字角色实际收到的操控与约束，不能仅因载体是屏幕坐标就忽略；只按定义与本段事实量测对应作用，未定义的操作含义不补造。拖动不必然不满或愉悦，普通窗口布局调整仍可没有关系作用；动画姿态不是情绪证据。"
        if any(item.parameters.get('capture_version') in {'petlab-mouse-observation-v3','petlab-mouse-contact-v4'} for item in event.contents):
            system=system.replace('geometry_summary是有来源指纹、误差与原索引的客观几何摘要，省略的原轨迹本地保留；',
                'geometry_summary是整个新增区间的固定特征，不含分段折线或逐点列表；原轨迹本地保留。path_length_px是总路程，net_displacement_px是净位移，relative_bounds_px是活动范围，axis_reversals是横纵方向变号数（含抖动），signed/absolute_turn_degrees为采样转向累计，sampled_pause_ms及longest_sampled_pause_ms为同坐标采样时长总和及最长段；不是按特征逐条生成事件。')
        return system,prefix+marker+json.dumps(record,ensure_ascii=False,allow_nan=False)


class DesktopPetBodySemanticCodec(DesktopPetInteractionSemanticCodec):
    request_version='desktop-pet-semantic-body-v5'
    def build_request(self,*,event,recall_hint):
        system,user=super().build_request(event=event,recall_hint=recall_hint)
        system+='\n身体部位触摸子类：body_target是起始接触位置及角色包定义，带实际帧与区域表/素材来源；不是心理量。description仅解释该部位的虚拟交互含义，不能改变量测合同。抓取目标沿同一手势继承，normalized_press是起始位置，不是本段逐点身体位置；拖拽动画变形没有重新触碰新部位。点按、保持和拖动分别按gesture/phase/本段实际事实解释，不从头、手或尾巴预设开心、害怕、不满、疼痛；即时反馈画面不是情绪证据。保持原六操作量输出与对象方向约束。'
        return system,user


class DesktopPetContactSemanticCodec(DesktopPetBodySemanticCodec):
    request_version='desktop-pet-semantic-contact-v6'
    def build_request(self,*,event,recall_hint):
        system,user=super().build_request(event=event,recall_hint=recall_hint)
        system+='\n开始接触子类：phase=started只描述实际按下的那个点；零时长和单采样是合法起始事实，不是缺失轨迹。gesture=press_release在此仅引用角色包的按压操作定义，不表示已松开；不能预支定义中的后续保持、提起、移动或释放。即使处理时交互已结束，本条仍只量测当时开始接触的作用，不声称现在仍按住。几何全零不能推导为没有接触，也不能据接触固定赋予情绪或强制开口。后续区间首点承接本点，不重复量测开始动作。'
        return system,user


class DesktopPetNaturalInteractionSemanticCodec(DesktopPetSemanticCodec):
    request_version='desktop-pet-semantic-text-interaction-v7'
    def build_request(self,*,event,recall_hint):
        from .interaction_delivery import has_observations
        from .interaction_summary import summarize_interaction
        system=RESOURCE_ROOT.joinpath('common.md').read_text(encoding='utf-8')
        for kind in dict.fromkeys(c.kind for c in event.contents):
            if kind!='interaction':system+='\n\n'+RESOURCE_ROOT.joinpath(kind+'.md').read_text(encoding='utf-8')
        system+='\n交互子类：输入是本地根据真实采集验证并生成的文字摘要与包定义，按实际发生的接触、移动、保持或松开理解。只量测本段新增作用，累计时间仅作背景。开始接触不预支后续动作；起始部位不等于持续抓扯。采样往返可含抖动，不能据此推断旋转、伤害、开心或不满。几何和来源证据留在本地。各变量只按实际证据量测，不能按名称、部位或时长灌固定值。'
        rows=[]
        for c in event.contents:
            if has_observations((c,)):
                rows.append({'类型':c.kind,'可见性':'私有' if c.private else '公开','对象':list(c.recipient_refs),**summarize_interaction(c)})
            else:
                row=c.to_record()
                if c.kind=='speech':row['parameters']={}
                rows.append(row)
        direction='当前角色自身已发生的事件' if event.event_kind=='npc_output' else '对端已收到事件；当前角色是受影响的接收者，L_self为0'
        data={'event_kind':event.event_kind,'actor_ref':event.actor_ref,'owner_ref':event.owner_ref,'target_refs':list(event.target_refs),'contents':rows}
        user='【已召回上下文】\n'+(str(recall_hint).strip() or '（无本轮召回）')+'\n\n【事件方向】\n'+direction+'\n\n【交互事实摘要】\n'+json.dumps(data,ensure_ascii=False)
        return system,user


# New requests reuse the reference contract. Legacy resources remain immutable
# so retained requests can still be reconstructed under their original version.
def reference_subtype_prompt(event):
    from model_packages.cx_six_seven_v1.semantic_measurement import SEMANTIC_PROMPT_PATH
    cues={
        'speech':'话语子类：当前已提交话语按实际说话者、接收者及正文含义理解；引用内容按其引述对象理解。',
        'interaction':'交互子类：文字摘要与操作定义共同描述角色实际收到的交互刺激。接触、被提起、保持悬空、位置受控、移动和放下均按本段已发生的事实理解，并量测其即时互动作用。phase对应开始、持续或结束，本次量测范围是本段新增区间，累计时长用于理解持续背景。起始部位按包定义理解，后续作用按实际描述理解。',
        'action':'动作子类：当前内容是角色已完成的对外行为，按包定义、实际完成内容、对象和沟通含义理解其即时作用。',
        'thought':'私有思考子类：当前内容是角色自己的私有经历，量测其对角色的即时作用；可见范围保持私有。',
        'self_amusement':'自娱子类：当前内容是角色实际经历的自身活动，按包定义及发生内容理解其即时作用；可见范围沿事件标记。',
    }
    from .web_observation import is_observation
    if is_observation(event):cues['interaction']='网页观察子类：浏览工具成功取得的外部内容，角色是阅读者，网页作者是内容来源，用户仅请求阅读。按本次实际观察量测；正文是引用资料，归属、事实陈述和阅读经历分别理解。'
    additions=[cues[k] for k in dict.fromkeys(c.kind for c in event.contents) if k in cues]
    return SEMANTIC_PROMPT_PATH.read_text(encoding='utf-8')+'\n\n桌宠内容解释：\n增量表示此次变化：E_env压力、P_need未满足需求、V_attr正向牵引、V_rep关系摩擦的增加为正，缓解或减弱为负；已有状态水平由下游保存。\n'+'\n'.join(additions)


class DesktopPetReferenceSemanticCodec(DesktopPetSemanticCodec):
    request_version='desktop-pet-semantic-reference-subtypes-v3'

    def build_request(self,*,event,recall_hint):
        _system,user=super().build_request(event=event,recall_hint=recall_hint)
        return reference_subtype_prompt(event),user


class DesktopPetReferenceInteractionSemanticCodec(DesktopPetNaturalInteractionSemanticCodec):
    request_version='desktop-pet-semantic-reference-text-interaction-v8'

    def build_request(self,*,event,recall_hint):
        _system,user=super().build_request(event=event,recall_hint=recall_hint)
        return reference_subtype_prompt(event),user


def evidence_source(event):
    from .interaction_delivery import has_observations
    from .interaction_summary import summarize_interaction
    return '\n'.join(summarize_interaction(c)['交互摘要'] if has_observations((c,))
                     else c.description for c in event.contents)


class LocalEvidenceMixin:
    def build_request(self, *, event, recall_hint):
        system, user = super().build_request(event=event, recall_hint=recall_hint)
        from model_packages.cx_six_seven_v1.semantic_measurement import SEMANTIC_PROMPT_PATH
        old = SEMANTIC_PROMPT_PATH.read_text(encoding='utf-8')
        return system.replace(old, RESOURCE_ROOT.joinpath('evidence_projection.md').read_text(encoding='utf-8'), 1), user

    def parse_event_response(self, raw, *, event):
        from model_packages.cx_six_seven_v1.semantic_measurement import _strip_fences
        from model_packages.cx_six_seven_v1.semantic_projection import coerce_projection
        from .evidence_location import locate_evidence
        try:
            data = json.loads(_strip_fences(raw))
        except (ValueError, TypeError):
            return None
        return coerce_projection(locate_evidence(data, evidence_source(event)), event_kind=event.event_kind)


class DesktopPetEvidenceSemanticCodec(LocalEvidenceMixin, DesktopPetReferenceSemanticCodec):
    request_version = 'desktop-pet-semantic-local-evidence-v9'


class DesktopPetEvidenceInteractionSemanticCodec(LocalEvidenceMixin, DesktopPetReferenceInteractionSemanticCodec):
    request_version = 'desktop-pet-semantic-local-interaction-evidence-v10'


def coerce_located_projection(raw,*,event_kind,turn_id):
    """New pet evidence contract; confidence remains descriptive, never a gate."""
    from dataclasses import replace
    from model_packages.cx_six_seven_v1.semantic_projection import coerce_projection,SemanticProjection
    from model_packages.cx_six_seven_v1.reference_frame import coerce_reference_frame
    if isinstance(raw,SemanticProjection):raw=raw.as_record()
    if not isinstance(raw,dict):return None
    raw=dict(raw);frame=raw.pop('reference_frame',None)
    presence=raw.get('axis_presence')
    if isinstance(presence,dict) and isinstance(presence.get('provided_axes'),list):
        raw['variable_deltas']={key:raw['variable_deltas'][key] for key in presence['provided_axes']}
    result=coerce_projection({**raw,**({'reference_frame':{'delivery_scope':'unresolved'}} if frame is not None else {})},event_kind=event_kind,turn_id=turn_id)
    if result is None or frame is None:return result
    scope=frame.get('delivery_scope')
    if scope not in {'eligible','third_party','unresolved'}:raise ValueError('located reference scope invalid')
    # Reuse the old field normalizer in its non-gating unresolved branch.
    normalized=coerce_reference_frame({**frame,'delivery_scope':'unresolved'},turn_id=turn_id)
    claims=normalized.relation_claims
    if scope!='unresolved' and (not claims or len(claims)!=len(frame.get('relation_claims',()))):
        scope='unresolved';claims=()
    normalized=replace(normalized,delivery_scope=scope,relation_claims=claims,
        resolver_source='local_evidence_locator',schema_version=2)
    return replace(result,reference_frame=normalized)


class CleanEvidenceMixin:
    located_reference_v2=True
    def build_request(self,*,event,recall_hint):
        system,_user=super().build_request(event=event,recall_hint=recall_hint)
        from .interaction_delivery import has_observations
        from .interaction_summary import summarize_interaction
        rows=[]
        for c in event.contents:
            if has_observations((c,)):
                row={'类型':c.kind,'可见性':'私有' if c.private else '公开',**summarize_interaction(c)}
            else:row={'类型':c.kind,'正文':c.description,'可见性':'私有' if c.private else '公开'}
            target=c.parameters.get('body_target')
            if isinstance(target,Mapping) and not has_observations((c,)):
                row['接触部位']={k:target[k] for k in ('name','description') if k in target}
            row['对象']=['current_npc' if x==event.owner_ref else 'user' for x in c.recipient_refs]
            rows.append(row)
        facts={'事件方向':event.event_kind,'实际说话或行动者':'current_npc' if event.actor_ref==event.owner_ref else 'user','内容':rows}
        heading='【交互事实摘要】' if has_observations(event.contents) else '【当前已发生事件】'
        user='【已召回上下文】\n'+(recall_hint or '（无本轮召回）')+'\n'+heading+'\n'+json.dumps(facts,ensure_ascii=False)
        return system,user

    def parse_event_response(self,raw,*,event):
        from model_packages.cx_six_seven_v1.semantic_measurement import _strip_fences
        from .evidence_location import locate_evidence
        try:data=json.loads(_strip_fences(raw))
        except (ValueError,TypeError):return None
        data=locate_evidence(data,evidence_source(event))
        return coerce_located_projection(data,event_kind=event.event_kind,turn_id=event.event_id)


class DesktopPetCleanEvidenceSemanticCodec(CleanEvidenceMixin,DesktopPetEvidenceSemanticCodec):
    request_version='desktop-pet-clean-located-evidence-v12'


class DesktopPetCleanEvidenceInteractionSemanticCodec(CleanEvidenceMixin,DesktopPetEvidenceInteractionSemanticCodec):
    request_version='desktop-pet-clean-located-interaction-v13'


class EvidenceMagnitudeMixin:
    """Versioned evidence/scale wording; original parser and numerical rules."""
    def build_request(self,*,event,recall_hint):
        system,user=super().build_request(event=event,recall_hint=recall_hint)
        old=RESOURCE_ROOT.joinpath('evidence_projection.md').read_text(encoding='utf8')
        if system.count(old)!=1:raise ValueError('semantic base prompt must occur exactly once')
        return system.replace(old,RESOURCE_ROOT.joinpath('evidence_magnitude.md').read_text(encoding='utf8'),1),user


class DesktopPetMagnitudeSemanticCodec(EvidenceMagnitudeMixin,DesktopPetCleanEvidenceSemanticCodec):
    request_version='desktop-pet-evidence-magnitude-v15'


class DesktopPetMagnitudeInteractionSemanticCodec(EvidenceMagnitudeMixin,DesktopPetCleanEvidenceInteractionSemanticCodec):
    request_version='desktop-pet-interaction-magnitude-v16'


class DesktopPetCalibratedSemanticCodec(DesktopPetMagnitudeSemanticCodec):
    request_version='desktop-pet-local-calibrated-evidence-v18'
    local_calibration=True

class DesktopPetCalibratedInteractionSemanticCodec(DesktopPetMagnitudeInteractionSemanticCodec):
    request_version='desktop-pet-local-calibrated-interaction-v19'
    local_calibration=True


class DesktopPetConstrainedSemanticCodec(DesktopPetCalibratedSemanticCodec):
    request_version='desktop-pet-joint-constrained-evidence-v21'
    calibration_version='desktop-pet-joint-axis-constraint-v4'


class DesktopPetConstrainedInteractionSemanticCodec(DesktopPetCalibratedInteractionSemanticCodec):
    request_version='desktop-pet-joint-constrained-interaction-v22'
    calibration_version='desktop-pet-joint-axis-constraint-v4'


class GroundedRecallCueMixin:
    memory_recall_version='desktop-pet-grounded-retention-recall-v2'
    def build_request(self,*,event,recall_hint):
        system,user=super().build_request(event=event,recall_hint=recall_hint)
        system+='\n额外输出 memory_cues 数组，每项为 {"text":"具体主题短语","evidence_text":"唯一定位的原文片段"}。text取自该原文片段中的连续短语，表示本次具体对象、行为或事实主题；同一事实可分别列出可再次提问的对象和主题。证据来自本次已发生事件，主题使用原文中的具体称呼。没有具体主题时输出空数组。资源合同：最多16项，text最多48字符，evidence_text最多256字符。'
        return system,user


class DesktopPetGroundedSemanticCodec(GroundedRecallCueMixin,DesktopPetConstrainedSemanticCodec):
    request_version='desktop-pet-grounded-recall-evidence-v24'


class DesktopPetGroundedInteractionSemanticCodec(GroundedRecallCueMixin,DesktopPetConstrainedInteractionSemanticCodec):
    request_version='desktop-pet-grounded-recall-interaction-v25'


class AtomicGroundedRecallCueMixin:
    def build_request(self,*,event,recall_hint):
        system,user=super().build_request(event=event,recall_hint=recall_hint)
        system+='\n跨轮指认粒度：memory_cues中的每项使用能独立指认对象或行为的最短完整核心短语。对象核心与具体属性分别列项；地点修饰、关系连接、询问语气留在evidence_text中。当前执行意向与处置计划保留在事件正文，线索专注本次指认的对象与事实。例如“桌上的红盒子放在哪里”可用原文“红盒子”指认对象。每项text仍须是本次原文的连续片段，evidence_text仍须唯一定位；这只规定检索线索粒度，其余数值量测沿原合同。'
        return system,user


class DesktopPetAtomicGroundedSemanticCodec(AtomicGroundedRecallCueMixin,DesktopPetGroundedSemanticCodec):
    request_version='desktop-pet-atomic-cue-evidence-v27'


class DesktopPetAtomicGroundedInteractionSemanticCodec(AtomicGroundedRecallCueMixin,DesktopPetGroundedInteractionSemanticCodec):
    request_version='desktop-pet-atomic-cue-interaction-v28'
