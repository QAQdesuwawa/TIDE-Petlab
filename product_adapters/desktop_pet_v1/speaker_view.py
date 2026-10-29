"""Speaker-first presentation of already selected evidence; no recall authority."""

def frame_event(row, *, owner, historical):
    speaker=row.get('说话者')
    body=row.get('正文')
    if not isinstance(speaker,str) or not speaker or not isinstance(body,str):
        return row
    result=dict(row)
    perspective='自己' if speaker==owner else '另一参与者'
    moment='当时' if historical else '本次'
    result['正文']=f'{speaker}（{perspective}）{moment}的可见内容（原文）：\n{body}'
    return result
