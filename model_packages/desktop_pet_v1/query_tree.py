"""One source-bound query parent owns its references; canonical recall stays v2."""
import json
from pathlib import Path
from .grounded_recall import VERSION as RECALL_VERSION,parse_cues
from .reference_window import USED_SCOPE,parse_response as parse_references
from .semantic import evidence_source

VERSION='desktop-pet-memory-query-tree-v1'
SUFFIX=':input-query-tree-v1'

class TreeQuerySemanticCodec:
    memory_recall_version=RECALL_VERSION
    reference_source_scope=USED_SCOPE
    def __init__(self,base):
        self.base=base;self.request_version=base.request_version+SUFFIX
    def __getattr__(self,name):return getattr(self.base,name)
    def build_request(self,*,event,recall_hint):
        system,user=self.base.build_request(event=event,recall_hint='')
        system+='\n'+Path(__file__).with_name('resources').joinpath('semantic/query-tree-v1.md').read_text(encoding='utf8').strip()
        user+='\n\n【近期用户话语：指代参照】\n'+(recall_hint or '（当前没有可用的此前用户话语）')
        return system,user

def parse_response(raw,event,context=None):
    from model_packages.cx_six_seven_v1.semantic_measurement import _strip_fences
    data=json.loads(_strip_fences(raw))
    if not isinstance(data,dict) or 'memory_cues' in data or 'reference_cues' in data:
        raise ValueError('query tree cannot mix legacy parallel cue fields')
    nodes=data.get('memory_query_nodes')
    if not isinstance(nodes,list) or len(nodes)>16:raise ValueError('query nodes exceed existing cue budget')
    literal=[];refs=[];parents=[];seen={}
    for node in nodes:
        if not isinstance(node,dict) or not {'kind','text','evidence_text'}<=set(node) or set(node)-{'kind','text','evidence_text','references'}:
            raise ValueError('query parent schema invalid')
        kind=node['kind'];children=node.get('references',[])
        if kind not in ('literal','query','unresolved') or not isinstance(children,list):raise ValueError('query parent kind invalid')
        if kind!='query' and children:raise ValueError('only query parents own reference children')
        term,quote=node['text'],node['evidence_text']
        if not isinstance(term,str) or not isinstance(quote,str):raise ValueError('query parent evidence invalid')
        key=(term,quote)
        if key in seen:raise ValueError('duplicate or conflicting query parent')
        seen[key]=kind
        item={'text':term,'evidence_text':quote,'cue_kind':'reference_slot' if kind=='query' else 'explicit_topic'}
        literal.append(item);parents.append((kind,children))
        for child in children:
            if not isinstance(child,dict) or not {'text','reference_text'}<=set(child) or set(child)-{'reference_id','text','reference_text'}:
                raise ValueError('query reference schema invalid')
            refs.append({**child,'evidence_text':quote})
        if len(literal)+len(refs)>16:raise ValueError('query nodes exceed existing cue budget')
    virtual=json.dumps({'memory_cues':literal,'reference_cues':refs},ensure_ascii=False)
    result=parse_cues(virtual,event)
    if result['rejected_cue_count'] or len(result['cues'])!=len(nodes):raise ValueError('query parent lacks unique current evidence')
    if context is not None and context.get('status')=='delivered':
        result.update(parse_references(virtual,event,context,available=16-len(literal),source_scope=USED_SCOPE))
    elif refs:
        # Missing source context preserves the parent, never invents a binding.
        result['rejected_reference_cue_count']=len(refs)
    accepted=result.get('reference_cues',[]);source_text={x['reference_id']:x['text'] for x in (context or {}).get('sources',[])}
    compact=[]
    for cue,(kind,children) in zip(result['cues'],parents):
        indices=[]
        for i,row in enumerate(accepted):
            if row['span']!=cue['span']:continue
            start,end=row['reference_span'];prior=source_text[row['reference_id']][start:end]
            if any(c['text']==row['text'] and c['reference_text']==prior and (c.get('reference_id') is None or str(c['reference_id'])==row['reference_id']) for c in children):indices.append(i)
        compact.append({'kind':kind,'text':cue['text'],'span':cue['span'],'reference_indices':indices,'unresolved_reference_count':max(0,len(children)-len(indices))})
    result.update(query_tree_version=VERSION,query_nodes=compact)
    return result


