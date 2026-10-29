"""Read-only presentation of the exact formal recall, through existing cards."""
from model_packages.desktop_pet_v1.writer import content_attributes
from framework.model_api import model_data_fingerprint, thaw_model_data
from model_packages.cx_six_seven_v1.realizer_packet import WriterCard
from model_packages.cx_six_seven_v1.memory_link_delivery import with_memory_links
from ops.memory_link_history_source import history_memory_link_subcard
from model_packages.desktop_pet_v1.interaction_delivery import has_observations

BOUNDED_VERSION="desktop-pet-memory-observation-display-v2"
NATURAL_VERSION="desktop-pet-memory-interaction-text-display-v3"


def memory_content_attributes(contents,*,bounded=True,natural=False):
    return content_attributes(contents,compact=bounded and has_observations(contents),natural=natural)


def memory_cards(memories, *, recall, read, history, owner_ref, numbered=False, links=False,bounded=True,natural=False,content_view_version=None,annotation_profile=None,application=None):
    if not isinstance(numbered,bool) or not isinstance(links,bool) or (links and not numbered):
        raise ValueError("memory links require explicitly numbered memory")
    if not memories:return ()
    ordered=tuple(sorted(memories,key=lambda m:m.fact.accepted_runtime_ordinal))
    selected=tuple(m.event.event_id for m in ordered)
    if len(selected)!=len(set(selected)) or set(selected)!=set(recall.recalled_fact_ids):
        raise ValueError("memory display differs from exact recall")
    rows=[];refs=[]
    for index,memory in enumerate(ordered,1):
        event=memory.event
        contents=event.visible_contents(owner_ref)
        if not contents:raise ValueError("memory has no owner-visible content")
        attrs=memory_content_attributes(contents,bounded=bounded,natural=natural)
        from model_packages.desktop_pet_v1.content_view import content_view
        text,attrs=content_view(contents,attrs,version=content_view_version)
        rows.append({"记忆标记":str(index),"说话者":event.actor_ref,"对象":list(event.target_refs),
            "位置":{"domain":event.position.binding.domain_id,"coordinate":event.position.coordinate},
            "推进序号":memory.fact.accepted_runtime_ordinal,
            "正文":text,"内容属性":attrs})
        if application is not None:
            from petlab_app.visible_delivery import presentation_fields
            rows[-1]=presentation_fields(application,event,rows[-1])
        from model_packages.desktop_pet_v1.content_view import DECLARED_MEMORY_VERSION
        if content_view_version==DECLARED_MEMORY_VERSION:
            from model_packages.desktop_pet_v1.declaration_sources import for_source
            annotation=for_source({'_declaration_key':(memory.fact.session_binding_fingerprint,memory.fact.fingerprint)})
            if annotation is not None:
                if annotation['event_id']!=event.event_id or annotation['event_fingerprint']!=event.fingerprint:
                    raise ValueError('memory subtype differs from accepted source')
                rows[-1]['记忆子类']={'类型':'用户提出的持续讲话约定','适用范围':'该角色对该用户的讲话'}
        from model_packages.desktop_pet_v1.content_view import HIERARCHY_MEMORY_VERSION
        if content_view_version==HIERARCHY_MEMORY_VERSION:
            from model_packages.desktop_pet_v1.hierarchy_sources import for_source as property_source
            annotation=property_source({'_declaration_key':(memory.fact.session_binding_fingerprint,memory.fact.fingerprint)})
            if annotation is not None:
                if annotation['event_id']!=event.event_id or annotation['event_fingerprint']!=event.fingerprint:
                    raise ValueError('hierarchical memory differs from accepted source')
                rows[-1]['语义属性']=[{k:thaw_model_data(v) for k,v in row.items() if k!='span'} for row in annotation['properties']]
        from model_packages.desktop_pet_v1.content_view import RELATION_MEMORY_VERSION,QUERY_MEMORY_VERSION
        if content_view_version in (RELATION_MEMORY_VERSION,QUERY_MEMORY_VERSION):
            from .relation_annotations import for_memory,FIELD
            annotation=for_memory(annotation_profile,memory,owner_ref)
            if annotation is not None:rows[-1][FIELD]=annotation
        refs.extend((memory.fact.fingerprint,recall.fingerprint,read.fingerprint))
    observed=bounded and any(has_observations(m.event.visible_contents(owner_ref)) for m in ordered)
    provenance={"presentation_version":content_view_version if content_view_version==HIERARCHY_MEMORY_VERSION else DECLARED_MEMORY_VERSION if content_view_version==DECLARED_MEMORY_VERSION else (NATURAL_VERSION if natural else BOUNDED_VERSION) if observed else "desktop-pet-numbered-memory-v1",
        "selected_memory_ids":selected,"fields_fingerprint":model_data_fingerprint(rows),
        "source_records":[{"event":m.event.to_record(),"fact_fingerprint":m.fact.fingerprint} for m in ordered]}
    if not numbered:
        return tuple(WriterCard(card_id="memory:"+m.event.event_id,lane="memory",kind="recalled_pet_event",
            source_id=m.event.event_id,fields=row,source_receipt_refs=(m.fact.fingerprint,recall.fingerprint,read.fingerprint),
            provenance={"selected_memory_ids":[m.event.event_id],**({"presentation_version":NATURAL_VERSION if natural else BOUNDED_VERSION}
                if bounded and has_observations(m.event.visible_contents(owner_ref)) else {})},budget_policy="preserve_selection")
            for m,row in zip(ordered,rows))
    parent=WriterCard(card_id="memory:numbered:"+model_data_fingerprint(provenance),lane="memory",
        kind="numbered_pet_memory",source_id="desktop-pet-numbered-memory-v1",
        fields={"相关记忆（按发生顺序）":rows},provenance=provenance,
        source_receipt_refs=tuple(dict.fromkeys(refs)),budget_policy="preserve_selection")
    if not links:return (parent,)
    subcard=history_memory_link_subcard(history=history,history_read=read,parent_card_id=parent.card_id,
        selected_memory_ids=selected,enabled=True)
    return (with_memory_links(parent,subcard),)


