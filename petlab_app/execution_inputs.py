"""Bind an input's existing qualification to the effect active when it arrived."""
from dataclasses import replace
from .peer_reaction import _signed,_verify,KEY as PEER
from .owned_journals import publish_session

KEY='cx67-live-execution-inputs-v1'
SPEECH_REVISION='settled-relationship-speech-revision-v1'


def support_reversed(supporting,anchor,current):
    positive={key:value for key,value in supporting.items() if value>0}
    # Losing the small increment that first opened a turn does not by itself
    # extinguish an utterance already in progress. The settled exchange drive
    # must actually have no positive channel left; explicit refusal is handled
    # separately by _current_revision.
    return bool(positive) and all(current[key]<=0 for key in positive)


def _opening_channels(app,source_ref):
    from .idle_opening import is_idle_tick,source
    if is_idle_tick(app,source_ref):
        tick,_=source(app,source_ref)
        return tick.row['grant']['shared_projection_output']['shared_projection']['receipt']
    node=app.runtime.memory_instance(source_ref).fact.model_payload['runtime_full_memory_node']['node_projection']
    return _channel_evidence(node)


def pending_revision(app,opening_source,event_id):
    """Use the same source-bound decision before and during playback."""
    from .destinations import destination
    target=destination(app,opening_source)
    if target is None:return None
    return speech_revision(app,event_id,target['primary'],opening_source=opening_source)


def speech_revision(app,event_id,peer,*,opening_source=None):
    """Read the accepted transition on the edge that owns the pending speech."""
    from model_packages.cx_six_seven_v1.interaction_geometry import relationship_transition
    from model_packages.desktop_pet_v1 import peer_opening
    from ops.runtime_full_memory_node_product import RuntimeFullMemoryNodeProduct

    memory=app.runtime.memory_instance(event_id)
    event,fact=memory.event,memory.fact
    if (event.event_kind!='peer_input' or event.actor_ref!=peer or event.owner_ref!=app.owner_ref
            or not any(c.kind=='speech' and not c.private and app.owner_ref in c.recipient_refs
                       for c in event.contents)):
        return None
    source=app._product()['sources'][event_id]
    if source.get('origin')!='input':
        return None
    app._verify_source(event)
    record=fact.model_payload.get('runtime_full_memory_node')
    if record is None:
        return None
    # Ordinary spoken peer input is settled on its actor edge by the package.
    node_product=RuntimeFullMemoryNodeProduct.from_record(record)
    if (node_product.event_id!=event_id or node_product.owner_participant_id!=app.owner_ref
            or node_product.acceptance_fingerprint!=fact.settlement_ref['acceptance_fingerprint']):
        raise ValueError('speech revision differs from its accepted event')
    node=node_product.node_projection
    transition=relationship_transition(node['x_before'],node['x_after'])
    before=transition['before']['values']['R_net']
    after=transition['after']['values']['R_net']
    change=transition['geometry_delta']['R_net']
    prior_attraction=transition['before']['roots']['V_attr']
    use=source.get('measurement',{}).get(peer_opening.KEY)
    refuses=(use is not None and use.get('kind')=='refuse_continuation'
             and peer_opening.matches(use,event))
    if opening_source is not None:
        return _current_revision(app,event,source,fact,peer,node_product,opening_source,use)
    # Preserve verification of previously archived v1 numeric receipts.
    reversal=(prior_attraction>0.0 and before>=0.0 and after<0.0
              and -change>=prior_attraction)
    if not refuses and not reversal:
        return None
    return {'kind':SPEECH_REVISION,'input_fact':fact.fingerprint,
            'edge':[app.owner_ref,peer],'memory_node':node_product.fingerprint,
            'before':before,'after':after,'change':change,'prior_attraction':prior_attraction,
            'cause':'refuse_continuation' if refuses else 'approach_to_withdrawal'}


def _channel_evidence(node):
    from model_packages.cx_six_seven_v1.continuous_root_projection import (
        CONTINUOUS_ROOT_PROJECTION_B_V1, project_continuous_roots,
    )
    from model_packages.cx_six_seven_v1.interaction_geometry import relationship_transition
    return project_continuous_roots(
        transition=relationship_transition(node['x_before'],node['x_after']),
        constant_binding=node['constant_binding'],
        params=CONTINUOUS_ROOT_PROJECTION_B_V1).to_record()['receipt']


def _exchange_basis(app,source_ref):
    from .peer_reaction import KEY,_verify
    assessment=app.runtime.session.profile.get(KEY,{}).get('assessments',{}).get(source_ref)
    if assessment is not None:
        _verify(assessment)
        return assessment.get('speech_use_assessment',{}).get('exchange_evidence',{}),assessment['fingerprint']
    return {},None


