"""Package-owned batch subtype; physical components remain local evidence."""
from collections.abc import Mapping
import json
from framework.model_api import thaw_model_data,model_data_fingerprint
from .events import Interaction,Content
VERSION='petlab-mouse-batch-v5'

def live_gesture_id(contents):
    """Presentation lifetime of a validated ongoing batch, not its event identity."""
    for content in contents:
        p=content.parameters
        if p.get('capture_version')==VERSION and p.get('phase')=='ongoing':
            return p['parts'][-1]['parameters']['gesture_id']
    return None

def _facts(parts):
    from .interaction_delivery import project_parameters
    if not isinstance(parts,(list,tuple)) or not parts:raise ValueError('batch requires captured parts')
    rows=[];previous=None;seen=set();by_gesture={};targets={};definitions={};active=0
    for raw in parts:
        c=Content.from_record(raw);p=c.parameters
        if c.kind!='interaction' or p.get('capture_version')!='petlab-mouse-observation-v3' or p.get('phase') not in {'ended','ongoing'}:raise ValueError('batch component is not a physical interval')
        project_parameters(p)
        if c.content_id in seen:raise ValueError('duplicate batch component')
        seen.add(c.content_id);a,b=p['trajectory'][0],p['trajectory'][-1]
        if previous is not None and a[2]<previous:raise ValueError('batch components overlap or move backwards')
        previous=b[2];active+=p['interval_duration_ms'];g=p['gesture_id']
        item=by_gesture.setdefault(g,{'drag':False,'ended':False,'first_sequence':p['sequence']});item['drag']|=p['gesture']=='drag';item['ended']|=p['phase']=='ended'
        if p.get('body_target'):
            t=p['body_target'];targets[t['region_id']]={'名称':t['label'],'含义':t['description']}
        definition=p['gesture_definition'];definitions[definition['semantic_key']]=definition['description']
        rows.append(p)
    return rows,{'gestures':len(by_gesture),'drags':sum(v['drag'] for v in by_gesture.values()),'clicks':sum(v['ended'] and not v['drag'] for v in by_gesture.values()),'new_drags':sum(v['drag'] and v['first_sequence']==1 for v in by_gesture.values()),'new_clicks':sum(v['ended'] and not v['drag'] and v['first_sequence']==1 for v in by_gesture.values()),'continuations':sum(v['first_sequence']>1 for v in by_gesture.values()),'active_ms':active,'targets':list(targets.values()),'definitions':list(definitions.values())}

def build_batch(*,batch_id,parts,recipient_ref,reason,sealed_at_ms,window_origin_ms,planned_tick_at_ms,advice_fingerprint,quiet_ms):
    if reason not in {'quiet','tick','explicit','recovery'}:raise ValueError('batch seal reason invalid')
    rows,summary=_facts(parts);first=rows[0]['trajectory'][0];last=rows[-1]['trajectory'][-1]
    if not isinstance(batch_id,str) or not batch_id.startswith('batch:'):raise ValueError('batch ID invalid')
    if type(sealed_at_ms) is not int or sealed_at_ms<last[2] or type(window_origin_ms) is not int or window_origin_ms>first[2]:raise ValueError('batch seal clock differs')
    p={'capture_version':VERSION,'batch_id':batch_id,'gesture_id':'gesture:'+batch_id,'sequence':1,'phase':rows[-1]['phase'],
       'gesture':'drag' if summary['drags'] else 'press_release','button':'left','coordinate_system':'screen_pixels',
       'trajectory':[list(first),list(last)],'interval_duration_ms':last[2]-first[2],'total_duration_ms':last[2]-first[2],
       'parts':[thaw_model_data(x) for x in parts],'summary':summary,'reason':reason,'sealed_at_ms':sealed_at_ms,
       'window_origin_ms':window_origin_ms,'planned_tick_at_ms':planned_tick_at_ms,'advice_fingerprint':advice_fingerprint,'quiet_ms':quiet_ms}
    return Interaction(content_id=batch_id+':input',description='用户鼠标互动批次；逐次物理事实已保留，整体量测一次。',recipient_refs=(recipient_ref,),semantic_key='mouse.batch',parameters=p)

