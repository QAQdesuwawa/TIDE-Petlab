"""Budgeted source inspection using the unchanged Full recall operators.

The budget limits coverage, never modifies a score or pretends unseen memories
failed recall. Graph dependencies are read solely to reconstruct a candidate;
they are not automatically added as current recall candidates.
"""
from time import perf_counter
import math
from framework.model_api import model_data_fingerprint
from interaction_extensions.local_memory_source import resolve
from model_packages.cx_six_seven_v1.full_v2_package import CxSixSevenFullV2Package
from . import memory_batch
from model_packages.cx_six_seven_v1.query_budget import scope, QueryBudgetExpired

VERSION='desktop-pet-indexed-memory-query-v1'
SOURCE_VALIDATION_PLAN='incremental-parent-certificates-v1'


def _edge_record(edge):
    evidence=edge.evidence
    return {'lineage_fingerprint':evidence['lineage_fingerprint'],
            'acceptance_fingerprint':evidence['acceptance_fingerprint'],
            'aggregate_stage_output_fingerprint':evidence['aggregate_stage_output_fingerprint'],
            'parent_event_id':edge.parent_fact_id,'child_event_id':edge.child_fact_id,
            'projected_contribution':edge.contribution_payload,'edge_evidence':evidence['edge_evidence']}


def _closure(view,identity,deadline):
    nodes={};edges={};pending=[identity]
    while pending:
        if perf_counter()>=deadline:return None
        node_id=pending.pop()
        if node_id in nodes:continue
        nodes[node_id]=view.node(node_id)
        for edge in view.incoming(node_id):
            edges[edge.edge_id]=_edge_record(edge);pending.append(edge.parent_fact_id)
    ordered=sorted(nodes.values(),key=lambda node:node['accepted_runtime_ordinal'])
    return {'candidate_event_ids':[node['event_id'] for node in ordered],
            'candidate_nodes':ordered,'runtime_lineage_edges':list(edges.values())}


