"""Source-owned static validation certificates; fresh Phi at every query.

Original Full preparation or incremental original-operator validation creates
a source certificate. It binds
an immutable node, exact incoming edges, parameter versions and the evaluated
source frontier. Cache hits never return a previous live vector.
"""
from collections import OrderedDict
from .memory_time_window import profile_for_node
from framework.model_api import model_data_fingerprint
from model_packages.cx_six_seven_v1.memory_projection import CanonicalMemoryProjection
from model_packages.cx_six_seven_v1.memory_numeric_contracts import MemoryInfluenceEdge,MemoryEventRef,NeutralMemoryReadout
from model_packages.cx_six_seven_v1.memory_forward_readout import _intrinsic_live,_propagate_edge_delta,_parent_scale,_norm,forward_live_readout_ref,FORWARD_MEMORY_READOUT_VERSION
from model_packages.cx_six_seven_v1.memory_runtime_order import order_record
from model_packages.cx_six_seven_v1.full_v2_package import _evaluation_time_ms
from model_packages.cx_six_seven_v1.interaction_geometry import ROOT_ORDER,relationship_transition
from model_packages.cx_six_seven_v1.continuous_root_projection import project_continuous_roots
VERSION='validated-source-fresh-phi-v1'

class Lineage:
    __slots__=('parents','edges')
    def __init__(self,parents,edges):self.parents=tuple(parents);self.edges=tuple(edges)
    def values(self):
        pending=[self];seen=set();edges=set()
        while pending:
            value=pending.pop()
            if isinstance(value,tuple):edges.update(value);continue
            if id(value) in seen:continue
            seen.add(id(value));edges.update(value.edges);pending.extend(value.parents)
        return tuple(sorted(edges))

def lineage_values(value):return value.values() if isinstance(value,Lineage) else value


