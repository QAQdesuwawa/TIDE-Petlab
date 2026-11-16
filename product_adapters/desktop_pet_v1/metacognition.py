"""Optional private metacognition metadata on the desktop Writer receipt."""
import hashlib
import json
import os

from framework.model_api import model_data_fingerprint as fingerprint, thaw_model_data

LEGACY_VERSION = 'desktop-pet-metacognition-v1'
VERSION = 'desktop-pet-metacognition-v2'
_SEALED_V1_INSTRUCTION = (
    '\n可选字段 metacognition：{"basis":"本轮根据什么作判断",'
    '"purpose":"若要讲话，为什么此刻对选中的对象说；否则 null",'
    '"lesson":null}。仅当已交付的同伴回应确实给了新证据、让你调整做法时，'
    'lesson 可写 {"memory_id":"下列反馈标记","change":"以后遇到什么情况会怎么做"}。'
    '理由和经验只供自己阅读。不要在台词里念这些字段。'
)


INSTRUCTION = (
    '\n可选字段 metacognition：{"basis":"依据当前事实或已交付的记忆，简述本次判断依据",'
    '"purpose":"为什么要向所选对象说这句话；无话则为 null"}。'
    '判断依据和表达目的仅供角色自己阅读。'
)
CLAIM_READING_BASIS = '简述影响本次回应的依据，注明谁说的、直接观察到什么，或哪一部分是推测'
TESTIMONY_FIRST_INSTRUCTION = (
    '\n先填写 JSON 的 metacognition，再填写 speech 和动作。'
    'basis 先写“某人说了什么；哪条回执或快照支持什么；还有什么待确认”，'
    '只核对影响本轮回应的具体主张。purpose 写为何此刻对这个人开口，无话可为 null。'
    '这两项都供角色自己阅读。'
)


SOURCE_CONTINUITY_INSTRUCTION = (
    '\n依次写 metacognition.basis、metacognition.purpose 和 speech。'
    'basis 分别写影响回应的结论、角色如何得知、仍不确定的部分。'
    'purpose 写为什么此刻对这个人说，沿用同一来源和确定程度。'
    'speech 把这个目的演成自然台词，可直接说结论；声称亲眼目击时，须有实际观察记录的观察者就是当前角色。'
    '依据与目的供角色自己阅读。'
)


def execution_note(row, owner):
    event = row.get('event', {})
    if (row.get('origin') != 'execution' or event.get('owner_ref') != owner
            or event.get('actor_ref') != owner):
        return None
    receipt = row['evidence']['fact']['model_payload']['execution_receipt']
    evidence = receipt['realization_evidence']
    meta = evidence.get('metacognition')
    for key in ('expression_children_evidence', 'speech_child_evidence'):
        meta = meta or evidence.get(key, {}).get('metacognition')
    if not meta or not receipt['completed_contents']:
        return None
    note = {'basis':meta['basis']}
    listeners = {ref for c in receipt['completed_contents'] if c['kind']=='speech'
                 for ref in c['recipient_refs']}
    targets = list(dict.fromkeys(ref for ref in meta.get('speech_targets') or ()
                                 if ref in listeners))
    if targets and meta.get('purpose'):
        note.update(purpose=meta['purpose'],speech_targets=targets)
    return note


def freeze_context(app, delivery, *, visibility=None):
    sources = app.runtime.session.profile.get('desktop_pet_product_v1', {}).get('sources', {})
    notes = []
    for alias, identity in delivery['memory_aliases'].items():
        if visibility is not None:
            from petlab_app.writer_visibility import memory_contents
            memory=app.runtime.memory_instance(identity)
            if memory_contents(app,memory,visibility)!=memory.event.visible_contents(app.owner_ref):
                continue
        note = execution_note(sources.get(identity, {}), app.owner_ref)
        if note is not None:
            notes.append({'memory_id':alias,**note})
    return {'version':VERSION,'notes':notes}


