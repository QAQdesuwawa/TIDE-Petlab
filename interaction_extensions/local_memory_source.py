"""Read-only, scoped references to already accepted numeric memory sources.

No math, recall ranking, persistence, clock advancement or write authority.
The host registers a validated History snapshot; packages can only read its
opaque node records. Existing records are shared, new commits extend the view.
"""
from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock
from itertools import islice
from framework.model_api import model_data_fingerprint, freeze_model_payload

VERSION='local-committed-memory-source-v1'
_LOCK=RLock()
_INDEXES=OrderedDict()
_VIEWS=OrderedDict()


class SourceIndex:
    def __init__(self,history,session_id,owner_ref):
        self.session_id=session_id;self.owner_ref=owner_ref
        self.binding=history.session_binding_fingerprint
        self.facts=[];self.by_id={};self.node_cache={};self.roots=[]
        self.edges=[];self.incoming={}
        self.sync(history)

    def sync(self,history):
        count=len(self.facts)
        if count and (len(history._facts)<count or history._fact_roots[count]!=self.roots[count]):
            raise ValueError('memory source history fork requires independent index')
        # A verified rolling prefix root binds the already accepted facts.
        # Fetch only the newly appended dictionary tail, in occurrence order.
        added=len(history._facts)-count
        identities=list(islice(reversed(history._facts),added))
        for identity in reversed(identities):
            fact=history._facts[identity]
            fact.verify_integrity()
            if self.owner_ref not in fact.visible_to_refs:
                raise ValueError('memory source contains another owner')
            self.by_id[fact.fact_id]=len(self.facts);self.facts.append(fact)
            # Validate each newly accepted opaque node envelope once, including
            # cold recovery. A later universal rejection must not mask corrupt
            # sources simply because it does not need a numerical DAG readout.
            self.node(fact.fact_id)
        self.roots.extend(history._fact_roots[len(self.roots):])
        edges=history.influence_edges()
        # Edges are immutable records. A changed prefix invalidates this index;
        # no supplied fingerprint alone can stand in for accepted history.
        if len(edges)<len(self.edges) or tuple(edges[:len(self.edges)])!=tuple(self.edges):
            raise ValueError('memory source edge prefix changed')
        for edge in edges[len(self.edges):]:
            self.edges.append(edge);self.incoming.setdefault(edge.child_fact_id,[]).append((len(self.edges)-1,edge))

    def node(self,identity):
        index=self.by_id[identity];fact=self.facts[index]
        cached=self.node_cache.get(identity)
        if cached is None:
            from ops.runtime_full_memory_node_product import RuntimeFullMemoryNodeProduct
            node=RuntimeFullMemoryNodeProduct.from_record(fact.model_payload['runtime_full_memory_node'])
            if (node.event_id!=identity or node.owner_participant_id!=self.owner_ref
                    or node.session_binding_fingerprint!=self.binding
                    or node.temporal_position_fingerprint!=fact.temporal_position_fingerprint
                    or node.accepted_runtime_ordinal!=fact.accepted_runtime_ordinal):
                raise ValueError('memory source node differs from accepted fact')
            cached=freeze_model_payload({'event_id':identity,'accepted_runtime_ordinal':node.accepted_runtime_ordinal,
                                        'projection':node.node_projection,'evolution_binding':node.node_evolution_binding})
            self.node_cache[identity]=cached
        return cached


@dataclass(frozen=True)
class SourceView:
    index: SourceIndex
    count: int
    edge_count: int
    reference: object

    def identity_at(self,position):
        if type(position)is not int or not 0<=position<self.count:raise ValueError('memory view position is outside snapshot')
        return self.index.facts[position].fact_id

    def node(self,identity):
        position=self.index.by_id.get(identity,self.count)
        if position>=self.count:raise ValueError('memory view cannot expose a future node')
        return self.index.node(identity)

    def incoming(self,identity):
        self.node(identity)
        return tuple(edge for position,edge in self.index.incoming.get(identity,()) if position<self.edge_count)


def register(history,*,session_id,owner_ref):
    key=(session_id,history.session_binding_fingerprint,owner_ref)
    with _LOCK:
        index=_INDEXES.get(key)
        if index is None:
            index=SourceIndex(history,session_id,owner_ref);_INDEXES[key]=index
        else:
            try:index.sync(history)
            except ValueError:
                index=SourceIndex(history,session_id,owner_ref);_INDEXES[key]=index
        _INDEXES.move_to_end(key)
        while len(_INDEXES)>4:_INDEXES.popitem(last=False)
        count=len(index.facts)
        ref={'version':VERSION,'session_id':session_id,'binding':index.binding,'owner_ref':owner_ref,
             'count':count,'edge_count':len(index.edges),'facts_root':index.roots[count],
             'edges_fingerprint':model_data_fingerprint([edge.fingerprint for edge in index.edges])}
        ref['fingerprint']=model_data_fingerprint(ref)
        ref=freeze_model_payload(ref);view=SourceView(index,count,len(index.edges),ref)
        _VIEWS[ref['fingerprint']]=view
        while len(_VIEWS)>64:_VIEWS.popitem(last=False)
        return view


def resolve(reference,context):
    with _LOCK:view=_VIEWS.get(reference.get('fingerprint'))
    if (view is None or dict(reference)!=dict(view.reference)
            or reference['session_id']!=context.session_id or reference['owner_ref']!=context.subject_ref):
        raise ValueError('unknown or foreign read-only memory source')
    if view.count and view.index.facts[view.count-1].accepted_runtime_ordinal>context.runtime_ordinal:
        raise ValueError('memory source exceeds current formal ordinal')
    return view


def restore_reference(history,reference):
    """Host-only rebuild of an old scoped view after process recovery."""
    current=register(history,session_id=reference['session_id'],owner_ref=reference['owner_ref'])
    index=current.index;count=reference['count'];edge_count=reference['edge_count']
    if (type(count)is not int or not 0<=count<=len(index.facts)
            or type(edge_count)is not int or not 0<=edge_count<=len(index.edges)):
        raise ValueError('restored memory view bounds are invalid')
    expected={**dict(current.reference),'count':count,'edge_count':edge_count,
              'facts_root':index.roots[count],
              'edges_fingerprint':model_data_fingerprint([e.fingerprint for e in index.edges[:edge_count]])}
    expected.pop('fingerprint');expected['fingerprint']=model_data_fingerprint(expected)
    if expected!=dict(reference):raise ValueError('restored memory source snapshot mismatch')
    view=SourceView(index,count,edge_count,freeze_model_payload(expected))
    with _LOCK:_VIEWS[expected['fingerprint']]=view
    return view
