"""Place already-delivered current facts in the current turn, without reselecting."""
from dataclasses import replace
import hashlib

LEGACY_VERSION = 'desktop-pet-current-turn-layout-v1'
PROPERTY_VERSION = 'desktop-pet-current-turn-layout-v2-property-owners'
SOURCE_VERSION = 'desktop-pet-current-turn-layout-v3-property-sources'
VERSION = 'desktop-pet-current-turn-layout-v4-occurrence'
ADJACENT_EVIDENCE_VERSION = 'desktop-pet-current-turn-layout-v5-adjacent-evidence'
WORLD_SOURCE_VERSION = 'desktop-pet-current-turn-layout-v6-world-sources'
FACT_SCOPE_VERSION = 'desktop-pet-current-turn-layout-v7-fact-scopes'
RECORDED_DIALOGUE_VERSION = 'desktop-pet-current-turn-layout-v8-recorded-dialogue'
SPEECH_EVIDENCE_VERSION = 'desktop-pet-current-turn-layout-v9-speech-evidence'
PRIVATE_JUDGMENT_VERSION = 'desktop-pet-current-turn-layout-v10-private-judgment'
EVIDENCE_REVIEW_VERSION = 'desktop-pet-current-turn-layout-v11-evidence-review'
CLAIM_READING_VERSION = 'desktop-pet-current-turn-layout-v12-claim-reading'
TESTIMONY_FIRST_VERSION = 'desktop-pet-current-turn-layout-v13-testimony-first'
OPTIONAL_MEMORY_REPORT_VERSION = 'desktop-pet-current-turn-layout-v14-optional-memory-report'
SPEECH_ACTION_FORMAT_VERSION = 'desktop-pet-current-turn-layout-v15-speech-action-format'
SCOPED_EVIDENCE_VERSION = 'desktop-pet-current-turn-layout-v16-scoped-evidence'
COHERENT_PERFORMANCE_VERSION = 'desktop-pet-current-turn-layout-v17-coherent-performance'
PROCESSING_TIME_VERSION = 'desktop-pet-current-turn-layout-v18-processing-time'
SOURCE_CONTINUITY_VERSION = 'desktop-pet-current-turn-layout-v19-source-continuity'
FRAGMENT_MEMORY_VERSION = 'desktop-pet-current-turn-layout-v20-memory-fragments'
from .memory_fragments import FRAGMENT_ROLE_VERSION, SOURCE_TYPED_VERSION

