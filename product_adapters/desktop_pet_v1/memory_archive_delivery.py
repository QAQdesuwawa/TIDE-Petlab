"""Read-only delivery of archived cognition and verified event relationships."""
import re


def collect_notes(app, cards, sources, visibility):
    from .metacognition import execution_note
    from petlab_app.writer_visibility import memory_contents

    notes = {}
    for card in cards:
        provenance = card.provenance
        coverage = provenance.get('source_content_ids', {})
        for identity in provenance['selected_memory_ids']:
            source = sources[identity]
            note = execution_note(source, app.owner_ref)
            if note is None:
                continue
            projected = coverage.get(identity, provenance.get('projected_content_ids'))
            if projected is not None:
                if set(projected) != {c['content_id'] for c in source['event']['contents']}:
                    continue
            if visibility is not None:
                memory = app.runtime.memory_instance(identity)
                if memory_contents(app, memory, visibility) != memory.event.visible_contents(app.owner_ref):
                    continue
            notes[identity] = note
    return notes


def behavior_definitions(rows):
    from model_packages.desktop_pet_v1.shared_meal import handoff_evidence_definition

    keys = {part.get('行为') for row in rows for part in row.get('内容属性', ())
            if part.get('类型') == 'action' and isinstance(part.get('行为'), str)}
    handoff = handoff_evidence_definition(keys)
    return [handoff] if handoff is not None else []


def attach_behavior_distinctions(fields):
    from model_packages.desktop_pet_v1.shared_meal import handoff_behavior_distinctions

    rows = fields.get('相关记忆（按发生顺序）', (fields,))
    paths = (fragment.get('行为路径') for row in rows for fragment in row.get('原事件片段', ()))
    keys = {path[1] for path in paths if path and path[0] == '身体动作'}
    distinction = handoff_behavior_distinctions(keys)
    if distinction is None:
        return fields
    return {'行为区分参照': [distinction], **fields}


def inline_records(fields, card, notes, *, explain_behavior=False):
    ids = tuple(card.provenance['selected_memory_ids'])
    grouped = '相关记忆（按发生顺序）' in fields
    rows = fields['相关记忆（按发生顺序）'] if grouped else (fields,)
    if len(rows) != len(ids):
        raise ValueError('memory archive rows differ from source events')
    markers = {identity: row['记忆标记'] for identity, row in zip(ids, rows, strict=True)}
    edges = {identity: [] for identity in ids}
    consumed = []
    for child in card.subcards:
        if child.kind != 'memory_link_subcard':
            continue
        pairs = child.provenance.get('reply_pairs')
        label = '后续实际回复'
        if pairs is None:
            pairs = child.provenance.get('related_chains')
            label = '被唤起并参与后续正式结算'
        if pairs is None:
            continue
        endpoints = dict(markers)
        if child.provenance.get('current_event_id') is not None:
            endpoints[child.provenance['current_event_id']] = '本次输入'
        for parent, following in pairs:
            link = {'关系': label, '事件': endpoints[following]}
            if link not in edges[parent]:
                edges[parent].append(link)
        consumed.append(child)

    updated = []
    for identity, row in zip(ids, rows, strict=True):
        note = notes.get(identity, {})
        basis = note.get('basis')
        purpose = note.get('purpose')
        entry = {**row, '当时私人判断依据原文': basis,
                 '当时表达目的': {'对象': note['speech_targets'], '原文': purpose} if purpose else None}
        if re.search(r'(?<![A-Za-z])R[0-9]+', (basis or '') + ' ' + (purpose or '')):
            entry['私人记录编号范围'] = '编号属于当时的请求'
        if edges[identity]:
            entry['后续回执关系'] = edges[identity]
        updated.append(entry)
    result = {**fields, '相关记忆（按发生顺序）': updated} if grouped else updated[0]
    if explain_behavior:
        definitions = behavior_definitions(rows)
        if definitions:
            result = {'本卡涉及的行为树定义': definitions, **result}
    return result, tuple(consumed)