def memory_aliases(packet, delivered_card_ids):
    aliases={}
    for card in packet.sections["memory"]:
        if card.card_id not in delivered_card_ids:continue
        ids=tuple(card.provenance["selected_memory_ids"])
        rows=card.fields.get("相关记忆（按发生顺序）",(card.fields,))
        if len(rows)!=len(ids):raise ValueError("memory aliases differ from source mapping")
        for row,event_id in zip(rows,ids):
            alias=row["记忆标记"]
            if alias in aliases:raise ValueError("duplicate memory marker")
            aliases[alias]=event_id
    return aliases


def ordered_memory_union(cards, *, memories, source_refs, assembly_version):
    """Order already selected, authorized records; never rescore or trim them."""
    cards=tuple(cards)
    if not cards:return ()
    rows={};markers=set()
    for card in cards:
        ids=tuple(card.provenance['selected_memory_ids'])
        records=card.fields.get('相关记忆（按发生顺序）',(card.fields,))
        if len(ids)!=len(records):raise ValueError('memory union source map differs')
        if any(child.kind!='memory_link_subcard' for child in card.subcards):
            raise ValueError('memory union cannot discard an unknown child')
        for identity,row in zip(ids,records):
            marker=row['记忆标记']
            if identity in rows or identity not in memories or marker in markers:
                raise ValueError('memory union has duplicate or unauthorized sources')
            rows[identity]=thaw_model_data(row);markers.add(marker)
    selected=tuple(sorted(rows,key=lambda i:memories[i].fact.accepted_runtime_ordinal))
    body=[rows[i] for i in selected]
    provenance={'presentation_version':assembly_version,'selected_memory_ids':selected,
        'fields_fingerprint':model_data_fingerprint(body),
        'source_cards':[{'card_id':c.card_id,'provenance_fingerprint':model_data_fingerprint(c.provenance),
                         'fields_fingerprint':model_data_fingerprint(c.fields)} for c in cards]}
    return (WriterCard(card_id='memory:ordered-union:'+model_data_fingerprint(provenance),
        lane='memory',kind='numbered_pet_memory',source_id=assembly_version,
        fields={'相关记忆（按发生顺序）':body},provenance=provenance,
        source_receipt_refs=tuple(dict.fromkeys(ref for c in cards for ref in c.source_receipt_refs))+tuple(source_refs),
        budget_policy='preserve_selection'),)