FACT_CARRY_VERSION = 'desktop-pet-current-turn-layout-v23-fact-carry'
OPEN_SCENE_VERSION = 'desktop-pet-current-turn-layout-v24-open-scene'
WEIGHTED_FACT_VERSION = 'desktop-pet-current-turn-layout-v25-weighted-properties'
WEIGHTED_SCENE_VERSION = 'desktop-pet-current-turn-layout-v26-weighted-open-scene'
WEIGHTED_LAYOUTS = (WEIGHTED_FACT_VERSION, WEIGHTED_SCENE_VERSION)
COMPRESSED_FACT_VERSION = 'desktop-pet-current-turn-layout-v27-compressed-properties'
COMPRESSED_SCENE_VERSION = 'desktop-pet-current-turn-layout-v28-compressed-open-scene'
COMPRESSED_LAYOUTS = (COMPRESSED_FACT_VERSION, COMPRESSED_SCENE_VERSION)
STRONGEST_FACT_VERSION = 'desktop-pet-current-turn-layout-v29-strongest-memory'
STRONGEST_SCENE_VERSION = 'desktop-pet-current-turn-layout-v30-strongest-open-scene'
COHERENT_HISTORY_FACT_VERSION = 'desktop-pet-current-turn-layout-v31-coherent-history'
COHERENT_HISTORY_SCENE_VERSION = 'desktop-pet-current-turn-layout-v32-coherent-history-open-scene'
WITNESS_FACT_VERSION = 'desktop-pet-current-turn-layout-v33-witness-attribution'
WITNESS_SCENE_VERSION = 'desktop-pet-current-turn-layout-v34-witness-attribution-open-scene'
INVESTIGATOR_FACT_VERSION = 'desktop-pet-current-turn-layout-v35-investigator'
INVESTIGATOR_SCENE_VERSION = 'desktop-pet-current-turn-layout-v36-investigator-open-scene'
INVESTIGATOR_LAYOUTS = (INVESTIGATOR_FACT_VERSION, INVESTIGATOR_SCENE_VERSION)
PROFILER_FACT_VERSION = 'desktop-pet-current-turn-layout-v37-third-person-profiler'
PROFILER_SCENE_VERSION = 'desktop-pet-current-turn-layout-v38-third-person-profiler-open-scene'
PROFILER_LAYOUTS = (PROFILER_FACT_VERSION, PROFILER_SCENE_VERSION)
ROLE_PERSPECTIVE_FACT_VERSION = 'desktop-pet-current-turn-layout-v39-role-perspective'
ROLE_PERSPECTIVE_SCENE_VERSION = 'desktop-pet-current-turn-layout-v40-role-perspective-open-scene'
ROLE_PERSPECTIVE_LAYOUTS = (ROLE_PERSPECTIVE_FACT_VERSION, ROLE_PERSPECTIVE_SCENE_VERSION)
EXPRESSION_ONLY_FACT_VERSION = 'desktop-pet-current-turn-layout-v41-expression-only'
EXPRESSION_ONLY_SCENE_VERSION = 'desktop-pet-current-turn-layout-v42-expression-only-open-scene'
ACQUISITION_FACT_VERSION = 'desktop-pet-current-turn-layout-v43-information-acquisition'
ACQUISITION_SCENE_VERSION = 'desktop-pet-current-turn-layout-v44-information-acquisition-open-scene'
FRAGMENT_SOURCE_FACT_VERSION = 'desktop-pet-current-turn-layout-v45-fragment-source'
FRAGMENT_SOURCE_SCENE_VERSION = 'desktop-pet-current-turn-layout-v46-fragment-source-open-scene'
INTERACTION_SOURCE_FACT_VERSION = 'desktop-pet-current-turn-layout-v47-interaction-source'
INTERACTION_SOURCE_SCENE_VERSION = 'desktop-pet-current-turn-layout-v48-interaction-source-open-scene'
ENVIRONMENT_FACT_VERSION = 'desktop-pet-current-turn-layout-v49-environment'
ENVIRONMENT_SCENE_VERSION = 'desktop-pet-current-turn-layout-v50-environment-open-scene'
ENVIRONMENT_TIMELINE_FACT_VERSION = 'desktop-pet-current-turn-layout-v51-environment-timeline'
ENVIRONMENT_TIMELINE_SCENE_VERSION = 'desktop-pet-current-turn-layout-v52-environment-timeline-open-scene'
WORLD_ACQUISITION_FACT_VERSION = 'desktop-pet-current-turn-layout-v53-world-acquisition'
WORLD_ACQUISITION_SCENE_VERSION = 'desktop-pet-current-turn-layout-v54-world-acquisition-open-scene'
WORLD_SCOPE_FACT_VERSION = 'desktop-pet-current-turn-layout-v55-world-observation-scope'
WORLD_SCOPE_SCENE_VERSION = 'desktop-pet-current-turn-layout-v56-world-observation-scope-open-scene'
STATEMENT_SCOPE_FACT_VERSION = 'desktop-pet-current-turn-layout-v57-statement-source'
STATEMENT_SCOPE_SCENE_VERSION = 'desktop-pet-current-turn-layout-v58-statement-source-open-scene'
COMPLETE_OUTPUT_FACT_VERSION = 'desktop-pet-current-turn-layout-v59-complete-output'
COMPLETE_OUTPUT_SCENE_VERSION = 'desktop-pet-current-turn-layout-v60-complete-output-open-scene'
INTEGRATED_EVIDENCE_FACT_VERSION = 'desktop-pet-current-turn-layout-v61-integrated-evidence'
INTEGRATED_EVIDENCE_SCENE_VERSION = 'desktop-pet-current-turn-layout-v62-integrated-evidence-open-scene'
EVIDENCE_CARRY_FACT_VERSION = 'desktop-pet-current-turn-layout-v63-evidence-carry'
EVIDENCE_CARRY_SCENE_VERSION = 'desktop-pet-current-turn-layout-v64-evidence-carry-open-scene'
PLAYED_FACTS_VERSION = 'desktop-pet-current-turn-layout-v65-played-facts'
PLAYED_FACTS_SCENE_VERSION = 'desktop-pet-current-turn-layout-v66-played-facts-open-scene'
PLAYED_CONTEXT_VERSION = 'desktop-pet-current-turn-layout-v67-played-context'
PLAYED_CONTEXT_SCENE_VERSION = 'desktop-pet-current-turn-layout-v68-played-context-open-scene'
ACQUISITION_BINDING_VERSION = 'desktop-pet-current-turn-layout-v69-acquisition-binding'
ACQUISITION_BINDING_SCENE_VERSION = 'desktop-pet-current-turn-layout-v70-acquisition-binding-open-scene'
PLAIN_JSON_VERSION = 'desktop-pet-current-turn-layout-v71-plain-json'
PLAIN_JSON_SCENE_VERSION = 'desktop-pet-current-turn-layout-v72-plain-json-open-scene'
MEMORY_ARCHIVE_VERSION = 'desktop-pet-current-turn-layout-v73-memory-archive'
MEMORY_ARCHIVE_SCENE_VERSION = 'desktop-pet-current-turn-layout-v74-memory-archive-open-scene'
BEHAVIOR_EVIDENCE_VERSION = 'desktop-pet-current-turn-layout-v75-behavior-evidence'
BEHAVIOR_EVIDENCE_SCENE_VERSION = 'desktop-pet-current-turn-layout-v76-behavior-evidence-open-scene'
FRAGMENT_BEHAVIOR_VERSION = 'desktop-pet-current-turn-layout-v77-fragment-behavior'
FRAGMENT_BEHAVIOR_SCENE_VERSION = 'desktop-pet-current-turn-layout-v78-fragment-behavior-open-scene'
MEAL_ACCEPTANCE_VERSION = 'desktop-pet-current-turn-layout-v79-bound-meal-acceptance'
MEAL_ACCEPTANCE_SCENE_VERSION = 'desktop-pet-current-turn-layout-v80-bound-meal-acceptance-open-scene'
SEGMENTED_MEAL_ACCEPTANCE_VERSION = 'desktop-pet-current-turn-layout-v81-segmented-meal-acceptance'
SEGMENTED_MEAL_ACCEPTANCE_SCENE_VERSION = 'desktop-pet-current-turn-layout-v82-segmented-meal-acceptance-open-scene'
SEGMENTED_MEAL_ACCEPTANCE_LAYOUTS = (SEGMENTED_MEAL_ACCEPTANCE_VERSION, SEGMENTED_MEAL_ACCEPTANCE_SCENE_VERSION)
MEAL_ACCEPTANCE_LAYOUTS = (MEAL_ACCEPTANCE_VERSION, MEAL_ACCEPTANCE_SCENE_VERSION, *SEGMENTED_MEAL_ACCEPTANCE_LAYOUTS)
FRAGMENT_BEHAVIOR_LAYOUTS = (FRAGMENT_BEHAVIOR_VERSION, FRAGMENT_BEHAVIOR_SCENE_VERSION, *MEAL_ACCEPTANCE_LAYOUTS)
BEHAVIOR_EVIDENCE_LAYOUTS = (BEHAVIOR_EVIDENCE_VERSION, BEHAVIOR_EVIDENCE_SCENE_VERSION, *FRAGMENT_BEHAVIOR_LAYOUTS)
MEMORY_ARCHIVE_LAYOUTS = (MEMORY_ARCHIVE_VERSION, MEMORY_ARCHIVE_SCENE_VERSION, *BEHAVIOR_EVIDENCE_LAYOUTS)
PLAIN_JSON_LAYOUTS = (PLAIN_JSON_VERSION, PLAIN_JSON_SCENE_VERSION, *MEMORY_ARCHIVE_LAYOUTS)
ACQUISITION_BINDING_LAYOUTS = (ACQUISITION_BINDING_VERSION, ACQUISITION_BINDING_SCENE_VERSION, *PLAIN_JSON_LAYOUTS)
PLAYED_CONTEXT_LAYOUTS = (PLAYED_CONTEXT_VERSION, PLAYED_CONTEXT_SCENE_VERSION, *ACQUISITION_BINDING_LAYOUTS)
PLAYED_FACTS_LAYOUTS = (PLAYED_FACTS_VERSION, PLAYED_FACTS_SCENE_VERSION, *PLAYED_CONTEXT_LAYOUTS)
EVIDENCE_CARRY_LAYOUTS = (EVIDENCE_CARRY_FACT_VERSION, EVIDENCE_CARRY_SCENE_VERSION, *PLAYED_FACTS_LAYOUTS)
INTEGRATED_EVIDENCE_LAYOUTS = (INTEGRATED_EVIDENCE_FACT_VERSION, INTEGRATED_EVIDENCE_SCENE_VERSION, *EVIDENCE_CARRY_LAYOUTS)
COMPLETE_OUTPUT_LAYOUTS = (COMPLETE_OUTPUT_FACT_VERSION, COMPLETE_OUTPUT_SCENE_VERSION, *INTEGRATED_EVIDENCE_LAYOUTS)
STATEMENT_SCOPE_LAYOUTS = (STATEMENT_SCOPE_FACT_VERSION, STATEMENT_SCOPE_SCENE_VERSION, *COMPLETE_OUTPUT_LAYOUTS)
WORLD_SCOPE_LAYOUTS = (WORLD_SCOPE_FACT_VERSION, WORLD_SCOPE_SCENE_VERSION, *STATEMENT_SCOPE_LAYOUTS)
WORLD_ACQUISITION_LAYOUTS = (WORLD_ACQUISITION_FACT_VERSION, WORLD_ACQUISITION_SCENE_VERSION, *WORLD_SCOPE_LAYOUTS)
ENVIRONMENT_TIMELINE_LAYOUTS = (ENVIRONMENT_TIMELINE_FACT_VERSION, ENVIRONMENT_TIMELINE_SCENE_VERSION, *WORLD_ACQUISITION_LAYOUTS)
ENVIRONMENT_LAYOUTS = (ENVIRONMENT_FACT_VERSION, ENVIRONMENT_SCENE_VERSION, *ENVIRONMENT_TIMELINE_LAYOUTS)
INTERACTION_SOURCE_LAYOUTS = (INTERACTION_SOURCE_FACT_VERSION, INTERACTION_SOURCE_SCENE_VERSION, *ENVIRONMENT_LAYOUTS)
FRAGMENT_SOURCE_LAYOUTS = (FRAGMENT_SOURCE_FACT_VERSION, FRAGMENT_SOURCE_SCENE_VERSION, *INTERACTION_SOURCE_LAYOUTS)
ACQUISITION_LAYOUTS = (ACQUISITION_FACT_VERSION, ACQUISITION_SCENE_VERSION, *FRAGMENT_SOURCE_LAYOUTS)
EXPRESSION_ONLY_LAYOUTS = (EXPRESSION_ONLY_FACT_VERSION, EXPRESSION_ONLY_SCENE_VERSION, *ACQUISITION_LAYOUTS)
WITNESS_LAYOUTS = (WITNESS_FACT_VERSION, WITNESS_SCENE_VERSION, *INVESTIGATOR_LAYOUTS,
                   *PROFILER_LAYOUTS, *ROLE_PERSPECTIVE_LAYOUTS, *EXPRESSION_ONLY_LAYOUTS)
