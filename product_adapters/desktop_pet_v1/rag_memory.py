"""Exact-ID, content-only local RAG beside the already-settled numeric lane."""
from pathlib import Path
from types import SimpleNamespace
import hashlib,json,time
from functools import lru_cache
from framework.model_api import model_data_fingerprint,thaw_model_data
from model_packages.desktop_pet_v1.events import PetEvent,MemoryInstance
from model_packages.desktop_pet_v1.rag_policy import POLICY,VERSION
from model_packages.cx_six_seven_v1.realizer_packet import WriterCard
from ops.memory_contracts import AddressReceipt
from ops.memory_actual_join import ActualRecallCanonicalItem
from . import EVENT_JOURNAL_KEY
from .memory_delivery import memory_content_attributes
from .relation_annotations import for_memory,FIELD

CONTEXT_VERSION='desktop-pet-local-rag-exchange-content-context-v2'
RETRIEVAL_TEXT_VERSION='desktop-pet-literal-query-address-filter-v1'

def _literal_query(event):
    return '\n'.join(c.description for c in event.visible_contents(event.owner_ref) if c.kind=='speech')

def _signed(record):return {**record,'fingerprint':model_data_fingerprint(record)}

@lru_cache(maxsize=32768)
def _document_digest(identity,content):
    # The value is a pure digest, not a validation/authorization certificate.
    return model_data_fingerprint({'id':identity,'content':content}).encode('ascii')

def _snapshot(documents):
    h=hashlib.sha256()
    for row in documents:
        identity=row['event_id'];content=row['content_fingerprint']
        digest=(_document_digest(identity,content) if type(identity) is str and type(content) is str else
                model_data_fingerprint({'id':identity,'content':content}).encode('ascii'))
        h.update(digest)
    return h.hexdigest()

ASSEMBLY_VERSION='desktop-pet-authorized-memory-order-v3'
from .rag_passages import VERSION as DOCUMENT_VERSION,project as passage_documents

