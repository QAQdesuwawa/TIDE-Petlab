"""Factual ordering beside current input; no recall, scoring or clock mutation."""
from petlab_app.session import PRODUCT_KEY,ADMISSION_VERSIONS,POINT_ADMISSION_VERSION


def source_chronology(application,event):
    app=application
    raw=app.runtime.session.profile[PRODUCT_KEY]['sources'][event.event_id]['evidence']
    if raw.get('version') not in ADMISSION_VERSIONS:return None,()
    intervals=[(c.parameters['trajectory'][0][2],c.parameters['trajectory'][-1][2]) for c in event.contents if c.kind=='interaction' and c.parameters.get('trajectory')]
    if raw.get('version')==POINT_ADMISSION_VERSION:
        at=raw['capture_position']['coordinate']
        return '本轮内容保留用户原提交时刻；本栏正式时刻为后台实际接收时刻。',tuple(e.source_binding_ref for e in app.runtime.events if e.event_kind=='npc_output' and e.scope_ref==event.scope_ref and at<e.position.coordinate<=event.position.coordinate)
    if not intervals:return None,()
    start=min(a for a,b in intervals);end=max(b for a,b in intervals)
    outputs=[e for e in app.runtime.events if e.event_kind=='npc_output' and e.scope_ref==event.scope_ref
        and start<e.position.coordinate<=event.position.coordinate
        and any(c.kind in {'speech','action'} for c in e.visible_contents(app.user_ref))]
    words=['本栏时间为后台实际接收时刻；鼠标描述对应原采集区间所见。']
    if any(e.position.coordinate>end for e in outputs):words.append('本段鼠标采样先结束，随后角色完成其他回应，本轮才接收这段排队片段。')
    if any(e.position.coordinate<=end for e in outputs):words.append('本段鼠标采样跨过角色此前输出完成的时刻，前后操作属于同一采集区间。')
    return ''.join(words),tuple(e.source_binding_ref for e in outputs)