COHERENT_HISTORY_LAYOUTS = (COHERENT_HISTORY_FACT_VERSION, COHERENT_HISTORY_SCENE_VERSION, *WITNESS_LAYOUTS)
STRONGEST_LAYOUTS = (STRONGEST_FACT_VERSION, STRONGEST_SCENE_VERSION, *COHERENT_HISTORY_LAYOUTS)
FACT_CARRY_LAYOUTS = (FACT_CARRY_VERSION, OPEN_SCENE_VERSION, *WEIGHTED_LAYOUTS, *COMPRESSED_LAYOUTS, *STRONGEST_LAYOUTS)
SOURCE_CONTINUITY_LAYOUTS = (SOURCE_CONTINUITY_VERSION, FRAGMENT_MEMORY_VERSION, FRAGMENT_ROLE_VERSION, SOURCE_TYPED_VERSION, *FACT_CARRY_LAYOUTS)
PROCESSING_TIME_LAYOUTS = (PROCESSING_TIME_VERSION, *SOURCE_CONTINUITY_LAYOUTS)
COHERENT_PERFORMANCE_LAYOUTS = (COHERENT_PERFORMANCE_VERSION, *PROCESSING_TIME_LAYOUTS)
SCOPED_EVIDENCE_LAYOUTS = (SCOPED_EVIDENCE_VERSION, *COHERENT_PERFORMANCE_LAYOUTS)
SPEECH_ACTION_FORMAT_LAYOUTS = (SPEECH_ACTION_FORMAT_VERSION, *SCOPED_EVIDENCE_LAYOUTS)
OPTIONAL_MEMORY_REPORT_LAYOUTS = (OPTIONAL_MEMORY_REPORT_VERSION, *SPEECH_ACTION_FORMAT_LAYOUTS)
TESTIMONY_FIRST_LAYOUTS = (TESTIMONY_FIRST_VERSION, *OPTIONAL_MEMORY_REPORT_LAYOUTS)
EVIDENCE_REVIEW_LAYOUTS = (EVIDENCE_REVIEW_VERSION, CLAIM_READING_VERSION, *TESTIMONY_FIRST_LAYOUTS)
PRIVATE_JUDGMENT_LAYOUTS = (PRIVATE_JUDGMENT_VERSION,*EVIDENCE_REVIEW_LAYOUTS)
SPEECH_EVIDENCE_LAYOUTS = (SPEECH_EVIDENCE_VERSION,*PRIVATE_JUDGMENT_LAYOUTS)
RECORDED_DIALOGUE_LAYOUTS = (RECORDED_DIALOGUE_VERSION,*SPEECH_EVIDENCE_LAYOUTS)
FACT_SCOPE_LAYOUTS = (FACT_SCOPE_VERSION, *RECORDED_DIALOGUE_LAYOUTS)
WORLD_LAYOUTS = (WORLD_SOURCE_VERSION, *FACT_SCOPE_LAYOUTS)
ADJACENT_LAYOUTS = (ADJACENT_EVIDENCE_VERSION, *WORLD_LAYOUTS)
FACT_SCOPE_GUIDANCE = '本次事件、现场快照和旧记忆各按自己的时间理解。角色可以猜测；动作和结果之间的联系以对应的执行回执为准。'
EVIDENCE_REVIEW_GUIDANCE = (
    '记忆是可参考的材料，判断时看其中的来源。现场快照反映采样时的状态，'
    '动作和结果之间的联系以对应的执行回执为准。'
    '别人说过的话只能确认他说过，话里的事是否发生要看对应回执；证据不足时，角色可以保留自己的猜测。'
)
CLAIM_READING_GUIDANCE = (
    '先找出会改变这次回应的事实主张，核对人物、时间和哪条材料直接支持它。'
    '从记忆中采用材料时保留来源：话语保留说话者，快照保留采样时的状态，'
    '回执保留实际动作及结果，私人判断保留当时的推测。'
    '例如甲说窗户是乙开的，现场只看到窗户开着。可确认甲这么说过、窗户现在开着，开窗者待确认。'
    '用角色自己的语气自然回应，可以相信、怀疑或追问；转述和推测沿原来的身份表达。'
)
TESTIMONY_FIRST_GUIDANCE = (
    '起草前，像律师核对证词一样做一次简短对照：谁说了什么？哪条已见回执直接支持哪件事？'
    '逐项核对人物、动作、时间和结果。快照只给出采样时的状态；回执保留实际动作及结果。'
    '若某主张只有转述，在依据里写成“某人说……，是否发生待确认”，保留说话者和原来的确定程度。'
    '例如甲说窗户是乙开的，快照显示窗户开着：证词来自甲；快照支持窗户开着；开窗者待确认。'
    '核对完再按人设组织台词和动作。角色可以打趣、相信或怀疑，话语保持角色自己的语气。'
)