def prepare(package,*,payload,context,replay_coverage=None):
    if set(payload)!={'memory_source_view','query'} or not memory_batch.enabled(context):
        raise ValueError('indexed memory query requires explicit package evidence format')
    query=payload['query']
    if set(query) not in ({'parameters','event_contribution','budget_ms'},
                           {'parameters','settlement','shared_projection','budget_ms'}):
        raise ValueError('indexed memory query fields are invalid')
    budget=query['budget_ms']
    if type(budget)not in (int,float) or not math.isfinite(budget) or budget<=0:
        raise ValueError('memory query requires a positive finite time budget')
    view=resolve(payload['memory_source_view'],context)
    start=perf_counter();deadline=start+budget/1000
    if replay_coverage is not None:
        if (dict(replay_coverage['snapshot_ref'])!=dict(view.reference)
                or type(replay_coverage['examined_count'])is not int
                or not 0<=replay_coverage['examined_count']<=view.count):
            raise ValueError('memory query replay coverage is invalid')
        deadline=float('inf')
    examined=0;pruned=0;candidates=[];next_position=view.count-1
    from .recall_model import select
    grounded_recall=select(context.metadata.get('pet_memory_recall',{}).get('version'))
    grounded=grounded_recall.enabled(context);semantic_pruned=0
    if grounded and dict(payload['memory_source_view'])!=dict(context.metadata[grounded_recall.KEY]['source_view']):
        raise ValueError('grounded memory query source snapshot mismatch')
    current_grounded=grounded_recall.current_projection(package,query,context) if grounded else None
    proof=None
    use_workspace=(replay_coverage is None or replay_coverage.get('workspace_version')=='single-evaluation-rows-v1')
    workspace={};reused=0;prepared_closures=0;direct_count=0
    use_direct=(replay_coverage is None or replay_coverage.get('direct_readout_version')=='validated-source-fresh-phi-v1')
    from .memory_direct_readout import DirectReader
    direct=DirectReader(package,view,context) if use_direct else None
    # Old sealed coverage must replay its old exact inspection plan. Only new
    # queries or explicitly proof-bearing coverage may take this optimization.
    if not grounded and (replay_coverage is None or 'pruning_proof' in replay_coverage):
        from .memory_query_bound import current_upper_bound
        proof=current_upper_bound(package,query,context)
    if proof is not None:
        examined=view.count;pruned=view.count;next_position=-1
    use_time=(not grounded and direct is not None and (replay_coverage is None or replay_coverage.get('phi_time_window_version')=='desktop-pet-phi-time-window-v1'))
    from .memory_time_window import TimeWindow
    window=TimeWindow(package,view,query,context,direct) if use_time and proof is None else None
    time_pruned=0
    incremental=(replay_coverage is None or replay_coverage.get('source_validation_plan')==SOURCE_VALIDATION_PLAN)
    while next_position>=0:
        if replay_coverage is not None and examined>=replay_coverage['examined_count']:break
        if perf_counter()>=deadline:break
        identity=view.identity_at(next_position)
        if grounded:
            source=grounded_recall.source_for(view,identity)
            relevance,_=grounded_recall.association(context.metadata[grounded_recall.KEY]['cue'],source)
            if not relevance:
                examined+=1;pruned+=1;semantic_pruned+=1;next_position-=1
                continue
        expected_time=(None if replay_coverage is None else next((item for item in replay_coverage.get('time_exclusions',()) if item['first_position']<=next_position<=item['last_position']),None))
        if window is not None and (replay_coverage is None or expected_time is not None):
            exclusion=window.exclusion(identity,next_position)
            if exclusion is None and expected_time is not None:
                # Cold recovery must re-establish immutable edge validation,
                # not trust a stored exclusion claim as a source certificate.
                raw_validation=_closure(view,identity,deadline)
                if raw_validation is None:raise ValueError('sealed time exclusion source is unavailable')
                checked=CxSixSevenFullV2Package.prepare_memory_inputs(package,payload=raw_validation,context=context)
                direct.remember(raw_validation,checked)
                exclusion=window.exclusion(identity,next_position)
                if exclusion is None:raise ValueError('sealed time exclusion cannot be reproduced')
            if exclusion is not None:
                examined+=1;pruned+=1;time_pruned+=1;next_position-=1
                continue
        if incremental and direct is not None and proof is None:
            if not direct.ensure(identity,deadline,perf_counter):break
        row=workspace.get(identity) if use_workspace else None
        raw=None
        if row is None and direct is not None:
            row=direct.get(identity)
            if row is not None:direct_count+=1
        if row is None:
            raw=_closure(view,identity,deadline)
            if raw is None:break
        try:
            with scope(deadline,perf_counter):
                if row is None:
                    prepared=CxSixSevenFullV2Package.prepare_memory_inputs(package,payload=raw,context=context)
                    prepared_closures+=1
                    if direct is not None:direct.remember(raw,prepared)
                    if use_workspace:
                        # Same immutable source view, timestamp and ordinal.
                        # Source-reference receipts contain no closure-wide order.
                        # Dependencies remain candidates only when the outer
                        # inspection plan explicitly reaches their identities.
                        workspace.update((item['event_id'],item) for item in prepared['memory_inputs']['candidates'])
                    row=next(item for item in prepared['memory_inputs']['candidates'] if item['event_id']==identity)
                else:
                    reused+=1
                current={key:value for key,value in query.items() if key!='budget_ms'}
                request={'candidate_event_ids':[identity],'historical_live':[row['recall_input']],**current}
                if grounded:
                    result=grounded_recall.recall(package,payload=request,context=context,rows=[row],current=current_grounded)
                elif 'shared_projection' in current:
                    from model_packages.cx_six_seven_v1.autonomous_recall_package import CxSixSevenAutonomousRecallPackage
                    result=CxSixSevenAutonomousRecallPackage.recall_history(package,payload=request,context=context)
                else:
                    result=CxSixSevenFullV2Package.recall_history(package,payload=request,context=context)

        except QueryBudgetExpired:
            break
        examined+=1;next_position-=1
        if result['recall_decision']['recalled_event_ids']:candidates.append(row)
        else:pruned+=1
    candidates.sort(key=lambda row:view.index.by_id[row['event_id']])
    coverage={'version':VERSION,'snapshot_ref':dict(view.reference),'budget_ms':budget,
              'total_count':view.count,'examined_count':examined,'pruned_count':pruned,
              'candidate_count':len(candidates),'unexamined_count':view.count-examined,
              'next_position':next_position,'budget_exhausted':next_position>=0,
              'current_source_kind':'shared_tick' if 'shared_projection' in query else 'event',
              'current_source_fingerprint':model_data_fingerprint(query.get('event_contribution',query.get('shared_projection'))),
              'candidate_math':'unchanged-full-recall','unexamined_is_negative':False}
    if grounded:coverage.update(recall_model=grounded_recall.VERSION,semantic_pruned_count=semantic_pruned,candidate_math=grounded_recall.VERSION)
    if incremental:
        coverage['source_validation_plan']=SOURCE_VALIDATION_PLAN
    if use_time:
        coverage.update(phi_time_window_version='desktop-pet-phi-time-window-v1',time_pruned_count=time_pruned,
            time_exclusions=[] if window is None else window.exclusions)
    if use_direct:
        coverage.update(direct_readout_version='validated-source-fresh-phi-v1',direct_recomputed_rows=direct_count)
    if use_workspace:
        coverage.update(workspace_version='single-evaluation-rows-v1',workspace_reused_rows=reused,workspace_prepared_closures=prepared_closures)
    if proof is not None:
        coverage.update(pruning_proof=proof,analytically_pruned_count=view.count,operator_examined_count=0)
    if replay_coverage is not None and use_direct:
        # Cache-hit counters witness the original execution, not a numerical
        # input. Recovery may begin cold or warm; preserve those sealed facts
        # after checking their bounds, while recomputing every numerical row.
        keys=('workspace_reused_rows','workspace_prepared_closures','direct_recomputed_rows')
        for key in keys:
            if key not in replay_coverage:continue
            value=replay_coverage[key]
            if type(value)is not int or not 0<=value<=examined:
                raise ValueError('invalid sealed query-work counter')
            coverage[key]=value
    result={'memory_inputs':{'version':'cx67-full-memory-input-product-v1',
            'candidate_event_ids':[row['event_id'] for row in candidates], 'candidates':candidates,
            'state_commit_authority':False,'memory_commit_authority':False},'memory_coverage':coverage}
    return memory_batch.pack(result,payload,context,
        parameter_version=package._package_params('memory_forward_readout')['version'])


def replay(package,*,payload,context,prepared):
    """Recheck the signed coverage interval, not a new machine-speed budget."""
    result=prepare(package,payload=payload,context=context,replay_coverage=prepared['memory_coverage'])
    if result!=prepared:raise ValueError('memory query replay differs from signed coverage or values')
    return result