def _opening_evidence(app,source_ref,peer,*,exchange_basis=None):
    from .idle_opening import is_idle_tick,source
    if is_idle_tick(app,source_ref):
        tick,_=source(app,source_ref)
        if tick.row.get('participant_destination',{}).get('primary')!=peer:return None
        grant=tick.row['grant']
        receipt=grant['shared_projection_output']['shared_projection']['receipt']
        channels=receipt['channel_conditions']
        weights=receipt['effective_constants']
        return {key:max(0.0,weights[key]*channels[key]['after']) for key in ('a1','a2')},grant['fingerprint']
    memory=app.runtime.memory_instance(source_ref)
    if memory.event.actor_ref!=peer:return None
    record=memory.fact.model_payload.get('runtime_full_memory_node')
    if record is None:return None
    assessed,fingerprint=exchange_basis if exchange_basis is not None else _exchange_basis(app,source_ref)
    if assessed:
        return {key:max(0.0,value['weighted_level']-value['reference_level'])
                for key,value in assessed.items() if value['supports_reply']},fingerprint
    receipt=_channel_evidence(record['node_projection'])
    weights=receipt['effective_constants']
    channels=receipt['channel_conditions']
    return {key:max(0.0,weights[key]*channels[key]['change']) for key in ('a1','a2')},memory.fact.fingerprint


def _exchange_support_reversed(assessed,current):
    from model_packages.desktop_pet_v1.peer_opening import supports_exchange_reply
    bound=[key for key,row in assessed.items() if row['supports_reply']]
    for key in bound:
        row=assessed[key]
        change=row['weighted_change']+(current[key]-row['weighted_level'])
        if supports_exchange_reply(current[key],row['reference_level'],change):
            return False
    return bool(bound)


def _current_revision(app,event,source,fact,peer,node,opening_source,use):
    from .participant_input import ADDRESSED_SPEECH_VERSION,NPC_VERSIONS,validate
    from model_packages.desktop_pet_v1 import peer_opening

    input_source=source['evidence'].get('input_source',{})
    kind=(use['kind'] if use is not None and peer_opening.matches(use,event) else None)
    explicit=(kind=='refuse_continuation'
              and input_source.get('version') in {*NPC_VERSIONS,ADDRESSED_SPEECH_VERSION})
    supporting={}
    adverse={}
    anchor=None
    if not explicit:
        exchange_basis=_exchange_basis(app,opening_source)
        basis=_opening_evidence(app,opening_source,peer,exchange_basis=exchange_basis)
        if basis is None:return None
        supporting,anchor=basis
        receipt=_channel_evidence(node.node_projection)
        weights=receipt['effective_constants']
        channels=receipt['channel_conditions']
        opening=_opening_channels(app,opening_source)
        # The comparison concerns a settled psychological change, not a change
        # in its measuring scale. Different epochs require a fresh basis; they
        # cannot supply numeric evidence for cancelling this old intention.
        comparable=('operator_version','parameter_version','constant_binding','effective_constants')
        if any(key not in opening or key not in receipt or opening[key]!=receipt[key]
               for key in comparable):
            return None
        initial={key:opening['effective_constants'][key]*opening['channel_conditions'][key]['after']
                 for key in supporting}
        current={key:weights[key]*channels[key]['after'] for key in ('a1','a2')}
        adverse={key:max(0.0,initial[key]-current[key]) for key in supporting}
        # Keep independent channel evidence. One weakly supported channel
        # cannot cancel an opening still supported by another channel.
        exchange,_=exchange_basis
        reversed_basis=(_exchange_support_reversed(exchange,current) if exchange
                        else support_reversed(supporting,initial,current))
        if not reversed_basis:
            return None
    if not input_source:return None
    _,artifact=validate(app,input_source,event.contents,event_id=event.event_id,return_artifact=True)
    if artifact['delivery']['delivery_scope']!='addressed':return None
    return {'kind':SPEECH_REVISION,'input_fact':fact.fingerprint,'edge':[app.owner_ref,peer],
            'memory_node':node.fingerprint,'cause':'explicit_stop' if explicit else 'opening_support_reversed',
            'input_at_ms':artifact['source']['event']['committed_at_ms'],'opening_source_ref':opening_source,
            'opening_evidence_ref':anchor,'opening_support':supporting,'adverse_change':adverse}