NATURAL_SPEECH_GUIDANCE = (
    '来源、回执、卡号和证据核对留在私人判断依据。speech只写角色会对听众说出口的话。'
    '按人设直接讲自己看到、听到或猜到的内容，保留原有的确定程度。'
)


SOURCE_CONTINUITY_SPEECH_GUIDANCE = (
    '证据核对留在私人判断依据。speech 用角色的语气直接讲它知道的结论。'
    '若台词要说自己是怎么知道的，沿用依据中的来源和确定程度。'
)


FACT_CARRY_SPEECH_GUIDANCE = (
    '证据核对留在私人判断依据。表达目的决定想让谁知道什么，可以略过事实、打趣或保留猜测。'
    '台词中的事实沿用依据确认的人物、动作、结果和确定程度；略过细节时也保留这个边界。'
    '角色刻意说反话或编造时，在表达目的中写清它有意这么说。'
)


WITNESS_SPEECH_GUIDANCE = (
    '把角色作为回应者写台词。对方的引语里，“我”指原说话者；speech 里，“我”指你正在演绎的角色。'
    '先确认当前角色是本人目击、听人说、读回执，还是只看到事后状态。'
    '例如对方说“我看见阿宁把窗关了”：相信对方时可以说“原来是阿宁关的呀”或“你看见她关的呀”；'
    '只有材料记载当前角色本人看见关窗时，才可以说“我看见她关的”。'
    '回执确认操作者和结果时可以直接讲结论。快照确认采样时的状态。'
    '台词沿用私人依据核实的身份和确定程度，按角色语气说；证据核对留在私人判断依据。'
)


