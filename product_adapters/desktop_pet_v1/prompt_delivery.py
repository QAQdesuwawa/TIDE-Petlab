"""Reuse the existing packet renderer and shared technical budget."""
from agents.responder import compose_responder_packet
from framework.model_api import model_data_fingerprint
from model_packages.desktop_pet_v1 import writer as codec
from .memory_delivery import memory_aliases
from model_packages.desktop_pet_v1.presentation import render_guidance

LEGACY_VERSION="desktop-pet-prompt-delivery-v1"
PREVIOUS_VERSION="desktop-pet-prompt-delivery-v2"
EXPRESSIVE_VERSION="desktop-pet-prompt-delivery-v3"
VOICE_VERSION="desktop-pet-prompt-delivery-v4"
VERSION="desktop-pet-prompt-delivery-v5-readable"
CAPABILITY_VERSION="desktop-pet-prompt-delivery-v6-visual-capabilities"
READABLE_VERSION="desktop-pet-prompt-delivery-v7-public-text"
MARKER_VERSION="desktop-pet-prompt-delivery-v8-grounded-memory-markers"
EVIDENCE_VERSION="desktop-pet-prompt-delivery-v9-memory-evidence"
EVIDENCE_MARKER_VERSION="desktop-pet-prompt-delivery-v10-memory-evidence-markers"
OPTIONAL_IDLE_VERSION="desktop-pet-prompt-delivery-v11-optional-idle"
OPTIONAL_IDLE_MARKER_VERSION="desktop-pet-prompt-delivery-v12-optional-idle-markers"
OPPORTUNITY_VERSION="desktop-pet-prompt-delivery-v13-idle-opportunity"
OPPORTUNITY_MARKER_VERSION="desktop-pet-prompt-delivery-v14-idle-opportunity-markers"
INDEPENDENT_VERSION="desktop-pet-prompt-delivery-v15-independent-idle-children"
INDEPENDENT_MARKER_VERSION="desktop-pet-prompt-delivery-v16-independent-idle-children-markers"
TIMED_VERSION="desktop-pet-prompt-delivery-v17-timed-idle-opportunity"
TIMED_MARKER_VERSION="desktop-pet-prompt-delivery-v18-timed-idle-opportunity-markers"
SHARED_VERSION="desktop-pet-prompt-delivery-v19-shared-event-bodies"
SHARED_MARKER_VERSION="desktop-pet-prompt-delivery-v20-shared-event-bodies-markers"
TYPED_VERSION="desktop-pet-prompt-delivery-v21-subtype-references"
TYPED_MARKER_VERSION="desktop-pet-prompt-delivery-v22-subtype-references-markers"
QUERY_VERSION="desktop-pet-prompt-delivery-v23-query-reference"
QUERY_MARKER_VERSION="desktop-pet-prompt-delivery-v24-query-reference-markers"
SPEAKER_VERSION="desktop-pet-prompt-delivery-v25-speaker-framed"
SPEAKER_MARKER_VERSION="desktop-pet-prompt-delivery-v26-speaker-framed-markers"
CONVERSATION_VERSION="desktop-pet-prompt-delivery-v27-conversation-context"
CONVERSATION_MARKER_VERSION="desktop-pet-prompt-delivery-v28-conversation-context-markers"
METADATA_VERSION="desktop-pet-prompt-delivery-v29-shared-memory-metadata"
METADATA_MARKER_VERSION="desktop-pet-prompt-delivery-v30-shared-memory-metadata-markers"
CONTINUITY_VERSION="desktop-pet-prompt-delivery-v31-typed-continuity"
CONTINUITY_MARKER_VERSION="desktop-pet-prompt-delivery-v32-typed-continuity-markers"
CONTINUITY_GUIDANCE='前文中本角色已经说出口的话与实际动作阶段各自成立，当前回应沿这些既有表态和进展继续。当下态度落实为措辞、语气与节奏；本轮speech与actions共同表达同一次选择，动作完成进度由实际执行记录提供。'
DEFAULT_PET_PROMPT_BUDGET_CHARS = 16384