def augment(delivery, context, budget, *, label_judgments=False,evidence_review=False,claim_reading=False,testimony_first=False,carry_evidence_source=False,note_ages=None):
    # Reconstruct old frozen requests bit for bit. This branch never builds new inputs.
    if context['version'] == LEGACY_VERSION:
        suffix = _SEALED_V1_INSTRUCTION
        if context['feedback']:
            suffix += '\n可用作反馈依据的标记：' + json.dumps(context['feedback'],ensure_ascii=False)
        current = context.get('current_feedback')
        if isinstance(current,dict):
            suffix += '\n当前同伴反馈 CURRENT：' + json.dumps(current,ensure_ascii=False)
        elif current:
            suffix += '\n当前这句同伴话语也可作为反馈证据，标记 CURRENT。'
        if context['notes']:
            suffix += '\n你对已召回经历的私人记录：' + json.dumps(context['notes'],ensure_ascii=False)
    elif context['version'] == VERSION:
        suffix=INSTRUCTION
        if carry_evidence_source:
            suffix=SOURCE_CONTINUITY_INSTRUCTION
        elif testimony_first:
            suffix=TESTIMONY_FIRST_INSTRUCTION
        elif claim_reading:
            suffix=INSTRUCTION.replace('依据当前事实或已交付的记忆，简述本次判断依据',
                CLAIM_READING_BASIS)
        elif evidence_review:
            suffix=INSTRUCTION.replace('依据当前事实或已交付的记忆，简述本次判断依据',
                '简述判断的来源，如执行回执、现场快照、某人的说法或自己的推测')
        instruction_size=len(TESTIMONY_FIRST_INSTRUCTION if carry_evidence_source else suffix)
        note_scope='附注秒数取对应旧事件的发生时间。原文里的“现在”“刚刚”指当时，原有 R 编号属于当时的请求。'
        if note_ages is not None:
            instruction_size=len(suffix)+len(note_scope)
        notes = []
        for note in context['notes']:
            if note_ages is not None:
                age=note_ages.get(note['memory_id'])
                if age is None:
                    continue
                note={'对应本轮记忆':note['memory_id'],'距处理时刻（秒）':age,
                      '当时判断原文':note['basis'],
                      **({'当时表达目的原文':note['purpose'],'当时对象':note['speech_targets']}
                         if note.get('purpose') else {})}
            updated = [*notes,note]
            if instruction_size + len(json.dumps(updated,ensure_ascii=False)) > 1750:
                continue
            notes = updated
        if notes:
            heading='你对已召回经历的当时判断（私人记录，可能有推测）' if label_judgments else '你对已召回经历的私人记录'
            if note_ages is not None:
                suffix += '\n'+note_scope
            suffix += '\n'+heading+'：' + json.dumps(notes,ensure_ascii=False)
    else:
        raise ValueError('unsupported metacognition context')

    result = thaw_model_data(delivery)
    result.pop('fingerprint')
    result['system'] += suffix
    result['budget_chars'] = max(budget, len(result['system']) + len(result['user']))
    result['prompt_fingerprint'] = hashlib.sha256(
        (result['system']+'\0'+result['user']).encode('utf8')).hexdigest()
    return {**result,'fingerprint':fingerprint(result)}


def split(text):
    wire = json.loads(text)
    if not isinstance(wire, dict) or 'metacognition' not in wire:
        return text,None
    note = wire.pop('metacognition')
    if not isinstance(note, dict):
        return json.dumps(wire,ensure_ascii=False),None

    def bounded(value, limit):
        return value.strip()[:limit] if isinstance(value,str) and value.strip() else None

    basis = bounded(note.get('basis'), 130)
    purpose = bounded(note.get('purpose'), 110)
    return json.dumps(wire,ensure_ascii=False), (
        {'version':VERSION,'basis':basis,'purpose':purpose} if basis else None)


def enabled():
    return os.environ.get('PETLAB_METACOGNITION') == '1'