OUTPUT_SUFFIX=':input-query-tree-v2'

class UnifiedTreeQuerySemanticCodec(TreeQuerySemanticCodec):
    """Declare the query tree in the same output object as numerical readings."""
    def __init__(self,base):
        super().__init__(base)
        self.request_version=base.request_version+OUTPUT_SUFFIX
    def build_request(self,*,event,recall_hint):
        system,user=super().build_request(event=event,recall_hint=recall_hint)
        marker='- reference_frame：'
        if system.count(marker)!=1:raise ValueError('query tree output contract anchor differs')
        field='- memory_query_nodes：必填数组，与上述数值共同放在同一个JSON对象中。每项按下文定义提供kind、text、evidence_text及对应references；没有可定位线索时提供[]。\n'
        return system.replace(marker,field+marker,1),user

GROUNDED_SUFFIX=':input-query-tree-v3-grounded-parent'
GROUNDING_VERSION='desktop-pet-query-parent-grounding-v1'

class GroundedTreeQuerySemanticCodec(UnifiedTreeQuerySemanticCodec):
    """Current wire separates an exact source anchor from an unresolved interpretation."""
    def __init__(self,base):
        super().__init__(base)
        self.request_version=base.request_version+GROUNDED_SUFFIX
    def build_request(self,*,event,recall_hint):
        system,user=super().build_request(event=event,recall_hint=recall_hint)
        old='父项字段为 kind、text、evidence_text；text是当前原文中能独立定位的短语（最多48字符），evidence_text是唯一定位的原文片段（最多256字符）。'
        new='父项字段为 kind、text、evidence_text；evidence_text逐字摘录唯一定位的当前原文片段（最多256字符），text从这段evidence_text逐字摘取短语（最多48字符）。原文中的人称与代词保留在证据锚中；能由来源确定的对象或值写在references子项。只有解释、尚无来源支持的对象保留在unresolved父层，references为空。'
        if system.count(old)!=1:raise ValueError('query grounding contract anchor differs')
        return system.replace(old,new,1),user


def parse_grounded_response(raw,event,context=None):
    """Keep uniquely grounded parent evidence without treating a paraphrase as a fact.

    Only a childless query can fall back to its entire, already bounded quote.
    Old parsing still verifies schema, unique spans, budgets and all references.
    Numerical readings and the original response remain untouched.
    """
    from model_packages.cx_six_seven_v1.semantic_measurement import _strip_fences
    data=json.loads(_strip_fences(raw));text=evidence_source(event);repairs=[]
    nodes=data.get('memory_query_nodes') if isinstance(data,dict) else None
    if isinstance(nodes,list):
        for index,node in enumerate(nodes):
            if not isinstance(node,dict):continue
            term=node.get('text');quote=node.get('evidence_text')
            if (node.get('kind')=='query' and node.get('references',[])==[]
                and isinstance(term,str) and term.strip() and len(term)<=48
                and isinstance(quote,str) and quote.strip() and len(quote)<=48
                and term not in quote and text.count(quote)==1):
                repairs.append({'node_index':index,'model_text':term,'grounded_text':quote,
                    'reason':'unanchored_interpretation_retained_as_unresolved_parent'})
                nodes[index]={**node,'text':quote,'kind':'unresolved'}
    rejected=[]
    if isinstance(nodes,list) and len(nodes)<=16 and isinstance(context,dict) and context.get('status')=='delivered':
        # Prior-source text is not a current-event anchor. Preserve the rejected
        # model claim separately, never turn it into a current fact or recall cue.
        budget=sum(1+len(n.get('references',[])) for n in nodes if isinstance(n,dict) and isinstance(n.get('references',[]),list))
        if budget>16:raise ValueError('query nodes exceed existing cue budget')
        kept=[]
        for index,node in enumerate(nodes):
            quote=node.get('evidence_text') if isinstance(node,dict) else None
            term=node.get('text') if isinstance(node,dict) else None
            sources=[str(r['reference_id']) for r in context.get('sources',[]) if isinstance(quote,str) and quote and isinstance(r.get('text'),str) and r['text'].count(quote)==1]
            if (isinstance(node,dict) and {'kind','text','evidence_text'}<=set(node)
                and not set(node)-{'kind','text','evidence_text','references'}
                and node['kind'] in ('literal','query','unresolved') and node.get('references',[])==[]
                and isinstance(term,str) and term.strip() and len(term)<=48
                and isinstance(quote,str) and quote.strip() and len(quote)<=256 and term in quote
                and quote not in text and sources):
                rejected.append({'node_index':index,'node':node,'source_reference_ids':sources,'reason':'prior_source_is_not_current_event_evidence'})
            else:kept.append(node)
        data['memory_query_nodes']=kept
    result=parse_response(json.dumps(data,ensure_ascii=False),event,context)
    if rejected:result.update(query_source_validation_version='petlab-query-source-validation-v1',rejected_query_nodes=rejected)
    if repairs:result.update(query_grounding_version=GROUNDING_VERSION,query_grounding_repairs=repairs)
    return result


