"""Exact-ID, content-only local RAG beside the already-settled numeric lane."""
from pathlib import Path
from types import SimpleNamespace
import hashlib,json,re,time
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
from . import memory_rerank
from .memory_fragments import ROLE_MATCH_LAYOUTS, SOURCE_TYPED_LAYOUTS, WEIGHTED_VERSIONS
from model_packages.desktop_pet_v1.address_recall import INDEPENDENT_CONFIG, INDEPENDENT_VERSION

CONTEXT_VERSION='desktop-pet-local-rag-exchange-content-context-v2'
RETRIEVAL_TEXT_VERSION='desktop-pet-literal-query-address-filter-v1'
INDEPENDENT_RETRIEVAL_VERSION='desktop-pet-literal-query-independent-candidates-v2'
THREE_ROUTE_TEXT_VERSION='desktop-pet-visible-content-query-v3'
LITERAL_ADDRESS_VERSION='desktop-pet-quoted-value-address-v1'
_QUOTE_PAIRS=(('"','"'), ("'","'"), ('“','”'), ('‘','’'),
              ('「','」'), ('『','』'), ('《','》'))
_QUOTE_CHARS=''.join(left+right for left,right in _QUOTE_PAIRS)
_CLAUSE_BREAK=re.compile(r'[\r\n.!?;。！？；]+')

def _literal_query(event):
    return '\n'.join(c.description for c in event.visible_contents(event.owner_ref) if c.kind=='speech')

def _signed(record):return {**record,'fingerprint':model_data_fingerprint(record)}


def _independent_routes(basis):
    return (basis.get('cue') or {}).get('retrieval_routes')==INDEPENDENT_VERSION


def _retrieval_text(event,basis):
    if _independent_routes(basis):
        return _visible_query(event)
    return _literal_query(event)


def _visible_query(event):
    return '\n'.join(c.description for c in event.visible_contents(event.owner_ref) if not c.private)

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
GRADED_ADDRESS_REPORT_VERSION='desktop-pet-shared-address-candidates-v2'
from .rag_passages import VERSION as DOCUMENT_VERSION,project as passage_documents
LAYERED_DOCUMENT_VERSION='desktop-pet-layered-memory-documents-v1'
CHILD_DOCUMENT_VERSION='desktop-pet-layered-memory-children-v2'
SOURCE_TYPED_DOCUMENT_VERSION='desktop-pet-layered-memory-sources-v3'
CHILD_DOCUMENT_VERSIONS=(CHILD_DOCUMENT_VERSION,SOURCE_TYPED_DOCUMENT_VERSION)
LAYERED_VERSIONS=(LAYERED_DOCUMENT_VERSION,*CHILD_DOCUMENT_VERSIONS)

@lru_cache(maxsize=4096)
def _address_projection(properties):
    """Memoize pure address arithmetic, never a source/permission decision."""
    from model_packages.semantic_addresses import address_indexes
    from model_packages.desktop_pet_v1.address_recall import addresses
    rows=addresses([{'owner':owner,'path':path} for owner,path in properties])
    indexes=tuple(address_indexes(rows))
    return indexes,not indexes or any(row['entity_id'] is None for row in rows)