def remember(app,action_id,event_id):
    from .live_execution import accepts_input,recovery_projection
    entry=app._product()['executions'].get(action_id,{})
    if not entry or not (accepts_input(app,action_id,entry) or recovery_projection(app,event_id,action_id)):
        raise ValueError('input was not assessed during this live execution')
    fact=app.runtime.memory_instance(event_id).fact
    grant=app.runtime.session.profile.get(PEER,{}).get('assessments',{}).get(event_id)
    if grant is None:raise ValueError('live input lacks formal assessment')
    _verify(grant)
    if grant['version']!=PEER or grant['fact_fingerprint']!=fact.fingerprint:
        raise ValueError('live input qualification differs')
    if not grant['eligible']:return
    row=_signed({'version':KEY,'event_id':event_id,'action_id':action_id,
        'input_fact':fact.fingerprint,'grant':grant['fingerprint'],
        'prepared_fingerprint':entry['prepared_fingerprint']})
    rows=app.runtime.session.profile.get(KEY,{})
    if event_id in rows:
        if rows[event_id]!=row:raise ValueError('live input binding changed')
        return
    publish_session(app,replace(app.runtime.session,profile={**app.runtime.session.profile,KEY:{**rows,event_id:row}}))


def completed_effect(app,event_id):
    """Resolve the immediate actual effect linked to the original peer grant."""
    row=app.runtime.session.profile.get(KEY,{}).get(event_id)
    if row is None:return None
    _verify(row)
    grant=app.runtime.session.profile.get(PEER,{}).get('assessments',{}).get(event_id)
    if grant is None:return None
    _verify(grant)
    product=app._product();entry=product['executions'].get(row['action_id'],{})
    fact_memory=app.runtime.memory_instance(event_id)
    fact=fact_memory.fact
    if (row['version']!=KEY or row['event_id']!=event_id or row['input_fact']!=fact.fingerprint
        or row['grant']!=grant['fingerprint'] or grant['version']!=PEER or not grant['eligible']
        or row['prepared_fingerprint']!=entry.get('prepared_fingerprint')):
        raise ValueError('live input source binding differs')
    if entry.get('status')!='executed':return None
    from product_adapters.desktop_pet_v1 import EVENT_JOURNAL_KEY
    if row['action_id'] not in app.runtime.session.profile.get(EVENT_JOURNAL_KEY,{}):return None
    effect=app.runtime.memory_instance(row['action_id'])
    app._verify_source(effect.event)
    if effect.fact.accepted_runtime_ordinal!=fact.accepted_runtime_ordinal+1:return None
    if not any(ref['event_id']==event_id and ref['source_binding']==fact_memory.event.source_binding_ref
               for ref in entry.get('live_input_sources',())):
        raise ValueError('completed playback lacks the bound input')
    return effect


def follows_completed_execution(app,event_id):
    from .intent_history import FACTS_KEY
    chain=app.runtime.session.profile.get(FACTS_KEY,{}).get(event_id,())
    product=app._product()
    if (not chain or chain[-1]['phase']!='formed'
            or 'intention:'+chain[0]['intention_id'] in product['sources']
            or event_id in app.runtime.session.profile.get(PEER,{}).get('outcomes',{})):
        return False
    effect=completed_effect(app,event_id)
    if not effect:return False
    row=app.runtime.session.profile[KEY][event_id]
    position=app.runtime.session.position
    if effect.fact.accepted_runtime_ordinal==position.runtime_ordinal:
        return effect.fact.temporal_position_fingerprint==position.position.fingerprint
    # The previous response may settle its own deferred formation after its
    # effect. That exact tail is not an external event cancelling this grant.
    if position.runtime_ordinal!=effect.fact.accepted_runtime_ordinal+1:return False
    if not app.runtime.events:return False
    identity=app.runtime.events[-1].event_id
    source=product['sources'].get(identity,{})
    if source.get('origin')!='cx67_private_intention':return False
    parent=source['evidence']['source_ref']
    from .deferred_intention import marker
    saved=marker(app,parent)
    if saved is None or saved['memory_id']!=identity:return False
    from .product import PetProduct
    from types import SimpleNamespace
    original=app.runtime.memory_instance(parent).fact
    if PetProduct._reply_source_key(SimpleNamespace(application=app),product['sources'][row['action_id']])!=(original.accepted_runtime_ordinal,original.temporal_position_fingerprint):
        return False
    tail=app.runtime.memory_instance(identity)
    app._verify_source(tail.event)
    return (tail.fact.accepted_runtime_ordinal==position.runtime_ordinal
        and tail.fact.temporal_position_fingerprint==position.position.fingerprint)