PARENT_SUFFIX=':query-parent-fallback-v1'
PARENT_VERSION='desktop-pet-query-parent-resolution-v1'


class ResilientTreeQueryCodec:
    """Version local interpretation; the model's request text stays identical."""
    def __init__(self,base):
        self.base=base;self.request_version=base.request_version+PARENT_SUFFIX
    def __getattr__(self,name):return getattr(self.base,name)
    def build_request(self,*,event,recall_hint):
        return self.base.build_request(event=event,recall_hint=recall_hint)


def accepts_received_contract(previous,current):
    """Only the same query request may gain this local parser revision."""
    return (isinstance(previous,str) and isinstance(current,str)
        and GROUNDED_SUFFIX in previous and current==previous+PARENT_SUFFIX)


def parse_resilient_response(raw,event,context=None,visible_context=None):
    """Ground optional nodes independently, then reuse the original source parser.

    A model paraphrase never becomes evidence. The complete short source quote
    can remain as a parent. Children still need the delivered, verified source
    window; absent/invalid children do not invalidate an otherwise real input.
    """
    from framework.model_api import model_data_fingerprint as fp
    from model_packages.cx_six_seven_v1.semantic_measurement import _strip_fences
    from .visible_context import parse_visible_grounded_response
    data=json.loads(_strip_fences(raw))
    if not isinstance(data,dict) or 'memory_cues' in data or 'reference_cues' in data:
        raise ValueError('query tree cannot mix legacy parallel cue fields')
    nodes=data.get('memory_query_nodes')
    if not isinstance(nodes,list) or len(nodes)>16:raise ValueError('query nodes exceed existing cue budget')
    count=sum(1+(len(n.get('references',[])) if isinstance(n,dict) and isinstance(n.get('references',[]),list) else 0) for n in nodes)
    if count>16:raise ValueError('query nodes exceed existing cue budget')
    audit={'version':PARENT_VERSION,'wire_content_fingerprint':fp(data),'original_item_count':count,
        'parents':[],'merged_parents':[],'rejected_parents':[],'rejected_children':[]}
    text=evidence_source(event);groups=[];by_key={}
    priority={'unresolved':0,'query':1,'literal':2}
    def reject_child(parent,child,reason):
        audit['rejected_children'].append({'node_index':parent,'child_index':child,'reason':reason})
    for index,node in enumerate(nodes):
        reason=None
        if (not isinstance(node,dict) or not {'kind','text','evidence_text'}<=set(node)
            or set(node)-{'kind','text','evidence_text','references'}):reason='invalid_optional_parent_shape'
        elif node['kind'] not in ('literal','query','unresolved'):reason='unknown_parent_kind'
        else:
            quote=node['evidence_text'];term=node['text']
            if not isinstance(quote,str) or not quote.strip() or len(quote)>256 or text.count(quote)!=1:
                reason='no_unique_current_source'
        if reason:
            audit['rejected_parents'].append({'node_index':index,'reason':reason});continue
        anchored=isinstance(term,str) and bool(term.strip()) and len(term)<=48 and term in quote
        if not anchored:
            if len(quote)>48:
                audit['rejected_parents'].append({'node_index':index,'reason':'no_bounded_exact_parent'});continue
            term=quote
        kind=node['kind'] if anchored or node['kind']=='query' else 'unresolved'
        key=(term,quote)
        if key not in by_key:
            by_key[key]=len(groups)
            groups.append({'kind':kind,'text':term,'evidence_text':quote,'children':[],'origins':[]})
        else:
            audit['merged_parents'].append({'node_index':index,'parent_index':by_key[key]})
        group=groups[by_key[key]];group['origins'].append(index)
        if priority[kind]>priority[group['kind']]:group['kind']=kind
        audit['parents'].append({'node_index':index,'parent_index':by_key[key],
            'status':'exact_parent' if anchored else 'source_quote_parent'})
        children=node.get('references',[])
        if not isinstance(children,list):
            reject_child(index,None,'invalid_child_list');continue
        for child_index,child in enumerate(children):
            if node['kind']!='query':reject_child(index,child_index,'non_query_parent');continue
            if (not isinstance(child,dict) or not {'text','reference_text'}<=set(child)
                or set(child)-{'reference_id','text','reference_text'}
                or not isinstance(child['text'],str) or not child['text'].strip() or len(child['text'])>48
                or not isinstance(child['reference_text'],str) or not 0<len(child['reference_text'])<=256
                or child['text'] not in child['reference_text']
                or ('reference_id' in child and not isinstance(child['reference_id'],str))):
                reject_child(index,child_index,'invalid_child_evidence');continue
            group['children'].append((index,child_index,child))
    canonical=[]
    for group in groups:
        children=[]
        for index,child_index,child in group['children']:
            if group['kind']!='query':reject_child(index,child_index,'explicit_parent_conflict')
            elif child not in children:children.append(child)
        canonical.append({k:group[k] for k in ('kind','text','evidence_text')})
        canonical[-1]['references']=children
    # This still validates the actual event, context fingerprint, source spans,
    # scope and typed reference slots; optional normalization grants no source.
    result=parse_visible_grounded_response(json.dumps({**data,'memory_query_nodes':canonical},ensure_ascii=False),event,context,visible_context)
    accepted=result.get('reference_cues',[])
    source_text={x['reference_id']:x['text'] for x in (context or {}).get('sources',[])}
    for parent_index,group in enumerate(groups):
        parent=result['query_nodes'][parent_index]
        if group['kind']=='query':
            for index,child_index,child in group['children']:
                matches=[]
                for ref_index in parent['reference_indices']:
                    row=accepted[ref_index];a,b=row['reference_span']
                    if (row['text']==child['text'] and source_text[row['reference_id']][a:b]==child['reference_text']
                        and (child.get('reference_id') is None or child['reference_id']==row['reference_id'])):matches.append(row)
                if not matches:reject_child(index,child_index,'unbound_source_child')
        parent['unresolved_reference_count']=sum(r['node_index'] in group['origins'] for r in audit['rejected_children'])
    result['rejected_cue_count']=len(audit['rejected_parents'])
    result['rejected_reference_cue_count']=len(audit['rejected_children'])
    # Preserve existing source-rejection provenance after the original context
    # validators succeeded. These are diagnostics, never new current cues.
    prior_rows=json.loads(visible_context['text']) if visible_context and visible_context.get('status')=='delivered' else []
    for rejection in audit['rejected_parents']:
        index=rejection['node_index'];node=nodes[index]
        if not isinstance(node,dict):continue
        quote=node.get('evidence_text');term=node.get('text')
        if (not isinstance(quote,str) or not quote or len(quote)>256 or quote in text
            or not isinstance(term,str) or not term.strip() or len(term)>48
            or node.get('references',[])!=[]):continue
        prior_ids=[x['reference_id'] for x in (context or {}).get('sources',[]) if context.get('status')=='delivered' and isinstance(x.get('text'),str) and x['text'].count(quote)==1]
        seen=[visible_context['sources'][i]['fact_fingerprint'] for i,row in enumerate(prior_rows) if row['原文'].count(quote)==1]
        if seen:
            rejection['source_fact_fingerprints']=seen
            result['query_visible_source_validation_version']='petlab-visible-query-source-validation-v1'
            result.setdefault('rejected_query_nodes',[]).append({'node_index':index,'node':node,'source_fact_fingerprints':seen,'reason':'prior_visible_source_is_not_current_event_evidence'})
        elif prior_ids:
            rejection['source_reference_ids']=prior_ids
            result['query_source_validation_version']='petlab-query-source-validation-v1'
            result.setdefault('rejected_query_nodes',[]).append({'node_index':index,'node':node,'source_reference_ids':prior_ids,'reason':'prior_source_is_not_current_event_evidence'})
    result['query_parent_resolution']=audit
    return result
