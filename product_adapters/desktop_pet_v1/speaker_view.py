"""Speaker-first presentation of already selected evidence; no recall authority."""

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