def compile_delivery(packet, *, budget_chars=None, delivery_version=VERSION,body_context=None,action_sequence_context=None):
    actual_version=delivery_version
    continuity=delivery_version in {CONTINUITY_VERSION,CONTINUITY_MARKER_VERSION}
    if continuity:delivery_version=METADATA_MARKER_VERSION if delivery_version==CONTINUITY_MARKER_VERSION else METADATA_VERSION
    shared_metadata=delivery_version in {METADATA_VERSION,METADATA_MARKER_VERSION}
    conversation_context=shared_metadata or delivery_version in {CONVERSATION_VERSION,CONVERSATION_MARKER_VERSION}
    speaker_framing=conversation_context or delivery_version in {SPEAKER_VERSION,SPEAKER_MARKER_VERSION}
    query_reference=speaker_framing or delivery_version in {QUERY_VERSION,QUERY_MARKER_VERSION}
    typed_references=query_reference or delivery_version in {TYPED_VERSION,TYPED_MARKER_VERSION}
    shared_all=typed_references or delivery_version in {SHARED_VERSION,SHARED_MARKER_VERSION}
    if shared_all:
        idle=any('idle_opening_reference' in card.fields for card in packet.sections['form'])
        marked=delivery_version in {SHARED_MARKER_VERSION,TYPED_MARKER_VERSION,QUERY_MARKER_VERSION,SPEAKER_MARKER_VERSION,CONVERSATION_MARKER_VERSION,METADATA_MARKER_VERSION}
        delivery_version=(TIMED_MARKER_VERSION if marked else TIMED_VERSION) if idle else (EVIDENCE_MARKER_VERSION if marked else EVIDENCE_VERSION)
    timed=delivery_version in {TIMED_VERSION,TIMED_MARKER_VERSION}
    independent=timed or delivery_version in {INDEPENDENT_VERSION,INDEPENDENT_MARKER_VERSION}
    opportunity=independent or delivery_version in {OPPORTUNITY_VERSION,OPPORTUNITY_MARKER_VERSION}
    optional_idle=opportunity or delivery_version in {OPTIONAL_IDLE_VERSION,OPTIONAL_IDLE_MARKER_VERSION}
    if optional_idle:delivery_version=EVIDENCE_MARKER_VERSION if delivery_version in {OPTIONAL_IDLE_MARKER_VERSION,OPPORTUNITY_MARKER_VERSION,INDEPENDENT_MARKER_VERSION,TIMED_MARKER_VERSION} else EVIDENCE_VERSION
    if delivery_version not in {LEGACY_VERSION,PREVIOUS_VERSION,EXPRESSIVE_VERSION,VOICE_VERSION,VERSION,CAPABILITY_VERSION,READABLE_VERSION,MARKER_VERSION,EVIDENCE_VERSION,EVIDENCE_MARKER_VERSION}:raise ValueError("unsupported prompt delivery version")
    guides=[card.fields["subtype_realization_guidance"] for card in packet.sections["form"]
        if "subtype_realization_guidance" in card.fields]
    if len(guides)>1:raise ValueError("writer requires one subtype reference contract")
    rendered_guidance=render_guidance(guides[0]) if guides else ""
    # The verified form already carries every reference. v1 is retained for explicit replay.
    core_text=codec.SYSTEM+("\n\n"+rendered_guidance if guides and delivery_version==LEGACY_VERSION else "")
    if delivery_version in {EXPRESSIVE_VERSION,VOICE_VERSION,VERSION,CAPABILITY_VERSION}:
        core_text=("以当前角色的身份，向眼前的对方说这一轮实际想说的话。人物的自尊、孩子气、亲疏和当下感受要体现在措辞、句子节奏和自然的语气词里；把人物设定演出来，不讲解设定。日常相处用口语，回应具体事情；需要认真解决问题时才采用对应的说明方式。话可以短、不完整、有停顿，按此刻的态度说够即可，不为凑一段完整答复补解释和服务式收尾。没有固定口头禅或必用语气词。\n"
            "下面是这轮已知事实、人物资料、已选表达方向和正式交付的记忆。当前量测是一次事件的增量，累计状态是已经结算的背景；据已选方向落实话语，不改心理数值、不另选行为。只知道交付过的事，不编造过去或已经完成的动作；交互定义与观测的事实范围保持，私有内容不冒充对方知道的内容。\n"
            "只输出JSON对象，字段speech为实际显示的话，reported_used_memory_ids为确实参考的已交付记忆标记字符串数组；没用记忆则为空数组。只在记忆正文支持时谈具体往事。不要输出后台说明、目标、权限、心理数值或其他字段。")
    if delivery_version in {VOICE_VERSION,VERSION,CAPABILITY_VERSION}:
        core_text=core_text.replace("人物的自尊、孩子气、亲疏和当下感受", "当前人物资料中的性格、亲疏和当下感受")
        core_text=core_text.replace("话可以短、不完整、有停顿，按此刻的态度说够即可，不为凑一段完整答复补解释和服务式收尾。没有固定口头禅或必用语气词。", "直接回应眼前具体事情，话说够即可；句子节奏与语气词随实际表达自然形成，不必每轮按感叹、解释、提要求的次序凑齐一套反应。没有固定口头禅或必用语气词。")
        core_text+="\n鼠标观测只证明交互定义中的实际操控。路径转角不能作为身体旋转、转圈或眩晕的经历；起始命中部位不能作为持续抓扯该部位的证据。感受可以表达，但发生经过必须沿实际观测。"
    if delivery_version in {VERSION,CAPABILITY_VERSION}:
        core_text+="\n桌宠的日常互动和主动搭话通常只说1–2句，目标8–35个汉字，说完眼前这一点就停。不写连续动作旁白，不把一次反应展开成小剧场；用户明确要求解释或详细回答时，按实际需要展开。保持人物原有语气，不加固定台词。"
    if any("presentation_contract" in card.fields for card in packet.sections["form"]):
        if delivery_version==CAPABILITY_VERSION:
            core_text+="\n另一个必需字段expression从本轮表情能力声明的编码中选择。声明中的描述是当前形象的实际画面；选与本轮话语态度相称的展示码，动画由外部播放。"
        elif delivery_version in {EXPRESSIVE_VERSION,VOICE_VERSION,VERSION,CAPABILITY_VERSION}:
            core_text+="\n另一个必需字段expression从normal/happy/sad/aggrieved/angry中选择。展示码描述话语的态度，不是重新量测或修改心理状态。按实际表达的态度选码：喜欢玩耍可以高兴，抗拒、委屈或生气选相称的负向码；不要因为没有新情绪增量就默认normal。话语在抗议时不选平静的normal；普通平静交流仍可normal。动画执行由外部完成。"
        else:
            core_text=core_text.replace('"reported_used_memory_ids":[]}','"reported_used_memory_ids":[],"expression":"normal"}')
            core_text+="\nexpression必须从normal/happy/sad/aggrieved/angry五个编码选一个，与本轮已给状态、表达方向和实际话语相称。只选已有展示码，不推断或修改情绪数值，不选择动作；正常并不要求笑脸。"
    original_packet=packet
    if delivery_version in {READABLE_VERSION,MARKER_VERSION,EVIDENCE_VERSION,EVIDENCE_MARKER_VERSION}:
        from .readable_prompt import project
        packet=project(packet,memory_evidence=delivery_version in {EVIDENCE_VERSION,EVIDENCE_MARKER_VERSION},shared_bodies=optional_idle or shared_all,optional_context=opportunity,independent_children=independent,current_time=timed,subtype_references=typed_references,speaker_framing=speaker_framing,conversation_context=conversation_context,shared_metadata=shared_metadata,body_context=body_context,action_sequence_context=action_sequence_context)
        core_text=("以当前角色的身份，依据人物资料直接回应当前输入。性格和当下态度体现在自然口语与句子节奏中。日常回应通常一两句、约8–35个汉字，详细问题按实际需要展开。\n"
            "当前输入和正式记忆给出已经发生的事实，已选表达资源给出本轮态度。身体行动的选择和播放由外部完成；本轮讲话与已选行动计划配合，具体往事以交付的记忆正文为据。\n"
            "输出JSON：speech为对对方说出的感受、观点或回应；其中身体姿势与运动的事实沿已交付的完成记录表达。reported_used_memory_ids为实际参考的记忆标记数组，未参考时填空数组。")
        if any('presentation_contract' in card.fields for card in packet.sections['form']):
            core_text+="\n另含expression字段，从表情能力声明选择与本轮话语态度相称的展示码。"
    if delivery_version in {EVIDENCE_VERSION,EVIDENCE_MARKER_VERSION}:
        core_text=core_text.replace(
            "当前输入和正式记忆给出已经发生的事实，已选表达资源给出本轮态度。",
            "当前输入是眼前正在回应的内容，已选表达资源给出本轮态度。历史记忆分别记录谁当时说了什么、发生了什么；话语中的意向、评价和设想按当时的话语理解，实际动作及完成状态以执行记录为据。")
        core_text+="\n回应的重点是对方本轮实际表达的问题或感受，态度体现在回应的语气里。"
    if delivery_version in {MARKER_VERSION,EVIDENCE_MARKER_VERSION}:
        core_text+="\nreported_used_memory_ids逐项填写本轮已交付的记忆标记，支持标记字符串或对应整数，例如[\"1\"]或[1]。"
    if continuity:
        core_text+='\n'+CONTINUITY_GUIDANCE
    if optional_idle:
        core_text+="\n本轮是获准的主动表达机会，眼前没有新用户输入。speech可为null，表示此刻选择安静；有当下想说的内容时才填写话语。安静时reported_used_memory_ids填空数组。历史中的自身话语是已经说过的话，其中的提问或要求属于当时，不是本轮待完成任务。相同正文参照只共享文字，每次发生仍保留自己的时间与标记。"
    if opportunity:
        core_text=core_text.replace("以当前角色的身份，依据人物资料直接回应当前输入。",
            "以当前角色的身份，依据人物资料落实这次已获准的主动表达机会。")
        core_text=core_text.replace("当前输入是眼前正在回应的内容，已选表达资源给出本轮态度。",
            "本轮当前输入为空，已选表达资源给出此刻态度。")
        core_text=core_text.replace("回应的重点是对方本轮实际表达的问题或感受，态度体现在回应的语气里。",
            "语音子项按当下实际想表达的内容形成，安静也是完整结果。")
    if shared_all and not optional_idle:
        core_text+="\n相同正文参照只共享文字，每次发生仍保留自己的时间与标记。"
    if query_reference:
        core_text+="\n人物资料给出设定背景，已选表达资源给出当下态度。回应中的人物与称呼归属沿当前问题的指代参照、记忆原文及各自说话者理解；态度决定怎样说，事实来源决定在说谁、指什么。"
    private_expression=any(card.fields.get('private_expression_version')==codec.PRIVATE_EXPRESSION_VERSION for card in original_packet.sections['form'])
    if private_expression:
        core_text=("以当前角色的身份，依据完整人物资料、当前输入、已选表达资源和正式交付记忆，产生这一轮表达。性格与态度体现在自然措辞中，日常每个子项简短表达眼前的一点；详细问题按实际需要展开。\n"
            "历史分别记录当时说过的话、私有独白和实际完成的事，按说话者、对象和时间理解；本轮已选行动由外部执行，完成事实以回执为据。\n"
            "输出JSON：speech是对外说出的话，thought是仅角色自己知道的内心独白，两者分别可为null，表示本轮未选择该子项。两者都为空也是安静的结果。thought是角色作品内容。\n"
            "reported_used_memory_ids是对象，包含speech和thought两个记忆标记数组，分别列出该子项实际参考的已交付记忆；没有参考或子项为空时填空数组。")
        if any('presentation_contract' in card.fields for card in original_packet.sections['form']):
            core_text+="\n另含expression字段，从表情能力声明中选择与当下表达态度相称的展示码。"
    if action_sequence_context is not None:
        core_text=core_text.replace('身体行动的选择和播放由外部完成；本轮讲话与已选行动计划配合，','本轮动作从已获准候选中组织为顺序计划，由本地执行；讲话可与动作同时呈现，')
        core_text=core_text.replace('本轮已选行动由外部执行，完成事实以回执为据。','本轮动作从已获准候选中组织为顺序计划，本地执行的实际范围以回执为据。')
        core_text+='\n另含actions字段：按先后顺序填写可选动作编码数组，可为空。各片段在同一父反应内由本地时间轴连续执行。speech可为null；另一角色如何回应由它独立选择。'
    cap = DEFAULT_PET_PROMPT_BUDGET_CHARS if budget_chars is None else budget_chars
    assembly=compose_responder_packet(packet,budget_chars=cap,return_assembly=True,core_text=core_text)
    delivered=tuple(item["card_id"] for item in assembly.delivered_materials)
    # These are already selected facts, not competing optional personality hints.
    # Never dispatch a degraded request that silently loses its character or intent.
    required_lanes = {"current_input", "persona", "conversation", "expression", "form"}
    missing = [card.card_id for lane, cards in packet.sections.items()
        if lane in required_lanes for card in cards if card.card_id not in delivered]
    if missing:
        raise ValueError("required Writer materials excluded by prompt budget: " + ", ".join(missing))
    record={"version":actual_version,"system":assembly.system,"user":assembly.user,
        "budget_chars":assembly.budget_chars,"core_over_budget":assembly.core_over_budget,
        "packet_fingerprint":original_packet.packet_fingerprint,"prompt_fingerprint":assembly.prompt_fingerprint,
        "delivered_materials":assembly.delivered_materials,"rejected_materials":assembly.rejected_materials,
        "budget_overflow_materials":assembly.budget_overflow_materials,
        "memory_aliases":memory_aliases(packet,delivered),
        "expression_ids":[card.source_id for card in packet.sections["expression"] if card.card_id in delivered]}
    if private_expression:record['private_expression_version']=codec.PRIVATE_EXPRESSION_VERSION
    if delivery_version in {READABLE_VERSION,MARKER_VERSION,EVIDENCE_VERSION,EVIDENCE_MARKER_VERSION}:
        record['rendered_packet_fingerprint']=assembly.source_packet_fingerprint
        record['rendering_view_version']='desktop-pet-readable-card-view-v2-memory-evidence' if delivery_version in {EVIDENCE_VERSION,EVIDENCE_MARKER_VERSION} else 'desktop-pet-readable-card-view-v1'
    if optional_idle:record['rendering_view_version']='desktop-pet-readable-card-view-v3-shared-bodies'
    if opportunity:record['rendering_view_version']='desktop-pet-readable-card-view-v4-idle-opportunity'
    if independent:record['rendering_view_version']='desktop-pet-readable-card-view-v5-independent-idle-children'
    if timed:record['rendering_view_version']='desktop-pet-readable-card-view-v6-formal-time'
    if shared_all:record['rendering_view_version']='desktop-pet-readable-card-view-v7-shared-event-bodies'
    if typed_references:record['rendering_view_version']='desktop-pet-readable-card-view-v8-subtype-references'
    if speaker_framing:record['rendering_view_version']='desktop-pet-readable-card-view-v9-speaker-framed'
    if conversation_context:record['rendering_view_version']='desktop-pet-readable-card-view-v10-conversation-context'
    if shared_metadata:record['rendering_view_version']='desktop-pet-readable-card-view-v11-shared-memory-metadata'
    if continuity:record['rendering_view_version']='desktop-pet-readable-card-view-v12-typed-continuity'
    if body_context is not None:
        if delivery_version not in {READABLE_VERSION,MARKER_VERSION,EVIDENCE_VERSION,EVIDENCE_MARKER_VERSION}:
            raise ValueError('selected body projection requires readable delivery')
        from .body_delivery import VERSION as BODY_VERSION
        record['body_delivery_version']=BODY_VERSION
        record['body_context_fingerprint']=model_data_fingerprint(body_context)
    if action_sequence_context is not None:
        from model_packages.desktop_pet_v1.action_sequence import VERSION as SEQUENCE_VERSION
        record['action_sequence_contract']=SEQUENCE_VERSION
        record['action_sequence_context_fingerprint']=model_data_fingerprint(action_sequence_context)
    record["fingerprint"]=model_data_fingerprint(record)
    return record

