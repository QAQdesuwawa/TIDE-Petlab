"""Speaker-first presentation of already selected evidence; no recall authority."""

_RECEIVED_FRAGMENT = {
    'speech': '收到这句发言',
    'action': '收到可见的动作呈现',
    'expression': '收到可见的表现',
    'interaction': '收到现场观察',
}
_MATERIAL_KIND = {'话语':'speech', '动作记录':'action', '表现记录':'expression', '现场记录':'interaction'}
_UNKNOWN_ACQUISITION = '角色取得来源未记录'


def received_fragment(kind, *, own_output=False):
    if own_output and kind in {'speech', 'action', 'expression'}:
        return '角色自己的记录'
    return _RECEIVED_FRAGMENT.get(kind, _UNKNOWN_ACQUISITION)


def acquisition_view(row, *, owner, preserve_verified=False):
    parts = row.get('片段材料')
    if not parts:
        return row
    receiver = row.get('本次观察者')
    if preserve_verified and receiver is None and row.get('内容接收者') == owner:
        return row
    own_output = row.get('说话者') == owner
    projected = []
    for part in parts:
        kind = _MATERIAL_KIND.get(part.get('材料性质'))
        if receiver == owner or own_output and kind in {'speech', 'action', 'expression'}:
            source = received_fragment(kind,own_output=own_output)
        else:
            source = _UNKNOWN_ACQUISITION
        projected.append({**part, '角色取得方式': source})
    result = {**row, '片段材料': projected}
    if receiver == owner:
        result.pop('本次观察者')
        result['内容接收者'] = receiver
    return result


def frame_event(row, *, owner, historical, world_sources=False):
    speaker=row.get('说话者')
    body=row.get('正文')
    if not isinstance(speaker,str) or not speaker or not isinstance(body,str):
        return row
    result=dict(row)
    if speaker=='screen:foreground':
        result.pop('说话者')
        result['本轮来源']='本地前台窗口观察'
        return result
    if world_sources and speaker.startswith('world:'):
        result.pop('说话者')
        result['环境来源']=speaker
        binding=result.get('属性归属绑定')
        if isinstance(binding,dict) and binding.get('user')==speaker:
            result['属性归属绑定']={**binding,'user':f'{speaker}（环境对象）'}
        return result
    perspective='自己' if speaker==owner else '另一参与者'
    moment='当时' if historical else '本次'
    result['正文']=f'{speaker}（{perspective}）{moment}的可见内容（原文）：\n{body}'
    return result