class DirectReader:
    def __init__(self,package,view,context):
        self.package=package;self.view=view;self.context=context
        self.at=_evaluation_time_ms(context);self.ordinal=context.runtime_ordinal
        self.parameter_key=model_data_fingerprint({'forward':package._package_params('memory_forward_readout'),'projection':package._package_params('shared_projection')})
        if not hasattr(view.index,'_direct_readout_certificates'):view.index._direct_readout_certificates=OrderedDict()
        self.cache=view.index._direct_readout_certificates
    def key(self,identity):
        return (identity,id(self.view.node(identity)),tuple(e.fingerprint for e in self.view.incoming(identity)),self.parameter_key)
    def remember(self,raw,prepared):
        from .memory_index_query import _edge_record
        rows={r['event_id']:r for r in prepared['memory_inputs']['candidates']}
        for node in raw['candidate_nodes']:
            identity=node['event_id'];key=self.key(identity)
            if key in self.cache:continue
            projection=CanonicalMemoryProjection.from_record(node['projection'])
            evolution=node['evolution_binding'];times={evolution['root_time_parameters']['version']:evolution['root_time_parameters']};bindings={};edges=[]
            for source in self.view.incoming(identity):
                record=_edge_record(source);e=record['edge_evidence']['evolution_binding'];fp=e['fingerprint']
                bindings[fp]={'version':e['version'],'binding_ref':fp,'constant_binding':e['constant_binding'],
                    'root_time_operator_version':e['root_time_operator_version'],'root_time_parameter_version':e['root_time_parameter_version']}
                times[e['root_time_parameters']['version']]=e['root_time_parameters']
                edges.append(MemoryInfluenceEdge(edge_id='runtime-edge:'+model_data_fingerprint(record),parent_node_id=record['parent_event_id'],
                    actual_delta=record['projected_contribution'],parent_live_readout_ref=record['edge_evidence']['parent_live_readout_ref'],
                    p4_receipt_ref=record['aggregate_stage_output_fingerprint'],propagation_binding_ref=fp,
                    formula_version='runtime-full-memory-lineage-adapter-v1',receipt_refs=(record['lineage_fingerprint'],)))
            edges.sort(key=lambda edge:edge.edge_id)
            receipt=rows[identity]['influence_input']['receipt']
            if tuple(e.edge_id for e in edges)!=tuple(receipt['incoming_edge_ids']):raise ValueError('validated source edge set differs')
            self.cache[key]={'projection':projection,'edges':tuple(edges),'times':times,'bindings':bindings,
                'lineage':tuple(receipt['lineage_edge_ids']),'validated_at':self.at,'validated_ordinal':self.ordinal,
                'node_ordinal':node['accepted_runtime_ordinal']}
            while len(self.cache)>4096:self.cache.popitem(last=False)
    def compile_node(self,identity):
        from .memory_index_query import _edge_record
        node=self.view.node(identity)
        # Validates canonical roots, frozen Phi and forward parameter bindings.
        profile_for_node(self.package,node,self.view.incoming(identity))
        projection=CanonicalMemoryProjection.from_record(node['projection'])
        if projection.event.committed_at_ms>self.at or node['accepted_runtime_ordinal']>self.ordinal:
            raise ValueError('future source is not visible')
        evolution=node['evolution_binding'];times={evolution['root_time_parameters']['version']:evolution['root_time_parameters']};bindings={};edges=[]
        for source in self.view.incoming(identity):
            record=_edge_record(source);e=record['edge_evidence']['evolution_binding'];fp=e['fingerprint']
            bindings[fp]={'version':e['version'],'binding_ref':fp,'constant_binding':e['constant_binding'],
                'root_time_operator_version':e['root_time_operator_version'],'root_time_parameter_version':e['root_time_parameter_version']}
            times[e['root_time_parameters']['version']]=e['root_time_parameters']
            edges.append(MemoryInfluenceEdge(edge_id='runtime-edge:'+model_data_fingerprint(record),parent_node_id=record['parent_event_id'],
                actual_delta=record['projected_contribution'],parent_live_readout_ref=record['edge_evidence']['parent_live_readout_ref'],
                p4_receipt_ref=record['aggregate_stage_output_fingerprint'],propagation_binding_ref=fp,
                formula_version='runtime-full-memory-lineage-adapter-v1',receipt_refs=(record['lineage_fingerprint'],)))
        edges.sort(key=lambda edge:edge.edge_id)
        value={'projection':projection,'edges':tuple(edges),'times':times,'bindings':bindings,'node_ordinal':node['accepted_runtime_ordinal'],
               'validated_at':self.at,'validated_ordinal':self.ordinal}
        # Enforce the versioned source registry, not just claimed version names.
        catalog=getattr(self.view.index,'_numeric_time_catalog',None)
        if catalog is None:catalog={};self.view.index._numeric_time_catalog=catalog
        for version,params in times.items():
            key=(self.parameter_key,version);fingerprint=model_data_fingerprint(params)
            if key in catalog and catalog[key]!=fingerprint:raise ValueError('root time parameter version conflicts')
            catalog[key]=fingerprint
        _intrinsic_live(projection,projection.event.committed_at_ms,times)
        for edge in edges:_propagate_edge_delta(edge,age_ms=0,bindings=bindings,time_packages=times)
        return value
    def numeric(self,source,at):
        projection=source['projection'];params=self.package._package_params('memory_forward_readout')
        intrinsic,receipt=_intrinsic_live(projection,at,source['times'])
        incoming=[_propagate_edge_delta(edge,age_ms=at-projection.event.committed_at_ms,bindings=source['bindings'],time_packages=source['times'])[0] for edge in source['edges']]
        pre={axis:intrinsic[axis]+sum(v[axis] for v in incoming) for axis in ROOT_ORDER}
        radius=float(params['bound_radius']);factor=max(1.,_norm(pre)/radius)
        return {axis:pre[axis]/factor for axis in ROOT_ORDER},receipt
    def ensure(self,identity,deadline,clock):
        pending=[(identity,False)];visiting=set();local={}
        while pending:
            if clock()>=deadline:return False
            node_id,ready=pending.pop();key=self.key(node_id)
            cached=local.get(key) or self.cache.get(key)
            if cached is not None:
                local[key]=cached;continue
            if not ready:
                if node_id in visiting:raise ValueError('forward source graph must be acyclic')
                visiting.add(node_id);pending.append((node_id,True))
                for edge in reversed(self.view.incoming(node_id)):
                    parent_key=self.key(edge.parent_fact_id)
                    if parent_key not in local and parent_key not in self.cache:pending.append((edge.parent_fact_id,False))
                continue
            source=self.compile_node(node_id);projection=source['projection'];parents=[]
            for edge in source['edges']:
                parent_key=self.key(edge.parent_node_id);parent=local.get(parent_key) or self.cache.get(parent_key)
                if parent is None:raise ValueError('parent source certificate missing')
                child_at=projection.event.committed_at_ms;parent_at=parent['projection'].event.committed_at_ms
                if parent_at>child_at or parent['node_ordinal']>=source['node_ordinal']:raise ValueError('influence parent must be strictly prior')
                ordinal=source['node_ordinal']-1;version=self.package._package_params('memory_forward_readout')['version']
                expected=forward_live_readout_ref(edge.parent_node_id,child_at,version,runtime_ordinal=ordinal)
                legacy=forward_live_readout_ref(edge.parent_node_id,child_at,version)
                if edge.parent_live_readout_ref!=expected and not(parent_at<child_at and edge.parent_live_readout_ref==legacy):raise ValueError('edge parent live readout ref does not match replay')
                vector,_receipt=self.numeric(parent,child_at)
                _parent_scale(vector,edge.actual_delta)
                parents.append(parent['lineage'])
            source['lineage']=Lineage(parents,[e.edge_id for e in source['edges']])
            local[key]=source;self.cache[key]=source
            while len(self.cache)>4096:self.cache.popitem(last=False)
            visiting.remove(node_id)
        return self.key(identity) in self.cache
    def get(self,identity):
        key=self.key(identity);source=self.cache.get(key)
        if source is None or self.at<source['projection'].event.committed_at_ms or self.ordinal<source['node_ordinal']:return None
        self.cache.move_to_end(key)
        projection=source['projection'];params=self.package._package_params('memory_forward_readout')
        live,receipt=self.numeric(source,self.at)
        radius=float(params['bound_radius']);lineage=lineage_values(source['lineage'])
        readout=NeutralMemoryReadout(event=MemoryEventRef(identity,projection.event.committed_at_ms),as_of_ms=self.at,
            live_vector=live,magnitude=_norm(live)/radius,formula_version=FORWARD_MEMORY_READOUT_VERSION,
            lineage=(identity,*lineage),receipt={'readout_ref':forward_live_readout_ref(identity,self.at,params['version'],runtime_ordinal=self.ordinal),
                'intrinsic_term':{'phi_before_event':receipt['phi_before_event']},'lineage_edge_ids':lineage,
                'incoming_edge_ids':tuple(edge.edge_id for edge in source['edges']),'runtime_order':order_record(source['node_ordinal']),
                'evaluation_runtime_ordinal':self.ordinal})
        before=receipt['phi_before_event'];after={axis:before[axis]+live[axis] for axis in ROOT_ORDER}
        projected=project_continuous_roots(transition=relationship_transition(before,after),constant_binding=projection.constant_binding,params=self.package._package_params('shared_projection'))
        return {'event_id':identity,'recall_input':{'event_id':identity,'evaluation_time_ms':self.at,'live_readout_ref':readout.receipt['readout_ref'],
            'temporal_position_fingerprint':self.context.temporal_position.fingerprint,'projection':projected.to_record()},'influence_input':readout.to_record()}
