"""Use the existing Tick grant and the same private intention settlement."""
from dataclasses import replace
from model_packages.cx_six_seven_v1 import intention_facts
from .intention_source import enabled,ingest
from .owned_journals import publish_session


def verify_delivery(app,delivery):
    from framework.model_api import thaw_model_data
    from product_adapters.desktop_pet_v1.settlement_view import current_projection
    from .intention_continuation import validate_planning
    view=delivery['intention_planning_view']
    projection=current_projection(app,record=view,historical=True,
                                  committed_remainders=delivery['before_state']['committed_remainders'])
    memory=validate_planning(app,delivery['event_id'],projection.context.event_ref)
    position=view['acceptance']['position']
    if (memory.fact.accepted_runtime_ordinal!=position['runtime_ordinal']
            or memory.fact.temporal_position_fingerprint!=position['position']['fingerprint']
            or projection.shared_projection_execution.receipt.output_fingerprint!=delivery['shared_output_fingerprint']
            or projection.semantic_resource_execution.receipt.output_fingerprint!=delivery['semantic_output_fingerprint']
            or thaw_model_data(projection.semantic_resource_execution.payload)!=delivery['semantic_payload']):
        raise ValueError('idle intention planning delivery differs')
    return position['runtime_ordinal'],position['position']['fingerprint']


def settle(product,tick_id):
    app=product.application
    if not enabled(app):return
    from .idle_opening import source
    tick,grant=source(app,tick_id)
    if grant.authorization is None:return
    key=intention_facts.VERSION
    if tick_id not in app.runtime.session.profile.get(key,{}):
        rows=dict(app.runtime.session.profile.get(key,{}))
        rows[tick_id]=intention_facts.form(owner=app.owner_ref,source_ref=tick_id,
            grant_ref=tick.row['fingerprint'],at_ms=tick.row['source_snapshot']['position']['position']['coordinate'],eligible=True)
        publish_session(app,replace(app.runtime.session,profile={**app.runtime.session.profile,key:rows}))
    return ingest(app,source_ref=tick_id,coordinate=app.runtime.session.position.position.coordinate,
                  measure=product.semantic.measure)


def reply(product,tick_id,**kwargs):
    from .intention_progress import planned,actual,stopped
    app=product.application
    settle(product,tick_id)
    from .intention_wait import QualifiedTakeover
    try:
        result=product._reply(tick_id,on_prepared=lambda prepared:planned(app,tick_id,prepared,writer=product.writer),**kwargs)
    except QualifiedTakeover as takeover:
        supersede(app,tick_id,takeover.deliveries)
        return None
    except Exception as error:
        from .live_execution import PlaybackSettlementPending
        if isinstance(error,PlaybackSettlementPending):raise
        stopped(app,tick_id,'technical_failure')
        raise
    if result is not None:actual(app,tick_id,app._product()['sources'][result.memory.event.event_id])
    stopped(app,tick_id,'executed' if result is not None else 'quiet' if product.last_quiet_realization is not None else 'cancelled')
    return result


def supersede(app,tick_id,deliveries):
    from .intention_progress import stopped
    from .idle_opening import journal,publish
    stopped(app,tick_id,'pending',superseded_by=deliveries)
    data=journal(app);data['pending_tick']=None
    data['outcomes'][tick_id]={'status':'pending','superseded_by':list(deliveries)}
    publish(app,data)


def recover_waiting_input(app,tick_id,event_id,delivery_id):
    """Resolve a proven failed wait after the new source has formally settled."""
    from .idle_opening import journal,source
    from .intent_history import FACTS_KEY
    from .intention_progress import PROOFS
    from .intention_continuation import validate_planning,base_planning_id
    from .retained_intention import head,retain
    from .peer_reaction import assess
    data=journal(app)
    if data['pending_tick']!=tick_id or tick_id in data['outcomes']:
        raise ValueError('idle recovery intention is not pending')
    tick,grant=source(app,tick_id)
    chain=app.runtime.session.profile.get(FACTS_KEY,{}).get(tick_id,())
    proof=app.runtime.session.profile.get(PROOFS,{}).get(chain[-1]['evidence_ref'],{}) if chain else {}
    if grant.authorization is None or not intention_facts.technical_interruption(chain,proof):
        raise ValueError('idle recovery lacks technical interruption')
    parent=validate_planning(app,tick_id,head(app,tick_id) or base_planning_id(app,tick_id))
    current=app.runtime.memory_instance(event_id)
    if current.fact.accepted_runtime_ordinal!=parent.fact.accepted_runtime_ordinal+1:
        raise ValueError('idle recovery input is not adjacent to interrupted plan')
    new=assess(app,event_id)
    if new['eligible']:supersede(app,tick_id,(delivery_id,))
    else:retain(app,tick_id,event_id)