class RagMemoryService:
    def __init__(self,*,application,provider,trace_dir,reply_source_key=None):
        self.application=application;self.provider=provider;self.trace_dir=Path(trace_dir)
        self._sources={};self._exchanges={};self._reply_keys={};self._prefetch=None;self.reply_source_key=reply_source_key;self._address_cache={};self._passages={};self._literal_values={}
    def _source(self,event_id):return self.application.runtime.memory_instance(event_id)
    def _document_version(self,*,independent=False):
        from .metacognition import enabled
        return SOURCE_TYPED_DOCUMENT_VERSION if independent else LAYERED_DOCUMENT_VERSION if enabled() else DOCUMENT_VERSION

    def _documents(self,source,history,*,document_version=DOCUMENT_VERSION):
        return self._documents_for(event=source.event,before_ordinal=source.fact.accepted_runtime_ordinal,history=history,document_version=document_version)

    def _layered_documents(self,event,before_ordinal,history,*,children=False,facts=None,source_typed=False):
        from .metacognition import execution_note
        from petlab_app.intention_source import ORIGIN
        from petlab_app.intention_progress import PROOFS
        from model_packages.cx_six_seven_v1.intention_facts import VERSION as INTENT_CHAINS,validate
        app=self.application
        profile=app.runtime.session.profile
        sources=profile.get('desktop_pet_product_v1',{}).get('sources',{})
        documents,memories=[],{}
        for fact in history.facts() if facts is None else facts:
            if (fact.accepted_runtime_ordinal>=before_ordinal or app.owner_ref not in fact.visible_to_refs
                    or fact.fact_id not in sources):continue
            row=sources[fact.fact_id]
            prior=PetEvent.from_record(row['event'])
            if prior.owner_ref!=app.owner_ref or prior.scope_ref!=event.scope_ref:continue
            intention=row['origin']==ORIGIN
            visible=prior.visible_contents(app.owner_ref)
            if not visible:continue
            if intention:
                if prior.actor_ref!=app.owner_ref:continue
                app._verify_source(prior)
            from model_packages.desktop_pet_v1.web_observation import is_content
            observed=tuple(c for c in visible if c.kind in ('speech','action','expression','interaction')
                      or is_content(c) or (intention and c.kind=='thought' and c.private))
            layers={'experience':'\n'.join(c.description for c in observed)}
            if children:
                layers.pop('experience')
                for c in observed:
                    if source_typed:
                        from .memory_fragments import experience_text
                        material=experience_text(c,intention=intention)
                    else:
                        material=('已形成的意图：' if intention else '实际经历：')+c.description
                    fingerprint=model_data_fingerprint({'fact':fact.fingerprint,'content':c.content_id,'text':material})
                    documents.append({'event_id':'rag-layer:'+fingerprint,'content_fingerprint':fingerprint,
                                      'text':material,'layer':'experience','member_event_ids':[fact.fact_id],
                                      'content_id':c.content_id})
            note=execution_note(row,app.owner_ref)
            if intention:
                chain=profile[INTENT_CHAINS][row['evidence']['source_ref']]
                validate(chain)
                for item in reversed(chain):
                    if item['phase']!='planned' or item['at_ms']>=event.position.coordinate:continue
                    proof=profile[PROOFS][item['evidence_ref']]
                    if (model_data_fingerprint(proof)!=item['evidence_ref'] or proof['owner']!=app.owner_ref
                            or proof['visibility']!='self' or proof['phase']!='planned'):
                        raise ValueError('RAG intention plan source differs')
                    plan=proof.get('metacognition',{})
                    note={'basis':plan.get('basis'),'purpose':plan.get('purpose'),
                          'speech_targets':plan.get('intended_targets',())}
                    break
            if note:
                layers['basis']=note.get('basis')
                if note.get('speech_targets'):layers['purpose']=note.get('purpose')
            for layer,text in layers.items():
                if not text:continue
                label=('已形成的意图' if intention else '实际经历') if layer=='experience' else {
                    'basis':'当时的判断依据','purpose':'当时的表达目的'}[layer]
                material=f'{label}：{text}'
                if layer=='purpose':material+='\n原对象：'+'、'.join(note['speech_targets'])
                fingerprint=model_data_fingerprint({'fact':fact.fingerprint,'layer':layer,'text':material})
                documents.append({'event_id':'rag-layer:'+fingerprint,'content_fingerprint':fingerprint,
                                  'text':material,'layer':layer,'member_event_ids':[fact.fact_id]})
            memories[fact.fact_id]=MemoryInstance(prior,fact)
        return passage_documents(documents),memories

    def _documents_for(self,*,event,before_ordinal,history,document_version=DOCUMENT_VERSION):
        if document_version in LAYERED_VERSIONS:
            return self._layered_documents(event,before_ordinal,history,children=document_version in CHILD_DOCUMENT_VERSIONS,
                                           source_typed=document_version==SOURCE_TYPED_DOCUMENT_VERSION)
        query_scope=event.scope_ref
        from petlab_app.session import PRODUCT_KEY
        app=self.application;docs=[];memories={};profile=app.runtime.session.profile
        journal=profile.get(EVENT_JOURNAL_KEY,{});sources=profile.get(PRODUCT_KEY,{}).get('sources',{})
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
                from petlab_app.intention_source import ORIGIN,enabled
                own_intention=(enabled(app) and event.owner_ref==app.owner_ref and event.actor_ref==app.owner_ref
                    and sources[event.event_id]['origin']==ORIGIN)
                if own_intention:
                    app._verify_source(event)
                    speech=[c.description for c in visible if c.kind=='thought' and c.private]
                if (event.owner_ref!=app.owner_ref or not speech):self._sources[key]=None
                else:
                    text=('本人私有意图：' if own_intention else '外部网页观察：' if any(is_content(c) for c in visible) else '用户说：' if event.actor_ref==app.user_ref else '角色说：')+'\n'.join(speech)
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
        _documents,memories=self._documents_for(event=source.event,
            before_ordinal=source.fact.accepted_runtime_ordinal+1,history=history)
        for memory in memories.values():self._address_properties(memory)

    def _address_properties(self,memory):
        key=memory.fact.fingerprint
        if key not in self._address_cache:
            from .relation_annotations import _measurement
            from model_packages.desktop_pet_v1.hierarchy_semantics import scope_speech_reports,validate
            from petlab_app.participant_input import VISIBLE_VERSION
            app=self.application
            parsed=_measurement(app.runtime.session.profile,memory).get('pet_memory_recall',{})
            event=memory.event
            properties=(validate(event,parsed) if parsed.get('version') in
                ('desktop-pet-hierarchical-property-recall-v7','desktop-pet-property-address-recall-v8') else [])
            source=app.runtime.session.profile['desktop_pet_product_v1']['sources'].get(event.event_id,{})
            if event.event_kind=='npc_output' or source.get('evidence',{}).get('input_source',{}).get('version')==VISIBLE_VERSION:
                properties,_=scope_speech_reports(event,properties)
            self._address_cache[key]=properties
        return self._address_cache[key]

    def _address_documents(self,documents,memories,basis,*,require_topic=False):
        """Apply the shared source address projection before vector inference.

        Old unannotated records remain available to text RAG. Unknown owners
        never become a precise identity. Selection never splits an exchange.
        """
        if 'addresses' not in basis:return documents,None
        from model_packages.semantic_addresses import levels,address_indexes,match_addresses
        from model_packages.desktop_pet_v1.address_recall import addresses
        candidates=[];unlocated=[]
        for doc in documents:
            props=[]
            for identity in doc['member_event_ids']:
                props.extend(p for p in self._address_properties(memories[identity]) if p['kind']=='assertion')
            indexes,unresolved=_address_projection(tuple((p['owner'],tuple(p['path'])) for p in props))
            if unresolved:unlocated.append(doc['event_id'])
            candidates.append({'event_id':doc['event_id'],'indexes':indexes})
        selected,decisions=match_addresses(basis['addresses'],candidates)
        if require_topic:
            # Numeric grading gives an empty path zero semantic strength.
            for decision in decisions:
                if decision['level']==len(levels(decision['query']))-1:
                    decision.update(level=None,fingerprint=None,matches=[])
            selected={identity for decision in decisions for identity in decision['matches']}
        resolved=any(levels(a) for a in basis['addresses'])
        allowed=set(selected)|set(unlocated) if resolved else {d['event_id'] for d in documents}
        qualified=[d for d in documents if d['event_id'] in allowed]
        return qualified,{
            'version':GRADED_ADDRESS_REPORT_VERSION if require_topic else 'desktop-pet-shared-address-candidates-v1',
            'decisions':[{k:v for k,v in d.items() if k!='matches'}|{'match_count':len(d['matches'])} for d in decisions],
            'unlocated_source_count':len(unlocated),'unresolved_query':not resolved,
            'selected_document_count':len(qualified),'selected_document_fingerprint':_snapshot(qualified)}

    def _rag_candidates(self,docs,memories,basis,*,independent,legacy_owner_only=False,layered=False):
        filtered,selection=self._address_documents(docs,memories,basis,require_topic=independent and not legacy_owner_only)
        if (independent or layered) and selection is not None:
            selection={**selection,'candidate_filter_applied':False,'rag_candidate_count':len(docs)}
            return docs,selection
        return filtered,selection

    def _literal_address(self,event,memories):
        # Match complete clauses against verified values. A quoted occurrence
        # anchors the designation; other matching assertions can carry its context.
        clauses={text.strip().strip(_QUOTE_CHARS).strip()
                 for text in _CLAUSE_BREAK.split(_literal_query(event))}
        clauses.discard('')
        if not clauses:return None
        from model_packages.desktop_pet_v1.semantic import evidence_source

        sources=[];anchored=set()
        for identity,memory in memories.items():
            key=memory.fact.fingerprint
            if key in self._address_cache and not self._address_cache[key]:
                continue
            prior=memory.event
            if prior.visible_contents(event.owner_ref)!=prior.contents or any(
                    content.kind!='speech' or content.private for content in prior.contents):
                continue
            if key not in self._literal_values:
                text=evidence_source(prior)
                matches=[]
                for prop in self._address_properties(memory):
                    value=prop['value']
                    if prop['kind']!='assertion' or prop['certainty']!='stated' or not isinstance(value,str) or not value.strip():
                        continue
                    start,end=prop['span']
                    evidence=text[start:end]
                    if value not in evidence:continue
                    quoted=any(left+value+right in evidence for left,right in _QUOTE_PAIRS)
                    matches.append({'value':value,'span':[start,end],'quoted':quoted})
                self._literal_values[key]=matches
            for match in self._literal_values[key]:
                if match['value'] not in clauses:continue
                sources.append({'event_id':identity,**match})
                if match['quoted']:anchored.add(match['value'])
        sources=[row for row in sources if row['value'] in anchored]
        return {'version':LITERAL_ADDRESS_VERSION,'sources':sources} if sources else None

    def _property_sources(self,event,before_ordinal,history,basis,memories):
        """Read committed property facts even if they have no vector document."""
        from model_packages.desktop_pet_v1.address_recall import (
            _graded_properties, _predecessor_phrases,
        )
        from model_packages.desktop_pet_v1.semantic import evidence_source

        app=self.application
        cue=basis['cue']
        refs={r['source']['event_id'] for r in cue.get('predecessor_refs',())}
        journal=app.runtime.session.profile.get(EVENT_JOURNAL_KEY,{})
        candidates={}; scoped={}
        for fact in history.facts():
            identity=fact.fact_id
            if fact.accepted_runtime_ordinal>=before_ordinal or app.owner_ref not in fact.visible_to_refs:
                continue
            if identity not in journal:
                continue
            memory=memories.get(identity)
            prior=memory.event if memory is not None else PetEvent.from_record(journal[identity]['event'])
            if (prior.owner_ref!=event.owner_ref or prior.scope_ref!=event.scope_ref
                    or not prior.visible_contents(app.owner_ref)):
                continue
            memory=memory or MemoryInstance(prior,fact)
            properties=self._address_properties(memory) if cue['properties'] else ()
            if not properties and identity not in refs:
                continue
            candidates[identity]=properties
            scoped[identity]=memory
        strengths,decisions=_graded_properties(cue['properties'],candidates)
        rows=[]
        for identity,memory in scoped.items():
            prior=memory.event
            evidence={'event_id':identity,'event_fingerprint':prior.fingerprint,
                      'source_ref':prior.source_binding_ref,
                      '_declaration_key':(memory.fact.session_binding_fingerprint,memory.fact.fingerprint)}
            quotes=list(_predecessor_phrases(cue,{**evidence,'text':evidence_source(prior)})) if identity in refs else []
            if identity not in strengths and not quotes:
                continue
            memories[identity]=memory
            rows.append({'event_id':identity,'fact_fingerprint':memory.fact.fingerprint,
                         'shared_depth_strength':strengths.get(identity,0.),
                         'matched_queries':[i for i,d in enumerate(decisions) if identity in d['matches']],
                         'predecessor_quotes':quotes})
        return rows

    def _basis(self,source):
        from .relation_annotations import _measurement
        from model_packages.desktop_pet_v1.retrieval_query import query_basis
        measurement=_measurement(self.application.runtime.session.profile,source)
        return query_basis(source.event,measurement.get('pet_memory_recall'))
    def prefetch(self,event,*,measurement=None):
        app=self.application
        if event.event_kind!='peer_input':return
        if event.event_id in app.runtime.session.profile.get(EVENT_JOURNAL_KEY,{}):return
        status=getattr(self.provider,'status','ready')
        if status!='ready' and not (status=='warming' and hasattr(self.provider,'submit_latest')):return
        app._verify_source(event)
        from model_packages.desktop_pet_v1.retrieval_query import query_basis
        basis=query_basis(event,(measurement or {}).get('pet_memory_recall'))
        routes=_independent_routes(basis) or (measurement is None
            and getattr(app,'memory_recall_version',None)==INDEPENDENT_CONFIG)
        text=_visible_query(event) if routes else _retrieval_text(event,basis)
        if not text.strip():return
        history=app.runtime._history();facts=history.facts()
        boundary=max((f.accepted_runtime_ordinal for f in facts),default=-1)+1
        version=self._document_version(independent=routes)
        docs,memories=self._documents_for(event=event,before_ordinal=boundary,history=history,document_version=version)
        independent=bool(basis.get('cue') and basis['cue'].get('address_strength_version'))
        if not independent and version not in LAYERED_VERSIONS:
            docs,_=self._address_documents(docs,memories,basis)
        if not docs:return
        namespace=model_data_fingerprint({'binding':app.runtime.session.session_binding.fingerprint,'owner':app.owner_ref,'scope':event.scope_ref})
        snapshot=_snapshot(docs)
        query_fingerprint=hashlib.sha256(text.encode('utf8')).hexdigest()
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
        if source.event.event_kind!='peer_input':return None
        basis=self._basis(source);text=_retrieval_text(source.event,basis)
        if not text.strip():return None
        history=app.runtime._history();recall=self._recall(event_id,history)
        routes=_independent_routes(basis)
        version=self._document_version(independent=routes)
        assembly_version=memory_rerank.VERSION if version==LAYERED_DOCUMENT_VERSION else ASSEMBLY_VERSION
        if routes:
            assembly_version=memory_rerank.UNIFIED_VERSION
        key=model_data_fingerprint({'binding':source.fact.session_binding_fingerprint,'source':source.fact.fingerprint,
            'policy':VERSION,'documents':version,**({'assembly':assembly_version} if assembly_version!=ASSEMBLY_VERSION else {})})
        path=self.trace_dir/(key+'.json')
        if path.exists():
            record=json.loads(path.read_text(encoding='utf8'));self.validate(record,event_id,history=history);return record
        docs,memories=(self._documents(source,history) if version==DOCUMENT_VERSION else
                       self._documents(source,history,document_version=version))
        literal=self._literal_address(source.event,memories)
        property_sources=self._property_sources(source.event,source.fact.accepted_runtime_ordinal,history,basis,memories) if routes else None
        independent=bool(basis.get('cue') and basis['cue'].get('address_strength_version'))
        docs,address_selection=self._rag_candidates(docs,memories,basis,independent=independent,
                                                       layered=version in LAYERED_VERSIONS)
        snapshot=_snapshot(docs)
        namespace=model_data_fingerprint({'binding':source.fact.session_binding_fingerprint,'owner':app.owner_ref,'scope':source.event.scope_ref})
        start=time.perf_counter()
        pending=self._prefetch;self._prefetch=None
        parallel=bool(pending and pending[0]==source.event.fingerprint and pending[1]==snapshot and pending[4]==hashlib.sha256(text.encode('utf8')).hexdigest())
        result=(self.provider.result(pending[2],timeout_s=.5) if parallel else
                self.provider.retrieve(query=text,documents=tuple(docs),namespace=namespace,timeout_s=.5)) if docs else {
                'status':'available','selected':{},'model':self.provider.model,'policy':dict(POLICY),'candidate_count':0,'cached_vectors':0,'new_vectors':0,'best_similarity':None}

        selected=result.get('selected',{});by_id={r['event_id']:r for r in docs}
        content_scores=result.get('content_scores',{}).get('scores',{})
        if not set(content_scores)<=set(by_id):raise ValueError('content scores escaped the exact-ID set')
        def source_record(i):
            return {'event_id':i,'content_fingerprint':by_id[i]['content_fingerprint'],
                    'member_event_ids':by_id[i]['member_event_ids'],
                    **{k:by_id[i][k] for k in ('layer','source_slice','content_id') if k in by_id[i]}}
        if not isinstance(selected,dict) or not set(selected)<=set(by_id):raise ValueError('local RAG escaped the closed exact-ID set')
        text_version=(THREE_ROUTE_TEXT_VERSION if routes else
                      INDEPENDENT_RETRIEVAL_VERSION if independent else RETRIEVAL_TEXT_VERSION)
        record=_signed({'version':CONTEXT_VERSION,'document_version':version,'retrieval_text_version':text_version,'assembly_version':assembly_version,'source_event_id':event_id,'source_fact_fingerprint':source.fact.fingerprint,
            'session_binding_fingerprint':source.fact.session_binding_fingerprint,'scope_ref':source.event.scope_ref,
            'query_fingerprint':hashlib.sha256(text.encode('utf8')).hexdigest(),'query_basis':basis,'candidate_snapshot':snapshot,
            **({'address_selection':address_selection} if address_selection is not None else {}),
            **({'literal_address':literal} if literal is not None else {}),
            **({'property_sources':property_sources} if routes else {}),
            'numeric_recall_fingerprint':recall.fingerprint,'result':result,
            'selected_sources':[source_record(i) for i in selected],
            **({'content_score_sources':[source_record(i) for i in content_scores]} if content_scores else {}),
            'elapsed_ms':round((time.perf_counter()-start)*1000),'parallel_with_measurement':parallel,'state_commit_authority':False,'send_authority':False})
        self._validate(record,event_id,history=history,
            prepared=(source,recall,docs,memories,basis,address_selection,literal,snapshot,property_sources))
        self.trace_dir.mkdir(parents=True,exist_ok=True)
        with path.open('x',encoding='utf8') as f:json.dump(record,f,ensure_ascii=False,separators=(',',':'),allow_nan=False)
        return record
    def validate(self,context,event_id,*,history=None):
        return self._validate(context,event_id,history=history)

    def _validate(self,context,event_id,*,history=None,prepared=None):
        from petlab_app.destinations import _INPUT_SNAPSHOT
        app=self.application
        c=thaw_model_data(context)
        if c.get('version')!=CONTEXT_VERSION or c.get('fingerprint')!=model_data_fingerprint({k:v for k,v in c.items() if k!='fingerprint'}):raise ValueError('local RAG context fingerprint differs')
        cache=_INPUT_SNAPSHOT.get()
        cache_key=('validated-rag-context',id(self),event_id,c['fingerprint'],self.provider.model)
        cached=cache.get(cache_key) if cache is not None else None
        if cached is not None and cached[0] is self and cached[1] is app.runtime.session:
            return cached[2]
        if prepared is None:
            source=self._source(event_id)
            history=history or app.runtime._history();recall=self._recall(event_id,history)
            docs,memories=self._documents(source,history,document_version=c.get('document_version'))
            basis=self._basis(source) if 'query_basis' in c or 'address_selection' in c else None
            literal=self._literal_address(source.event,memories) if 'literal_address' in c else None
            property_sources=self._property_sources(source.event,source.fact.accepted_runtime_ordinal,history,basis,memories) if basis and _independent_routes(basis) else None
            selection=None
            if 'address_selection' in c:
                independent=c.get('retrieval_text_version') in (INDEPENDENT_RETRIEVAL_VERSION,THREE_ROUTE_TEXT_VERSION)
                docs,selection=self._rag_candidates(docs,memories,basis,independent=independent,
                    legacy_owner_only=c['address_selection'].get('version')=='desktop-pet-shared-address-candidates-v1',
                    layered=c.get('document_version') in LAYERED_VERSIONS)
            snapshot=_snapshot(docs)
        else:
            # Only context() supplies this, within the same synchronous build.
            source,recall,docs,memories,basis,selection,literal,snapshot,property_sources=prepared
        if ('property_sources' in c)!=(property_sources is not None) or c.get('property_sources')!=property_sources:
            raise ValueError('property lane differs from committed sources')
        if 'literal_address' in c and (literal is None or c['literal_address']!=literal):
            raise ValueError('RAG literal address differs from committed sources')
        if 'address_selection' in c and c['address_selection']!=selection:
            raise ValueError('RAG address selection differs from committed sources')
        by_id={r['event_id']:r for r in docs}
        from .relation_annotations import _measurement
        if _measurement(app.runtime.session.profile,source).get('pet_memory_recall',{}).get('version')=='desktop-pet-property-address-recall-v8':
            if 'query_basis' not in c or 'address_selection' not in c:raise ValueError('RAG property source contract missing')
        text='\n'.join(x.description for x in source.event.visible_contents(app.owner_ref) if x.kind=='speech')
        if 'query_basis' in c:
            if 'addresses' in basis and 'address_selection' not in c:raise ValueError('RAG address selection missing')
            if c['query_basis']!=basis:raise ValueError('RAG query basis differs from committed source')
            text=basis['text']
        if 'retrieval_text_version' in c:
            if c['retrieval_text_version'] not in (RETRIEVAL_TEXT_VERSION,INDEPENDENT_RETRIEVAL_VERSION,THREE_ROUTE_TEXT_VERSION):
                raise ValueError('unsupported retrieval text version')
            if (c['retrieval_text_version']==THREE_ROUTE_TEXT_VERSION)!=bool(basis and _independent_routes(basis)):
                raise ValueError('retrieval text version differs from source routes')
            if c['retrieval_text_version']==INDEPENDENT_RETRIEVAL_VERSION and (not basis or not (basis.get('cue') or {}).get('address_strength_version')):
                raise ValueError('independent RAG context lacks graded address contract')
            text=_retrieval_text(source.event,basis) if basis is not None else _literal_query(source.event)
        if (c['source_event_id']!=event_id or c['source_fact_fingerprint']!=source.fact.fingerprint
            or c['session_binding_fingerprint']!=source.fact.session_binding_fingerprint or c['scope_ref']!=source.event.scope_ref
            or c['numeric_recall_fingerprint']!=recall.fingerprint
            or c['query_fingerprint']!=hashlib.sha256(text.encode('utf8')).hexdigest()
            or c['candidate_snapshot']!=snapshot
            or c['state_commit_authority'] is not False or c['send_authority'] is not False):raise ValueError('local RAG context source differs')
        if c.get('assembly_version') not in (None,ASSEMBLY_VERSION,memory_rerank.VERSION,memory_rerank.UNIFIED_VERSION):raise ValueError('unsupported RAG assembly version')
        if c.get('assembly_version') in (memory_rerank.VERSION,memory_rerank.UNIFIED_VERSION) and c.get('document_version') not in LAYERED_VERSIONS:
            raise ValueError('effect reranking requires layered source evidence')
        if c.get('assembly_version')==memory_rerank.UNIFIED_VERSION and property_sources is None:
            raise ValueError('unified ranking needs independent retrieval sources')
        result=c['result'];selected=result['selected']
        if result.get('model')!=self.provider.model:raise ValueError('RAG model identity differs')
        if result['policy']!=POLICY or result['status'] not in {'available','unavailable','warming','failed','timeout','busy'}:raise ValueError('invalid local RAG model policy/status')
        if result['status']!='available' and selected:raise ValueError('unavailable RAG cannot supply content')
        if len(c['selected_sources'])!=len(selected) or set(selected)!=set(x['event_id'] for x in c['selected_sources']):raise ValueError('RAG selected source set differs')
        for row in c['selected_sources']:
            i=row['event_id']
            if i not in by_id or row['content_fingerprint']!=by_id[i]['content_fingerprint'] or row['member_event_ids']!=by_id[i]['member_event_ids']:raise ValueError('RAG memory lacks exact authorized source')
            if any(row.get(k)!=by_id[i].get(k) for k in ('source_slice','content_id')):raise ValueError('RAG passage source range differs')
            layer=by_id[i].get('layer')
            if row.get('layer')!=layer:raise ValueError('RAG source layer differs')
            best=result.get('layer_best_similarity',{}).get(layer) if layer is not None else result.get('best_similarity')
            score=selected[i]
            from math import isfinite
            if type(score) not in (int,float) or not isfinite(score) or not -1<=score<=1 or best is None or not isfinite(best):raise ValueError('invalid RAG score')
            if score<max(POLICY['minimum_similarity'],best-POLICY['maximum_distance_from_best']):raise ValueError('unqualified RAG content')
        content=result.get('content_scores')
        score_sources=c.get('content_score_sources',())
        if content is not None:
            from math import isfinite
            method=content.get('method')
            scores=content.get('scores',{})
            partial=content.get('partial_rerank')
            if partial is not None and (
                method!='embedding' or not isinstance(partial,dict) or not partial
                or not set(partial)<=set(scores) or len(partial)>=len(scores)
                or len(partial)!=result['coverage']['rank_examined']
                or any(type(v) not in (float,int) or not isfinite(v) or not 0<=v<=1 for v in partial.values())):
                raise ValueError('invalid partial RAG rerank scores')
            if (c['document_version'] not in LAYERED_VERSIONS or result['status']!='available'
                or method not in ('embedding','reranker') or not scores
                or len(scores)!=len(score_sources) or set(scores)!={r['event_id'] for r in score_sources}
                or len(scores)!=result['coverage']['vector_examined']
                or method=='reranker' and len(scores)!=result['coverage']['rank_examined']
                or any(type(v) not in (float,int) or not isfinite(v)
                       or not (-1 if method=='embedding' else 0)<=v<=1 for v in scores.values())):
                raise ValueError('invalid RAG competition scores')
            for row in score_sources:
                doc=by_id.get(row['event_id'])
                if doc is None or any(row.get(k)!=doc.get(k) for k in (
                        'content_fingerprint','member_event_ids','layer','source_slice','content_id')):
                    raise ValueError('RAG competition source differs')
        elif score_sources:
            raise ValueError('RAG competition sources lack scores')
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
        validated=(source,recall,memories)
        if cache is not None:cache[cache_key]=(self,app.runtime.session,validated)
        return validated
    def cards(self,context,event_id,*,content_view_version=None,intention_snapshot=None):
        validated=self.validate(context,event_id)
        return self._cards(context,event_id,validated,content_view_version=content_view_version,intention_snapshot=intention_snapshot)
    def writer_ranking(self, context, event_id, *, visibility, fragment_memory=False,
                       ranking_version=memory_rerank.UNIFIED_VERSION):
        from petlab_app.writer_visibility import filter_memories, memory_contents
        from .weak_memory_content import _occupy
        source,recall,memories=self.validate(context,event_id)
        app=self.application
        saved=app._product()['sources'][event_id].get('writer_numeric_recall')
        numeric={}
        if saved is not None:
            if model_data_fingerprint(saved)!=recall.model_output_fingerprint:
                raise ValueError('Writer numeric scores differ from formal recall')
            numeric={i:row['impulse'] for i,row in saved['recall_decision']['candidate_evidence'].items()
                     if i in recall.recalled_fact_ids}
        properties={row['event_id']:1. if row['predecessor_quotes'] else row['shared_depth_strength']
                    for row in context['property_sources']}
        for row in context.get('literal_address',{}).get('sources',()):
            properties[row['event_id']]=1.
        result=context['result']
        content=result.get('content_scores',{})
        raw=content.get('scores',result['selected'])
        documents=context.get('content_score_sources',context['selected_sources'])
        child_mode=context['document_version'] in CHILD_DOCUMENT_VERSIONS
        projected={}
        fragment_matches={}
        if child_mode and fragment_memory:
            from .memory_fragments import best_match, fragments, match, receipt_phases, weighted_match
            from functools import partial
            weighted = fragment_memory in WEIGHTED_VERSIONS
            match_fragments = partial(weighted_match, version=fragment_memory) if weighted else best_match
            cue=context['query_basis']['cue']
            query=fragments(source.event,source.event.visible_contents(app.owner_ref),cue['properties'])
            if weighted:
                phases=receipt_phases(app,source.event)
                query=[{**row,'phase':phases.get(row['id'])} for row in query]
            ids=set(properties)|set(recall.recalled_fact_ids)|{i for d in documents for i in d['member_event_ids']}
            projected={i:memory_contents(app,memories[i],visibility) for i in ids}
            properties={}
            cues=[row for row in query if row['properties'] or row['behavior'] or (weighted and row['objects'])]
            score_phases=weighted and any(row.get('phase') is not None for row in cues)
            if cues:
                for identity,memory in memories.items():
                    contents=projected.get(identity, memory.event.visible_contents(app.owner_ref))
                    if not contents:
                        continue
                    tags=self._address_properties(memory)
                    if identity not in projected:
                        historical=fragments(memory.event,contents,tags)
                        if weighted or fragment_memory in ROLE_MATCH_LAYOUTS:
                            found=match_fragments(cues,historical) is not None
                        else:
                            found=any(match(q,h) for q in cues for h in historical)
                        if not found:
                            continue
                        projected[identity]=memory_contents(app,memory,visibility)
                    historical=fragments(memory.event,projected[identity],tags)
                    if weighted or fragment_memory in ROLE_MATCH_LAYOUTS:
                        witness=match_fragments(cues,historical)
                        if score_phases and witness:
                            phases=receipt_phases(app,memory.event)
                            historical=[{**row,'phase':phases.get(row['id'])} for row in historical]
                            witness=match_fragments(cues,historical)
                        if witness:
                            fragment_matches[identity]=witness
                            properties[identity]=witness['score']
                    else:
                        strength=max((match(q,h) for q in cues for h in historical),default=0.0)
                        if strength:
                            properties[identity]=strength
            # A verified exact quotation keeps its existing route. Only an
            # authorized child containing that quotation can carry the hit.
            for record in context['property_sources']:
                identity=record['event_id']
                if any(quote in content.description for quote in record['predecessor_quotes']
                       for content in projected[identity]):
                    if not weighted or identity not in properties:
                        properties[identity]=1.
            for record in context.get('literal_address',{}).get('sources',()):
                identity=record['event_id']
                if projected[identity] and projected[identity]==memories[identity].event.visible_contents(app.owner_ref):
                    if not weighted or identity not in properties:
                        properties[identity]=1.
        elif child_mode:
            ids=set(properties)|set(recall.recalled_fact_ids)|{i for d in documents for i in d['member_event_ids']}
            projected={i:memory_contents(app,memories[i],visibility) for i in ids}
            # An event-wide attribute or private reasoning cannot stand in for
            # the specific child that was authorized.
            records={r['event_id']:r for r in context['property_sources']}
            from dataclasses import replace
            from model_packages.desktop_pet_v1.semantic import evidence_source
            for i in tuple(properties):
                event=memories[i].event
                if projected[i]!=event.visible_contents(app.owner_ref):
                    row=records.get(i,{})
                    quotes=row.get('predecessor_quotes',())
                    visible=tuple(evidence_source(replace(event,contents=(c,))) for c in projected[i])
                    if any(q in text for q in quotes for text in visible):
                        continue
                    # Reuse the same property depth matching on assertions
                    # whose spans lie inside a retained child.
                    from model_packages.desktop_pet_v1.address_recall import property_strengths
                    props=self._address_properties(memories[i])
                    offset=0; located=[]
                    for child in event.contents:
                        end=offset+len(evidence_source(replace(event,contents=(child,))))
                        if child in projected[i]:
                            located.extend(p for p in props if offset<=p['span'][0]<p['span'][1]<=end)
                        offset=end+1
                    strength=property_strengths(context['query_basis']['cue']['properties'],{i:located}).get(i)
                    if strength is None:
                        del properties[i]
                    else:
                        properties[i]=strength
        rag={}
        for doc in documents:
            for i in doc['member_event_ids']:
                if child_mode:
                    contents=projected[i]
                    if doc.get('content_id') is not None:
                        if not any(c.content_id==doc['content_id'] for c in contents):continue
                    elif contents!=memories[i].event.visible_contents(app.owner_ref):
                        continue
                rag[i]=max(rag.get(i,-1.),raw[doc['event_id']])
        def admitted(ids,lane):
            if child_mode:
                return (memories[i] for i in ids if projected[i])
            return filter_memories(app,(memories[i] for i in ids),visibility,lane=lane)
        candidates={}
        for lane, scores in (('property',properties),('rag',rag),('numeric',numeric)):
            for memory in admitted(scores,lane):
                i=memory.event.event_id
                candidates.setdefault(i,{})[lane]=scores[i]
        for memory in admitted((i for i in recall.recalled_fact_ids if i not in numeric),'numeric'):
            candidates.setdefault(memory.event.event_id,{})
        status=result['status']
        if status=='available':
            coverage=result.get('coverage',{})
            status='unexamined' if coverage.get('vector_unexamined') or coverage.get('rank_unexamined') else 'no_match'
        ranking=memory_rerank.rank_routes(candidates,rag_status=status,
            numeric_status='unavailable' if saved is None else 'not_recalled',
            rag_scale=content.get('method','reranker'), version=ranking_version)
        for row in ranking:
            witness=fragment_matches.get(row['event_id'])
            if witness:
                row['routes']['property']['fragment_match']=witness
        # Missing or zero-strength evidence does not make a positive claim.
        # Keep such rows in the audit. The legacy path retains its replay rules.
        scores={row['event_id']:row['score'] for row in ranking
                if ranking_version!=memory_rerank.STRONGEST_VERSION or row['score']>0}
        occupation=_occupy(context,ranking_version,scores)
        return ranking, occupation

    def _cards(self,context,event_id,validated,*,content_view_version=None,intention_snapshot=None,visibility=None,route_evidence=None,ranked=None,fragment_memory=False,partial_acquisition=False,interaction_sources=False,environment_evidence=False,environment_timeline=False):
        source,recall,memories=validated
        c=thaw_model_data(context);app=self.application;selected=c['result']['selected']
        addresses={};layer_scores={}
        documents=c['selected_sources'];occupation=None
        if ranked is None and c.get('content_score_sources'):
            from .weak_memory_content import select_documents
            # Presence of the new field versions the policy for archived
            # requests even when no rank completed before publication.
            groups=None
            if c['result']['content_scores'].get('exchange_competition'):
                sources=app.runtime.session.profile['desktop_pet_product_v1']['sources']
                anchors={(m.fact.accepted_runtime_ordinal,m.fact.temporal_position_fingerprint):i
                         for i,m in memories.items() if m.event.event_kind=='peer_input'}
                groups={}
                if self.reply_source_key is not None:
                    for i,m in memories.items():
                        if m.event.event_kind!='npc_output' or sources[i]['origin']!='execution':continue
                        parent=anchors.get(self.reply_source_key(sources[i]))
                        if parent is not None:groups[i]=parent
            documents,selected,occupation=select_documents(app,c,memories,recall.recalled_fact_ids,visibility,groups=groups)
        for document in documents:
            score=selected[document['event_id']]
            for i in document['member_event_ids']:
                if 'layer' in document:
                    scores=layer_scores.setdefault(i,{})
                    layer=document['layer']
                    scores[layer]=max(score,scores.get(layer,score))
                addresses[i]=AddressReceipt(event_id=i,status='available',similarity=max(score,addresses[i].similarity) if i in addresses else score,provider=self.provider.identity,
                    model=c['result']['model'],policy_version=VERSION)

        properties={}
        if ranked is None and c.get('property_sources'):
            from petlab_app.writer_visibility import filter_memories
            from .weak_memory_content import _occupy
            records={row['event_id']:row for row in c['property_sources']}
            allowed=filter_memories(app,(memories[i] for i in records),visibility,lane='rag')
            scores={m.event.event_id:(1. if records[m.event.event_id]['predecessor_quotes'] else
                                    records[m.event.event_id]['shared_depth_strength']) for m in allowed}
            selected_properties=_occupy(c,'property_address',scores)
            properties={i:records[i] for i in selected_properties.selected_for_hydration_ids}
        if ranked is None and route_evidence is not None:
            route_evidence.update(
                numeric=set(recall.recalled_fact_ids),
                property=properties,
                rag={i:receipt.to_record() for i,receipt in addresses.items()},
            )
            # Relative RAG competition skips already selected numeric IDs.
            # Preserve positive vector selections that also hit that route.
            for doc in c['selected_sources']:
                for i in doc['member_event_ids']:
                    if i in route_evidence['numeric']:
                        route_evidence['rag'].setdefault(i,{'selected_document_id':doc['event_id'],
                            'similarity':c['result']['selected'][doc['event_id']]})

        # Formal numeric recall alone grants state. The Writer ranking only
        # selects which authorized IDs are hydrated as content.
        canonical=[ActualRecallCanonicalItem(event_id=i,actual_recalled=i in recall.recalled_fact_ids,
            address_receipt=addresses.get(i),content_eligible=True,state_eligible=i in recall.recalled_fact_ids)
            for i in set(recall.recalled_fact_ids)|set(addresses)]
        literal={}
        for row in c.get('literal_address',{}).get('sources',()):
            literal.setdefault(row['event_id'],[]).append(row)
        # Literal addresses authorize the same content read without a vector score.
        ids=sorted(({x.event_id for x in canonical}|set(literal)|set(properties))-set(recall.recalled_fact_ids),
                   key=lambda i:memories[i].fact.accepted_runtime_ordinal)
        if ranked is not None:
            ids=[r['event_id'] for r in ranked if c['document_version'] in CHILD_DOCUMENT_VERSIONS
                 or r['event_id'] not in recall.recalled_fact_ids]
        history=app.runtime._history()
        read=history.authorize_read(reader_ref=app.owner_ref,trigger_fact_id=event_id,
            before_runtime_ordinal=source.fact.accepted_runtime_ordinal,requested_fact_ids=tuple(ids),
            authorization_ref={'rag_content_context_fingerprint':c['fingerprint'],'numeric_recall_fingerprint':recall.fingerprint,
                **({'content_occupation_fingerprint':occupation['fingerprint']} if occupation is not None else {})})
        rows=[]
        for index,i in enumerate(ids,1):
            memory=memories[i];event=memory.event;contents=event.visible_contents(app.owner_ref)
            complete=contents
            if c['document_version'] in CHILD_DOCUMENT_VERSIONS:
                from petlab_app.writer_visibility import memory_contents
                contents=memory_contents(app,memory,visibility)
            partial=contents!=complete
            from model_packages.desktop_pet_v1.content_view import content_view
            text,attrs=content_view(contents,memory_content_attributes(contents,natural=True),version=content_view_version)
            fields={'记忆标记':'R'+str(index),'说话者':event.actor_ref,'对象':list(event.target_refs),
                    '位置':{'domain':event.position.binding.domain_id,'coordinate':event.position.coordinate},'正文':text,'内容属性':attrs}
            from petlab_app.visible_delivery import presentation_fields, source_context
            if partial:
                fields.pop('对象')
                if partial_acquisition and source_context(app,event,interaction_sources=interaction_sources) is not None:
                    # Preserve receipt ownership only for the selected fragments.
                    fields['本次观察者']=app.owner_ref
            else:
                fields=presentation_fields(app,event,fields,interaction_sources=interaction_sources)
            from .intention_delivery import annotation as intention_annotation,FIELD as INTENTION_FIELD
            intention,intention_refs=(None,()) if partial else intention_annotation(app,memory,reader_ref=app.owner_ref,snapshot=intention_snapshot)
            if intention is not None:fields[INTENTION_FIELD]=intention
            annotation=None if partial or fragment_memory in SOURCE_TYPED_LAYOUTS else for_memory(app.runtime.session.profile,memory,app.owner_ref,allow_unresolved_alias=True)
            if annotation is not None:fields[FIELD]=annotation
            if fragment_memory and c['document_version'] in CHILD_DOCUMENT_VERSIONS:
                from .memory_fragments import fragments, evidence_rows, receipt_phases
                fields['片段材料']=evidence_rows(fragments(event,contents,()),phases=receipt_phases(app,event),
                                                   explicit_material=fragment_memory in (*ROLE_MATCH_LAYOUTS, *WEIGHTED_VERSIONS))
            elif 'address_selection' in c and not partial:
                from .relation_annotations import _measurement
                parsed=_measurement(app.runtime.session.profile,memory).get('pet_memory_recall',{})
                if parsed.get('version') in ('desktop-pet-hierarchical-property-recall-v7','desktop-pet-property-address-recall-v8'):
                    fields['语义属性']=[{k:thaw_model_data(v) for k,v in p.items() if k!='span'}
                        for p in self._address_properties(memory)]
            if environment_evidence:
                from .environment_delivery import FIELD as ENVIRONMENT_FIELD, evidence
                environment = evidence(app, event, contents)
                if environment:
                    fields[ENVIRONMENT_FIELD] = environment
                    if environment_timeline and any('采样时刻' in row for row in environment):
                        fields['本次观察者'] = app.owner_ref
            provenance={'selected_memory_ids':[i],'rag_context_fingerprint':c['fingerprint'],
                        'state_eligible':i in recall.recalled_fact_ids}
            if partial:provenance['projected_content_ids']=[content.content_id for content in contents]
            if i in addresses:
                provenance['address_receipt']=addresses[i].to_record()
                if occupation is not None:provenance['content_occupation_fingerprint']=occupation['fingerprint']
            if i in layer_scores:provenance['matched_layer_scores']=layer_scores[i]
            if i in literal:provenance['literal_address_evidence']=literal[i]
            if i in properties:provenance['property_address_evidence']=properties[i]
            rows.append(WriterCard(card_id='memory:local-rag:'+i,lane='memory',kind='addressed_pet_event',source_id=i,fields=fields,
                source_receipt_refs=(memory.fact.fingerprint,c['fingerprint'],read.fingerprint,*intention_refs),
                provenance=provenance,budget_policy='preserve_selection'))
        return tuple(rows)
    def compose_cards(self,context,event_id,numeric_cards,*,content_view_version=None,links=False,visibility=None,intention_snapshot=None,takeover_journal=None,writer_ranking=None,fragment_memory=False,track_content=False,partial_acquisition=False,interaction_sources=False,environment_evidence=False,environment_timeline=False):
        c=thaw_model_data(context)
        validated=self.validate(context,event_id)
        route_evidence={} if 'property_sources' in c else None
        ranked=None
        if c.get('assembly_version')==memory_rerank.UNIFIED_VERSION:
            if writer_ranking is None:
                writer_ranking=self.writer_ranking(c,event_id,visibility=visibility,fragment_memory=fragment_memory)
            ranking_all,occupation=writer_ranking
            chosen=set(occupation.selected_for_hydration_ids)
            ranked=tuple(row for row in ranking_all if row['event_id'] in chosen)
            numeric_cards=tuple(card for card in numeric_cards
                                if set(card.provenance['selected_memory_ids'])<=chosen)
            if c['document_version'] in CHILD_DOCUMENT_VERSIONS:
                numeric_cards=()
            for row in ranked:
                for lane,witness in row['routes'].items():
                    if witness['status']=='scored':
                        route_evidence.setdefault(lane,{})[row['event_id']]=witness
        rag_cards=self._cards(context,event_id,validated,content_view_version=content_view_version,
            intention_snapshot=intention_snapshot,visibility=visibility,route_evidence=route_evidence,ranked=ranked,fragment_memory=fragment_memory,partial_acquisition=partial_acquisition,interaction_sources=interaction_sources,environment_evidence=environment_evidence,environment_timeline=environment_timeline)
        if visibility is not None and ranked is None:
            from petlab_app.writer_visibility import filter_memories
            ids=tuple(i for c in rag_cards for i in c.provenance['selected_memory_ids'])
            allowed={m.event.event_id for m in filter_memories(self.application,(self._source(i) for i in ids),visibility,lane='rag')}
            rag_cards=tuple(c for c in rag_cards if set(c.provenance['selected_memory_ids'])<=allowed)
        cards=(*numeric_cards,*rag_cards)
        # Old sealed Writer requests must retain their original two-block view.
        if c.get('assembly_version') is None:return cards
        source,recall,memories=validated
        if takeover_journal is not None and c['assembly_version'] in (memory_rerank.VERSION,memory_rerank.UNIFIED_VERSION):
            from .takeover_memory import cards as takeover_cards
            selected={i for card in cards for i in card.provenance['selected_memory_ids']}
            carried,carried_memories=takeover_cards(self.application,takeover_journal,
                source,memories,selected,visibility,source_typed=fragment_memory in SOURCE_TYPED_LAYOUTS)
            if carried:
                cards=(*cards,*carried)
                memories={**memories,**carried_memories}
        ids=tuple(sorted((i for card in cards for i in card.provenance['selected_memory_ids']),
                         key=lambda i:memories[i].fact.accepted_runtime_ordinal))
        if len(ids)!=len(set(ids)):raise ValueError('memory union repeats a selected source')
        ranking=() if ranked is None else tuple(row for row in ranked if row['event_id'] in ids)
        if c['assembly_version']==memory_rerank.VERSION:
            authorized=set(ids)
            scores={}
            for doc in c['selected_sources']:
                layer=doc['layer']
                similarity=c['result']['selected'][doc['event_id']]
                for identity in doc['member_event_ids']:
                    if identity in authorized:
                        by_layer=scores.setdefault(identity,{})
                        by_layer[layer]=max(similarity,by_layer.get(layer,similarity))
            if c.get('content_score_sources'):
                scores={card.source_id:dict(card.provenance['matched_layer_scores']) for card in rag_cards
                        if 'matched_layer_scores' in card.provenance}
            sources=self.application.runtime.session.profile['desktop_pet_product_v1']['sources']
            ranking=memory_rerank.rank({i:scores[i] for i in ids if i in scores},sources)
        history=self.application.runtime._history()
        read=history.authorize_read(reader_ref=self.application.owner_ref,trigger_fact_id=event_id,
            before_runtime_ordinal=source.fact.accepted_runtime_ordinal,requested_fact_ids=ids,
            authorization_ref={'rag_content_context_fingerprint':c['fingerprint'],
                               'numeric_recall_fingerprint':recall.fingerprint,'assembly_version':c['assembly_version']})
        from .memory_delivery import ordered_memory_union
        witnesses=None if route_evidence is None else {
            i:{name:(True if name=='numeric' else matches[i])
               for name,matches in route_evidence.items() if i in matches}
            for i in ids}
        merged=ordered_memory_union(cards,memories=memories,source_refs=(read.fingerprint,),
                                   assembly_version=c['assembly_version'],ranking=ranking,route_evidence=witnesses,
                                    ranking_audit={'candidates':ranking_all,'occupation':occupation.receipt} if ranked is not None else None,
                                    track_content=track_content)
        fragment_links=fragment_memory and c['document_version'] in CHILD_DOCUMENT_VERSIONS
        if not merged or not (links or fragment_links):return merged
        from ops.memory_link_history_source import history_memory_link_subcard
        from model_packages.cx_six_seven_v1.memory_link_delivery import with_memory_links
        # A link is kept only when both whole source bodies can be delivered.
        endpoints=None
        if fragment_links:
            endpoints=frozenset(card.source_id for card in rag_cards
                if 'projected_content_ids' not in card.provenance
                and memories[card.source_id].event.contents==memories[card.source_id].event.visible_contents(self.application.owner_ref))
        child=history_memory_link_subcard(history=history,history_read=read,parent_card_id=merged[0].card_id,
            selected_memory_ids=ids,enabled=True,edge_fact_ids=endpoints)
        parent=with_memory_links(merged[0],child)
        if fragment_links and child is not None:
            from dataclasses import replace
            aliases={identity:row['记忆标记'] for identity,row in zip(ids,parent.fields['相关记忆（按发生顺序）'])}
            link_card=parent.subcards[-1]
            fields={'当时召回并参与结算的关联':[' → '.join(aliases[i] for i in edge) for edge in child.related_chains]}
            parent=replace(parent,subcards=(*parent.subcards[:-1],replace(link_card,fields=fields)))
        return (parent,)

    def prime(self):
        app=self.application;history=app.runtime._history();facts=history.facts()
        if not facts or self.provider.status not in ('ready','warming'):return
        source=self._source(facts[-1].fact_id)
        independent=getattr(app,'memory_recall_version',None)==INDEPENDENT_CONFIG
        docs,_=self._documents_for(event=source.event,before_ordinal=source.fact.accepted_runtime_ordinal+1,
                                   history=history,document_version=self._document_version(independent=independent))
        namespace=model_data_fingerprint({'binding':source.fact.session_binding_fingerprint,'owner':app.owner_ref,'scope':source.event.scope_ref})
        self.provider.prime(documents=tuple(docs),namespace=namespace)

    def prime_committed(self,memory):
        """Queue only this newly committed fact's immutable documents."""
        app=self.application
        if (getattr(app,'memory_recall_version',None)!=INDEPENDENT_CONFIG
                or self.provider.status not in ('ready','warming')):
            return
        event,fact=memory.event,memory.fact
        docs,_=self._layered_documents(event,fact.accepted_runtime_ordinal+1,None,
                                      children=True,facts=(fact,),source_typed=True)
        if docs:
            namespace=model_data_fingerprint({'binding':fact.session_binding_fingerprint,
                'owner':app.owner_ref,'scope':event.scope_ref})
            self.provider.prime(documents=tuple(docs),namespace=namespace,incremental=True)
    def close(self):self.provider.close()