EXPRESSION_ONLY_GUIDANCE = (
    '按该角色实际知道的内容写台词。角色可以相信同伴，或把不确定的地方留作猜测。'
    '动作记录支持动作，快照支持当时状态；有本人观察才用目击口吻。台词只写角色要说的话。'
)


FRAGMENT_BEHAVIOR_GUIDANCE = (
    '【行为片段】\n'
    '行为路径依次写父类和子项。同一事件可以有多个子项；每个记录阶段只对应所在片段。'
    '行为区分参照只解释相近子项的区别；本次发生了哪些子项及效果，各看对应片段和回执。'
    '话语确认当时说过的话，快照确认采样时的状态。'
)


BEHAVIOR_EVIDENCE_GUIDANCE = (
    '【行为树与证据口径】\n'
    '一个事件可以含有话语、表情、动作等并列子项，各自记录实际发生的部分。'
    '话语能表达意愿、回应或说法。话语播完，只确认说过这段话。'
    '动作的完成阶段对应这一个动作；动作造成的物件或场景变化，以对应执行结果为据。'
    '快照确认采样时的状态。卡内行为定义说明每个子项的含义，卡内原文和回执才说明这次实际发生到哪一步。'
)


EVIDENCE_CARRY_GUIDANCE = (
    '写台词前，先区分角色记得的是一件事本身，还是记得有人说过这件事。'
    '角色自己曾经说过的说法，也只直接证明它当时那样说过。'
    '有原话、本人观察或执行记录直接支持时，可以说清楚对应的事实。'
    '写完检查台词会让听众以为什么已经确定。口吻可以随人设变化，句子的确定程度沿用角色实际掌握的材料。'
    '只记得某个说法时，保留这个来源，或自然说哪一部分记不准。'
    '动作、当前状态和因果也各沿能直接支持它的材料表达。'
)


