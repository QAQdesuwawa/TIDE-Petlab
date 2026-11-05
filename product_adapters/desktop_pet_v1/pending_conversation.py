"""Frozen, bounded actual public speech received while a response was prepared.

This supplies literal situation context, not a numerical settlement or recall.
"""
import hashlib,json
from framework.model_api import model_data_fingerprint as fp,thaw_model_data

LEGACY_VERSION='petlab-pending-public-speech-v1'
OCCURRENCE_VERSION='petlab-pending-public-speech-v2-occurrence'
PARTICIPANTS_VERSION='petlab-pending-public-speech-v3-participants'
SIBLING_VERSION='petlab-pending-public-speech-v4-sibling-speech'
VERSION='petlab-pending-public-speech-v5-interaction-target'

def _body(app,row,readers):
    w=app.multi_workspace;alias=w.aliases[app.owner_ref]
    actual=w.read_delivery(alias,w.inbox_path(alias,row['delivery']['delivery_id']))
    if actual!=row:raise ValueError('pending conversation delivery changed')
    source=w.source_event(row['projection']['source_event_id'])
    if w.registry.load_session(source['event']['conversation_id']).conversation.kind!='group':return None
    if 'visible' in row:
        from petlab_app.visible_delivery import validate_projection
        contents=validate_projection(source,app.owner_ref,row['visible'])
        words=[c.description for c in contents if c.kind=='speech' and not c.private
            and set(readers)<=set(c.recipient_refs)|{row['projection']['sender_participant_id']}]
    elif row['sender_kind']=='human' and 'human_content' not in source:
        words=[row['projection']['text']] if set(readers)<=set(source['event']['member_snapshot_ids']) else []
    else:words=[]
    if not words:return None
    return {'说话者':row['projection']['sender_participant_id'],'原文':words}


def _versioned_body(app,row,readers,version):
    body=_body(app,row,readers)
    if body is None or version not in (PARTICIPANTS_VERSION,SIBLING_VERSION,VERSION):return body
    from datetime import datetime,timezone
    projection=row['projection']
    targets=row['visible']['target_refs'] if 'visible' in row else projection['addressed_participant_ids']
    target_label='父级互动对象' if version==VERSION and 'visible' in row else '交谈对象'
    return {**body,target_label:list(targets),'本次读者':app.owner_ref,
            '原发生时间':datetime.fromtimestamp(projection['committed_at_ms']/1000,tz=timezone.utc).isoformat(),
            '记录种类':'已发生的公开话语' if version in (SIBLING_VERSION,VERSION) else '另一次已发生的公开话语'}

def build(app,event_id,readers):
    capture=getattr(app,'pending_visible_inputs',None)
    if capture is None:return None
    source=app._product()['sources'].get(event_id)
    if source is None:return None
    after=source['event']['position']['coordinate'];rows=[];bodies=[]
    from .visible_context import _artifact
    artifact=_artifact(app,source)
    conversation=None;parent=None
    if artifact is not None:
        after=artifact['source']['event']['committed_at_ms']
        conversation=artifact['source']['event']['conversation_id']
        parent=artifact['source'].get('visible_manifest',{}).get('parent_event_id')
    for row in reversed(capture()):
        sibling=parent is not None and row.get('visible',{}).get('parent_event_id')==parent
        if row['projection']['committed_at_ms']<=after and not sibling:continue
        if conversation is not None and row['projection']['conversation_id']!=conversation:continue
        body=_versioned_body(app,row,readers,VERSION)
        if body is None or body in bodies:continue
        if len(json.dumps([body,*bodies],ensure_ascii=False))>2048:break
        rows.insert(0,row);bodies.insert(0,body)
    if not rows:return None
    value={'version':VERSION,'owner':app.owner_ref,'event_id':event_id,'readers':list(readers),
        'rows':rows,'bodies':bodies,'formal_recall':False,'state_commit_authority':False}
    return {**value,'fingerprint':fp(value)}

def validate(app,context,event_id,readers):
    value=thaw_model_data(context)
    if (value['version'] not in (LEGACY_VERSION,OCCURRENCE_VERSION,PARTICIPANTS_VERSION,SIBLING_VERSION,VERSION) or value['owner']!=app.owner_ref or value['event_id']!=event_id
        or value['readers']!=list(readers) or value['fingerprint']!=fp({k:v for k,v in value.items() if k!='fingerprint'})
        or len(value['rows'])>16 or len(json.dumps(value['bodies'],ensure_ascii=False))>2048):
        raise ValueError('pending conversation snapshot differs')
    if [_versioned_body(app,row,readers,value['version']) for row in value['rows']]!=value['bodies']:raise ValueError('pending conversation sources differ')
    return value

def text(context):
    if context['version']==VERSION:
        return '\n\n【同一回应及其后已收到的公开话语】\n以下是已收到、尚待单独结算的现场话语。父级互动对象记录这轮回应承接的互动关系；具体话语指向结合原文理解。本次读者表示谁收到这份材料。\n'+json.dumps(context['bodies'],ensure_ascii=False)
    if context['version']==SIBLING_VERSION:
        return '\n\n【同一回应及其后已收到的公开话语】\n以下为本角色已经收到、尚待单独结算的话语，包括同一回应中已说出的台词与之后的现场发言；说话者、交谈对象和原发生时间分别标注：\n'+json.dumps(context['bodies'],ensure_ascii=False)
    if context['version'] in (OCCURRENCE_VERSION,PARTICIPANTS_VERSION):
        return '\n\n【原事件之后已收到的公开话语】\n以下原话发生在本轮来源之后，尚待本角色单独结算；用于理解已推进的现场，不代表原事件此刻重新发生：\n'+json.dumps(context['bodies'],ensure_ascii=False)
    return '\n\n【准备回应期间新收到的公开话语】\n以下是已经发生的原话，尚待本角色单独结算，供理解当前现场：\n'+json.dumps(context['bodies'],ensure_ascii=False)

def augment(delivery,context,budget):
    out=thaw_model_data(delivery);out['user']+=text(context)
    if len(out['system'])+len(out['user'])>budget:raise ValueError('pending conversation exceeds Writer budget')
    out['pending_conversation_fingerprint']=context['fingerprint']
    out['prompt_fingerprint']=hashlib.sha256((out['system']+'\0'+out['user']).encode()).hexdigest()
    out['fingerprint']=fp({k:v for k,v in out.items() if k!='fingerprint'})
    return out