class RagMemoryService:
    def __init__(self,*,application,provider,trace_dir,reply_source_key=None):
        self.application=application;self.provider=provider;self.trace_dir=Path(trace_dir)
        self._sources={};self._exchanges={};self._reply_keys={};self._prefetch=None;self.reply_source_key=reply_source_key;self._address_cache={};self._passages={}
    def _source(self,event_id):return self.application.runtime.memory_instance(event_id)
    def _documents(self,source,history,*,document_version=DOCUMENT_VERSION):
        return self._documents_for(event=source.event,before_ordinal=source.fact.accepted_runtime_ordinal,history=history,document_version=document_version)
    def _documents_for(self,*,event,before_ordinal,history,document_version=DOCUMENT_VERSION):
        query_scope=event.scope_ref
        app=self.application;docs=[];memories={};journal=app.runtime.session.profile.get(EVENT_JOURNAL_KEY,{})
        for fact in history.facts():
            if fact.accepted_runtime_ordinal>=before_ordinal:continue
            key=fact.fingerprint
            if key not in self._sources:
                raw=journal.get(fact.fact_id)
                if raw is None:raise ValueError('RAG source lacks committed event journal')
                event=PetEvent.from_record(raw['event']);memory=MemoryInstance(event,fact)
                visible=event.visible_contents(app.owner_ref)
                from model_packages.desktop_pet_v1.web_observation import is_content
                speech=[c.description for c in visible if c.kind=='speech' or is_content(c)]
                if (event.owner_ref!=app.owner_ref or not speech):self._sources[key]=None
                else:
                    text=('外部网页观察：' if any(is_content(c) for c in visible) else '用户说：' if event.actor_ref==app.user_ref else '角色说：')+'\n'.join(speech)
                    fingerprint=model_data_fingerprint({'event':event.fingerprint,'fact':fact.fingerprint,'text':text})
                    self._sources[key]=({'event_id':event.event_id,'content_fingerprint':fingerprint,'text':text},memory)
            row=self._sources[key]
            if row is not None:
                doc,memory=row
                if memory.event.scope_ref==query_scope:docs.append(doc);memories[doc['event_id']]=memory
        # The original RAG adapter addresses a committed exchange. Preserve both
        # exact events instead of indexing a question and then dropping its answer.
        by_id={d['event_id']:d for d in docs};children={};paired=set()
        anchors={(m.fact.accepted_runtime_ordinal,m.fact.temporal_position_fingerprint):i
                 for i,m in memories.items() if m.event.event_kind=='peer_input'}
        if self.reply_source_key is not None:
            sources=app._product()['sources']
            for i,m in memories.items():
                row=sources.get(i)
                if m.event.event_kind!='npc_output' or row is None or row['origin']!='execution':continue
                if m.fact.fingerprint not in self._reply_keys:self._reply_keys[m.fact.fingerprint]=self.reply_source_key(row)
                parent=anchors.get(self._reply_keys[m.fact.fingerprint])
                if parent is not None:
                    children.setdefault(parent,[]).append(i);paired.add(i)
        documents=[]
        for doc in docs:
            i=doc['event_id']
            if i in paired:continue
            members=[i,*sorted(children.get(i,()),key=lambda k:memories[k].fact.accepted_runtime_ordinal)]
            key=tuple((k,by_id[k]['content_fingerprint']) for k in members)
            if key not in self._exchanges:
                text='\n'.join(by_id[k]['text'] for k in members)
                fingerprint=model_data_fingerprint({'members':list(key),'text':text})
                self._exchanges[key]={'event_id':i,'text':text,'content_fingerprint':fingerprint,'member_event_ids':members}
            documents.append(self._exchanges[key])
        if document_version is None:return documents,memories
        if document_version!=DOCUMENT_VERSION:raise ValueError('unsupported retrieval document version')
        projected=[]
        for doc in documents:
            key=(document_version,doc['event_id'],doc['content_fingerprint'])
            if key not in self._passages:self._passages[key]=tuple(passage_documents([doc],version=document_version))
            projected.extend(self._passages[key])
        return projected,memories
    def warm_sources(self):
        """Decode existing immutable text envelopes at open, without retrieval."""
        app=self.application;history=app.runtime._history();facts=history.facts()
        if not facts:return
        source=self._source(facts[-1].fact_id)
        self._documents_for(event=source.event,
            before_ordinal=source.fact.accepted_runtime_ordinal+1,history=history)

    def _address_documents(self,documents,memories,basis):
        """Apply the shared source address projection before vector inference.

        Old unannotated records remain available to text RAG. Unknown owners
        never become a precise identity. Selection never splits an exchange.
        """
        if 'addresses' not in basis:return documents,None
        from model_packages.semantic_addresses import levels,address_indexes,match_addresses
        from model_packages.desktop_pet_v1.address_recall import addresses
        from model_packages.desktop_pet_v1.hierarchy_semantics import validate
        from .relation_annotations import _measurement
        candidates=[];unlocated=[]
        for doc in documents:
            props=[]
            for identity in doc['member_event_ids']:
                memory=memories[identity];key=memory.fact.fingerprint
                if key not in self._address_cache:
                    parsed=_measurement(self.application.runtime.session.profile,memory).get('pet_memory_recall',{})
                    self._address_cache[key]=(validate(memory.event,parsed) if parsed.get('version') in
                        ('desktop-pet-hierarchical-property-recall-v7','desktop-pet-property-address-recall-v8') else [])
                props.extend(p for p in self._address_cache[key] if p['kind']=='assertion')
            indexes=address_indexes(addresses(props))
            if not indexes or any(a['entity_id'] is None for a in addresses(props)):unlocated.append(doc['event_id'])
            candidates.append({'event_id':doc['event_id'],'indexes':indexes})
        selected,decisions=match_addresses(basis['addresses'],candidates)
        resolved=any(levels(a) for a in basis['addresses'])
        allowed=set(selected)|set(unlocated) if resolved else {d['event_id'] for d in documents}
        qualified=[d for d in documents if d['event_id'] in allowed]
        return qualified,{
            'version':'desktop-pet-shared-address-candidates-v1',
            'decisions':[{k:v for k,v in d.items() if k!='matches'}|{'match_count':len(d['matches'])} for d in decisions],
            'unlocated_source_count':len(unlocated),'unresolved_query':not resolved,
            'selected_document_count':len(qualified),'selected_document_fingerprint':_snapshot(qualified)}

    def _basis(self,source):
        from .relation_annotations import _measurement
        from model_packages.desktop_pet_v1.retrieval_query import query_basis
        measurement=_measurement(self.application.runtime.session.profile,source)
        return query_basis(source.event,measurement.get('pet_memory_recall'))
    def prefetch(self,event,*,measurement=None):
        app=self.application
        if event.event_kind!='peer_input' or not any(c.kind=='speech' for c in event.contents):return
        if event.event_id in app.runtime.session.profile.get(EVENT_JOURNAL_KEY,{}):return
        app._verify_source(event)
        from model_packages.desktop_pet_v1.retrieval_query import query_basis
        basis=query_basis(event,(measurement or {}).get('pet_memory_recall'));text=_literal_query(event)
        if not text.strip():return
        history=app.runtime._history();facts=history.facts()
        boundary=max((f.accepted_runtime_ordinal for f in facts),default=-1)+1
        docs,memories=self._documents_for(event=event,before_ordinal=boundary,history=history)
        docs,_=self._address_documents(docs,memories,basis)
        if not docs:return
        namespace=model_data_fingerprint({'binding':app.runtime.session.session_binding.fingerprint,'owner':app.owner_ref,'scope':event.scope_ref})
        snapshot=_snapshot(docs);query_fingerprint=hashlib.sha256(text.encode('utf8')).hexdigest()
        pending=self._prefetch
        if pending and pending[0]==event.fingerprint and pending[1]==snapshot and pending[4]==query_fingerprint:return
        submit=getattr(self.provider,'submit_latest',self.provider.submit)
        ticket=submit(query=text,documents=tuple(docs),namespace=namespace)
        self._prefetch=(event.fingerprint,snapshot,ticket,time.perf_counter(),query_fingerprint)
    def _recall(self,event_id,history):
        values=[r for r in history.actual_recalls() if r.trigger_fact_id==event_id]
        if len(values)!=1:raise ValueError('RAG requires one actual numeric recall source')
        return values[0]
    def context(self,event_id):
        app=self.application;source=self._source(event_id)
        if source.event.event_kind!='peer_input' or not any(c.kind=='speech' for c in source.event.contents):return None
        basis=self._basis(source);text=_literal_query(source.event)
        if not text.strip():return None
        history=app.runtime._history();recall=self._recall(event_id,history)
        docs,memories=self._documents(source,history)
        docs,address_selection=self._address_documents(docs,memories,basis)
        snapshot=_snapshot(docs)
        key=model_data_fingerprint({'binding':source.fact.session_binding_fingerprint,'source':source.fact.fingerprint,'policy':VERSION,'documents':DOCUMENT_VERSION})
        path=self.trace_dir/(key+'.json')
        if path.exists():
            record=json.loads(path.read_text(encoding='utf8'));self.validate(record,event_id,history=history);return record
        namespace=model_data_fingerprint({'binding':source.fact.session_binding_fingerprint,'owner':app.owner_ref,'scope':source.event.scope_ref})
        start=time.perf_counter()
        pending=self._prefetch;self._prefetch=None
        parallel=bool(pending and pending[0]==source.event.fingerprint and pending[1]==snapshot and pending[4]==hashlib.sha256(text.encode('utf8')).hexdigest())
        result=(self.provider.result(pending[2],timeout_s=.5) if parallel else
                self.provider.retrieve(query=text,documents=tuple(docs),namespace=namespace,timeout_s=.5)) if docs else {
                'status':'available','selected':{},'model':self.provider.model,'policy':dict(POLICY),'candidate_count':0,'cached_vectors':0,'new_vectors':0,'best_similarity':None}

        selected=result.get('selected',{});by_id={r['event_id']:r for r in docs}
        if not isinstance(selected,dict) or not set(selected)<=set(by_id):raise ValueError('local RAG escaped the closed exact-ID set')
        record=_signed({'version':CONTEXT_VERSION,'document_version':DOCUMENT_VERSION,'retrieval_text_version':RETRIEVAL_TEXT_VERSION,'assembly_version':ASSEMBLY_VERSION,'source_event_id':event_id,'source_fact_fingerprint':source.fact.fingerprint,
            'session_binding_fingerprint':source.fact.session_binding_fingerprint,'scope_ref':source.event.scope_ref,
            'query_fingerprint':hashlib.sha256(text.encode('utf8')).hexdigest(),'query_basis':basis,'candidate_snapshot':snapshot,
            **({'address_selection':address_selection} if address_selection is not None else {}),
            'numeric_recall_fingerprint':recall.fingerprint,'result':result,
            'selected_sources':[{'event_id':i,'content_fingerprint':by_id[i]['content_fingerprint'],'member_event_ids':by_id[i]['member_event_ids'],**({'source_slice':by_id[i]['source_slice']} if 'source_slice' in by_id[i] else {})} for i in selected],
            'elapsed_ms':round((time.perf_counter()-start)*1000),'parallel_with_measurement':parallel,'state_commit_authority':False,'send_authority':False})
        self.validate(record,event_id,history=history)
        self.trace_dir.mkdir(parents=True,exist_ok=True)
        with path.open('x',encoding='utf8') as f:json.dump(record,f,ensure_ascii=False,separators=(',',':'),allow_nan=False)
        return record
    def validate(self,context,event_id,*,history=None):
        app=self.application;source=self._source(event_id)
        history=history or app.runtime._history();recall=self._recall(event_id,history)
        docs,memories=self._documents(source,history,document_version=context.get('document_version'))
        if 'address_selection' in context:
            basis=self._basis(source);docs,selection=self._address_documents(docs,memories,basis)
            if thaw_model_data(context['address_selection'])!=selection:raise ValueError('RAG address selection differs from committed sources')
        by_id={r['event_id']:r for r in docs}
        c=thaw_model_data(context)
        from .relation_annotations import _measurement
        if _measurement(app.runtime.session.profile,source).get('pet_memory_recall',{}).get('version')=='desktop-pet-property-address-recall-v8':
            if 'query_basis' not in c or 'address_selection' not in c:raise ValueError('RAG property source contract missing')
        if c.get('version')!=CONTEXT_VERSION or c.get('fingerprint')!=model_data_fingerprint({k:v for k,v in c.items() if k!='fingerprint'}):raise ValueError('local RAG context fingerprint differs')
        text='\n'.join(x.description for x in source.event.visible_contents(app.owner_ref) if x.kind=='speech')
        if 'query_basis' in c:
            basis=self._basis(source)
            if 'addresses' in basis and 'address_selection' not in c:raise ValueError('RAG address selection missing')
            if c['query_basis']!=basis:raise ValueError('RAG query basis differs from committed source')
            text=basis['text']
        if 'retrieval_text_version' in c:
            if c['retrieval_text_version']!=RETRIEVAL_TEXT_VERSION:raise ValueError('unsupported retrieval text version')
            text=_literal_query(source.event)
        if (c['source_event_id']!=event_id or c['source_fact_fingerprint']!=source.fact.fingerprint
            or c['session_binding_fingerprint']!=source.fact.session_binding_fingerprint or c['scope_ref']!=source.event.scope_ref
            or c['numeric_recall_fingerprint']!=recall.fingerprint
            or c['query_fingerprint']!=hashlib.sha256(text.encode('utf8')).hexdigest()
            or c['candidate_snapshot']!=_snapshot(docs)
            or c['state_commit_authority'] is not False or c['send_authority'] is not False):raise ValueError('local RAG context source differs')
        if c.get('assembly_version') not in (None,ASSEMBLY_VERSION):raise ValueError('unsupported RAG assembly version')
        result=c['result'];selected=result['selected']
        if result.get('model')!=self.provider.model:raise ValueError('RAG model identity differs')
        if result['policy']!=POLICY or result['status'] not in {'available','unavailable','warming','failed','timeout','busy'}:raise ValueError('invalid local RAG model policy/status')
        if result['status']!='available' and selected:raise ValueError('unavailable RAG cannot supply content')
        if len(c['selected_sources'])!=len(selected) or set(selected)!=set(x['event_id'] for x in c['selected_sources']):raise ValueError('RAG selected source set differs')
        best=result.get('best_similarity')
        for row in c['selected_sources']:
            i=row['event_id']
            if i not in by_id or row['content_fingerprint']!=by_id[i]['content_fingerprint'] or row['member_event_ids']!=by_id[i]['member_event_ids']:raise ValueError('RAG memory lacks exact authorized source')
            if row.get('source_slice')!=by_id[i].get('source_slice'):raise ValueError('RAG passage source range differs')
            score=selected[i]
            from math import isfinite
            if type(score) not in (int,float) or not isfinite(score) or not -1<=score<=1 or best is None or not isfinite(best):raise ValueError('invalid RAG score')
            if score<max(POLICY['minimum_similarity'],best-POLICY['maximum_distance_from_best']):raise ValueError('unqualified RAG content')
        # Numeric recall also includes actions/expressions that deliberately
        # have no text-vector document. Preserve those selected facts in the
        # union's exact-ID lookup, without adding any RAG candidates or scores.
        for identity in recall.recalled_fact_ids:
            if identity not in memories:
                memory=self._source(identity)
                if (memory.event.owner_ref!=app.owner_ref or not memory.event.visible_contents(app.owner_ref)
                    or memory.fact.session_binding_fingerprint!=source.fact.session_binding_fingerprint
                    or memory.fact.accepted_runtime_ordinal>=source.fact.accepted_runtime_ordinal):
                    raise ValueError('numeric memory union source differs')
                memories[identity]=memory
        return source,recall,memories
    def cards(self,context,event_id,*,content_view_version=None):
        source,recall,memories=self.validate(context,event_id)
        c=thaw_model_data(context);app=self.application;selected=c['result']['selected']
        addresses={}
        for document in c['selected_sources']:
            score=selected[document['event_id']]
            for i in document['member_event_ids']:
                addresses[i]=AddressReceipt(event_id=i,status='available',similarity=max(score,addresses[i].similarity) if i in addresses else score,provider=self.provider.identity,
                    model=c['result']['model'],policy_version=VERSION)

        # Reuse the existing canonical contract: numeric recall alone grants state;
        # available semantic addresses grant content only. Never fuse the scores.
        canonical=[ActualRecallCanonicalItem(event_id=i,actual_recalled=i in recall.recalled_fact_ids,
            address_receipt=addresses.get(i),content_eligible=True,state_eligible=i in recall.recalled_fact_ids)
            for i in set(recall.recalled_fact_ids)|set(addresses)]
        ids=sorted((x.event_id for x in canonical if not x.actual_recalled),key=lambda i:memories[i].fact.accepted_runtime_ordinal)
        history=app.runtime._history()
        read=history.authorize_read(reader_ref=app.owner_ref,trigger_fact_id=event_id,
            before_runtime_ordinal=source.fact.accepted_runtime_ordinal,requested_fact_ids=tuple(ids),
            authorization_ref={'rag_content_context_fingerprint':c['fingerprint'],'numeric_recall_fingerprint':recall.fingerprint})
        rows=[]
        for index,i in enumerate(ids,1):
            memory=memories[i];event=memory.event;contents=event.visible_contents(app.owner_ref)
            from model_packages.desktop_pet_v1.content_view import content_view
            text,attrs=content_view(contents,memory_content_attributes(contents,natural=True),version=content_view_version)
            fields={'记忆标记':'R'+str(index),'说话者':event.actor_ref,'对象':list(event.target_refs),
                    '位置':{'domain':event.position.binding.domain_id,'coordinate':event.position.coordinate},'正文':text,'内容属性':attrs}
            from petlab_app.visible_delivery import presentation_fields
            fields=presentation_fields(app,event,fields)
            annotation=for_memory(app.runtime.session.profile,memory,app.owner_ref)
            if annotation is not None:fields[FIELD]=annotation
            if 'address_selection' in c:
                from .relation_annotations import _measurement
                parsed=_measurement(app.runtime.session.profile,memory).get('pet_memory_recall',{})
                if parsed.get('version') in ('desktop-pet-hierarchical-property-recall-v7','desktop-pet-property-address-recall-v8'):
                    from model_packages.desktop_pet_v1.hierarchy_semantics import validate
                    fields['语义属性']=[{k:thaw_model_data(v) for k,v in p.items() if k!='span'} for p in validate(event,parsed)]
            rows.append(WriterCard(card_id='memory:local-rag:'+i,lane='memory',kind='addressed_pet_event',source_id=i,fields=fields,
                source_receipt_refs=(memory.fact.fingerprint,c['fingerprint'],read.fingerprint),
                provenance={'selected_memory_ids':[i],'rag_context_fingerprint':c['fingerprint'],'state_eligible':False,
                            'address_receipt':addresses[i].to_record()},budget_policy='preserve_selection'))
        return tuple(rows)
    def compose_cards(self,context,event_id,numeric_cards,*,content_view_version=None,links=False,visibility=None):
        c=thaw_model_data(context)
        rag_cards=self.cards(context,event_id,content_view_version=content_view_version)
        if visibility is not None:
            from petlab_app.writer_visibility import filter_memories
            ids=tuple(i for c in rag_cards for i in c.provenance['selected_memory_ids'])
            allowed={m.event.event_id for m in filter_memories(self.application,(self._source(i) for i in ids),visibility,lane='rag')}
            rag_cards=tuple(c for c in rag_cards if set(c.provenance['selected_memory_ids'])<=allowed)
        cards=(*numeric_cards,*rag_cards)
        # Old sealed Writer requests must retain their original two-block view.
        if c.get('assembly_version') is None:return cards
        source,recall,memories=self.validate(context,event_id)
        ids=tuple(sorted((i for card in cards for i in card.provenance['selected_memory_ids']),
                         key=lambda i:memories[i].fact.accepted_runtime_ordinal))
        if len(ids)!=len(set(ids)):raise ValueError('memory union repeats a selected source')
        history=self.application.runtime._history()
        read=history.authorize_read(reader_ref=self.application.owner_ref,trigger_fact_id=event_id,
            before_runtime_ordinal=source.fact.accepted_runtime_ordinal,requested_fact_ids=ids,
            authorization_ref={'rag_content_context_fingerprint':c['fingerprint'],
                               'numeric_recall_fingerprint':recall.fingerprint,'assembly_version':ASSEMBLY_VERSION})
        from .memory_delivery import ordered_memory_union
        merged=ordered_memory_union(cards,memories=memories,source_refs=(read.fingerprint,),assembly_version=ASSEMBLY_VERSION)
        if not merged or not links:return merged
        from ops.memory_link_history_source import history_memory_link_subcard
        from model_packages.cx_six_seven_v1.memory_link_delivery import with_memory_links
        child=history_memory_link_subcard(history=history,history_read=read,parent_card_id=merged[0].card_id,
            selected_memory_ids=ids,enabled=True)
        return (with_memory_links(merged[0],child),)

    def prime(self):
        app=self.application;history=app.runtime._history();facts=history.facts()
        if not facts or self.provider.status!='ready':return
        source=self._source(facts[-1].fact_id)
        docs,_=self._documents_for(event=source.event,before_ordinal=source.fact.accepted_runtime_ordinal+1,history=history)
        namespace=model_data_fingerprint({'binding':source.fact.session_binding_fingerprint,'owner':app.owner_ref,'scope':source.event.scope_ref})
        self.provider.prime(documents=tuple(docs),namespace=namespace)
    def close(self):self.provider.close()