CARD_EVIDENCE_GUIDANCE = (
    '私人依据只列实际采用的材料。逐张核对它记下的是谁的原话、哪个动作的实际阶段，还是某时刻的状态；'
    '引用多个卡号时，每张只支持自己的那一段，提到某个动作的台词不能算执行过。'
    '先确认前文中双方已说过什么，再判断这句话接在哪一步。'
)


def processing_time_note(at_ms):
    from .event_timeline import iso
    return (
        f'\n【本轮处理时刻】{iso(at_ms)}。各条记忆的秒数都直接以此为参照，正数表示此前发生。'
        '动作阶段只描述其记录时点；已完成或打断的动作沿对应回执理解，只有开始记录时，后续是否持续未知。'
        '快照只确认采样时的状态；本轮拟做的动作待实际播放后才有回执。'
    )


def labels(version):
    from agents.responder import _PACKET_LANE_TITLES
    if version in INTEGRATED_EVIDENCE_LAYOUTS:
        return '本轮收到的原事件','已召回的原事件记录'
    if version in EVIDENCE_REVIEW_LAYOUTS:
        return '本次可见的已发生事件','此前经历 · 记忆材料'
    if version in FACT_SCOPE_LAYOUTS:
        return '本次可见的已发生事件','此前经历 · 已召回记忆'
    return _PACKET_LANE_TITLES['current_input'],_PACKET_LANE_TITLES['memory']


