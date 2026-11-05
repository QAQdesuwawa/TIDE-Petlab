"""Read-only occurrence labels; receipt time never replaces world occurrence."""
from datetime import datetime, timezone


def iso(at):
    return datetime.fromtimestamp(at / 1000, tz=timezone.utc).isoformat()


def describe(app, event):
    from .visible_context import _artifact
    source = app._product()['sources'][event.event_id]
    artifact = _artifact(app, source)
    at = event.position.coordinate
    parent = event.event_id
    if artifact is not None:
        original = artifact['source']
        at = original['event']['committed_at_ms']
        parent = original.get('visible_manifest', {}).get('parent_event_id', original['event']['event_id'])
    return {'原发生时间': iso(at), '本角色接收时间': iso(event.position.coordinate),
            '时间含义': '本轮处理这条已发生的记录；接收与处理不表示事情此刻重新发生。'}, parent


def group(rows):
    """Join phase descriptions by verified parent, without dropping any phase."""
    result = []; groups = {}
    for row in rows:
        parent = row.get('父事件')
        if parent is None:
            result.append(row); continue
        key = (row['实际说话或行动者'], parent)
        if key not in groups:
            item = {'实际说话或行动者': key[0], '同一回应的已见阶段': []}
            groups[key] = item; result.append(item)
        groups[key]['同一回应的已见阶段'].append({k:v for k,v in row.items() if k not in ('实际说话或行动者','父事件')})
    for item in result:
        if '同一回应的已见阶段' in item:
            item['同一回应的已见阶段'].sort(key=lambda r:r['事实时序']['原发生时间'])
    return result