def validate_batch(parameters):
    p=thaw_model_data(parameters)
    if p.get('capture_version')!=VERSION:raise ValueError('batch version differs')
    rows,summary=_facts(p['parts']);first=rows[0]['trajectory'][0];last=rows[-1]['trajectory'][-1]
    if (p.get('summary')!=summary or p.get('trajectory')!=[list(first),list(last)] or p.get('sequence')!=1
        or p.get('gesture_id')!='gesture:'+p['batch_id'] or p.get('interval_duration_ms')!=last[2]-first[2]
        or p.get('total_duration_ms')!=last[2]-first[2] or p.get('phase')!=rows[-1]['phase']
        or p.get('gesture')!=('drag' if summary['drags'] else 'press_release')):raise ValueError('batch differs from physical components')
    if p.get('reason') not in {'quiet','tick','explicit','recovery'} or type(p.get('sealed_at_ms')) is not int or p['sealed_at_ms']<last[2]:raise ValueError('batch boundary differs')
    if p['reason']=='tick' and p['sealed_at_ms']<p['planned_tick_at_ms']:raise ValueError('batch Tick is early')
    if type(p.get('quiet_ms')) is not int or p['quiet_ms']<0:raise ValueError('batch quiet window invalid')
    if p['reason']=='quiet' and (p['phase']!='ended' or p['sealed_at_ms']<last[2]+p['quiet_ms']):raise ValueError('batch quiet deadline is early')
    return p

def summarize_batch(content):
    p=validate_batch(content.parameters);s=p['summary'];parts=[]
    if s['new_clicks']:parts.append(f"用户点按了{s['new_clicks']}次。")
    if s['new_drags']:parts.append(f"用户提起并拖动了{s['new_drags']}次。")
    if s['continuations']:parts.append('本段延续此前已经开始的拖拽或按住操作。')
    if not s['clicks'] and not s['drags']:parts.append('用户保持接触。')
    parts.append('截至本段末尾仍按住角色。' if p['phase']=='ongoing' else '本批最后一次接触已经松开。')
    parts.append(f"本批接触累计{s['active_ms']/1000:g}秒，包含{s['gestures']}次手势；只量测本批新增互动。")
    # Direction/pause are derived per physical interval, never from a jump between two separate gestures.
    pp=[x['parameters'] for x in p['parts']]
    if any(x['geometry_summary']['axis_reversals'][0] for x in pp):parts.append('拖动采样中有横向往返。')
    if any(x['geometry_summary']['axis_reversals'][1] for x in pp):parts.append('拖动采样中有纵向往返。')
    if any(x['geometry_summary']['sampled_pause_ms'] for x in pp):parts.append('接触采样中有停顿。')
    result={'交互摘要':''.join(parts),'操作含义':s['definitions'],'起始部位':s['targets'],
            '事实边界':'各次手势的抬起、移动与松开分别保留；手势之间的鼠标移动不是角色被拖动，动画不是心情证据。'}
    return bound_summary(result)


MAX_SUMMARY_CHARS=1200


def _wire_size(value):
    return len(json.dumps(value,ensure_ascii=False,allow_nan=False))


def _bounded_items(items,budget):
    """Whole definitions first, then a labelled excerpt if no whole item fits.

    Order is the source summary's first-occurrence order, not a relevance score.
    The full source remains in the event; this is only a delivery budget.
    """
    selected=[]
    for item in items:
        if _wire_size(selected+[item])>budget:break
        selected.append(item)
    excerpted=0
    if items and not selected:
        item=items[0]
        def excerpt(n):
            if isinstance(item,str):return item[:n]+'…（节略）'
            return {k:v[:n]+'…（节略）' if len(v)>n else v for k,v in item.items()}
        low,high=0,max([len(item)] if isinstance(item,str) else [len(v) for v in item.values()])
        while low<high:
            mid=(low+high+1)//2
            if _wire_size([excerpt(mid)])<=budget:low=mid
            else:high=mid-1
        if _wire_size([excerpt(low)])<=budget:
            selected=[excerpt(low)];excerpted=1
    return selected,{'未展开条数':len(items)-len(selected),'节略条数':excerpted}


def bound_summary(result):
    """Fuse display metadata under the existing wire cap, never cut JSON."""
    if _wire_size(result)<=MAX_SUMMARY_CHARS:return result
    bounded={**result,'操作含义':[],'起始部位':[],
        '摘要收敛':{'版本':'bounded-mouse-summary-v1','方式':'按原列表顺序保留；全文在本地事件',
            '操作含义':{'未展开条数':len(result['操作含义']),'节略条数':1},
            '起始部位':{'未展开条数':len(result['起始部位']),'节略条数':1}}}
    remaining=MAX_SUMMARY_CHARS-_wire_size(bounded)
    for key,budget in [('操作含义',remaining//2),('起始部位',remaining-remaining//2)]:
        bounded[key],bounded['摘要收敛'][key]=_bounded_items(result[key],budget)
    if _wire_size(bounded)>MAX_SUMMARY_CHARS:
        raise ValueError('fixed mouse facts exceed summary budget')
    return bounded