def place_current_input(assembly, packet, *, adjacent_evidence=False,version=None):
    from agents.responder import _PACKET_LANE_TITLES, _render_packet_card

    delivered = {row['card_id'] for row in assembly.delivered_materials}
    blocks = []
    for card in packet.sections['current_input']:
        if card.card_id not in delivered:
            continue
        blocks.append(_PACKET_LANE_TITLES['current_input'] + '：\n' + _render_packet_card(card))
        blocks.extend(_render_packet_card(child) for child in card.subcards
                      if child.subcard_id in delivered)
    if not blocks:
        return assembly
    block = '\n\n'.join(blocks)
    # Only move the exact renderer output, not text selected by names/keywords.
    framed = '\n\n' + block
    if assembly.system.count(framed) != 1:
        raise ValueError('current input renderer boundary differs')
    system = assembly.system.replace(framed, '', 1)
    input_label,memory_label=labels(version)
    user=block.replace(_PACKET_LANE_TITLES['current_input']+'：\n',input_label+'：\n',1)
    if adjacent_evidence:
        evidence = []
        for card in packet.sections['memory']:
            if card.card_id not in delivered:
                continue
            old_heading=_PACKET_LANE_TITLES['memory']+'：\n'
            parts = [old_heading + _render_packet_card(card)]
            parts.extend(_render_packet_card(child) for child in card.subcards
                         if child.subcard_id in delivered)
            rendered = '\n\n'.join(parts)
            framed = '\n\n' + rendered
            if system.count(framed) != 1:
                raise ValueError('memory evidence renderer boundary differs')
            system = system.replace(framed, '', 1)
            evidence.append(memory_label+'：\n'+rendered.removeprefix(old_heading))
        user = '\n\n'.join([*evidence, user])
    return replace(assembly, system=system, user=user,
                   prompt_fingerprint=hashlib.sha256((system + '\0' + user).encode('utf-8')).hexdigest())
