"""Six-seven grant continuity across its own private formation settlement."""
from .intention_source import ORIGIN, verify


def carried_grant(app,event_id):
    from .peer_reaction import KEY,CARRY,_verify_carried
    grant=app.runtime.session.profile.get(KEY,{}).get('assessments',{}).get(event_id)
    if grant is None or grant.get('version')!=CARRY:return None
    _verify_carried(app,grant)
    return grant


def base_planning_id(app,event_id):
    if carried_grant(app,event_id) is not None:return event_id
    from .deferred_intention import marker
    if marker(app,event_id) is not None:return event_id
    from .intent_history import FACTS_KEY
    rows=app.runtime.session.profile.get(FACTS_KEY,{}).get(event_id)
    return 'intention:'+rows[0]['intention_id'] if rows else None


def validate_planning(app, event_id, planning_event_id, *, _formation_only=False):
    """Historical evidence check; does not reactivate a closed opportunity."""
    from .deferred_intention import marker
    if planning_event_id==event_id and marker(app,event_id) is not None:
        memory=app.runtime.memory_instance(event_id)
        app._verify_source(memory.event)
        return memory
    if planning_event_id==event_id and carried_grant(app,event_id) is not None:
        memory=app.runtime.memory_instance(event_id)
        app._verify_source(memory.event)
        return memory
    source = app._product()['sources'].get(planning_event_id)
    if not _formation_only and source is not None and source['origin']!='cx67_private_intention':
        from .retained_intention import validate
        return validate(app,event_id,planning_event_id)
    if source is None or source['origin'] != ORIGIN:
        raise ValueError('planning source is not an accepted private intention')
    memory = app.runtime.memory_instance(planning_event_id)
    verify(app, memory.event, source)
    if source['evidence']['source_ref'] != event_id:
        raise ValueError('planning intention belongs to another input')
    from .idle_opening import is_idle_tick,source as idle_source
    ordinal=(idle_source(app,event_id)[0].row['source_snapshot']['position']['runtime_ordinal']
             if is_idle_tick(app,event_id) else app.runtime.memory_instance(event_id).fact.accepted_runtime_ordinal)
    if memory.fact.accepted_runtime_ordinal <= ordinal:
        raise ValueError('private intention does not follow its input')
    return memory


def current(app, event_id):
    identity=base_planning_id(app,event_id)
    if identity is None:return None
    from .retained_intention import head
    identity=head(app,event_id) or identity
    from product_adapters.desktop_pet_v1 import EVENT_JOURNAL_KEY
    if identity not in app.runtime.session.profile.get(EVENT_JOURNAL_KEY, {}):
        return None
    memory = validate_planning(app, event_id, identity)
    position = app.runtime.session.position
    if (memory.fact.accepted_runtime_ordinal != position.runtime_ordinal
            or memory.fact.temporal_position_fingerprint != position.position.fingerprint):
        return None
    return memory


def authorize(app, event_id):
    from .peer_reaction import _journal, _verify
    data = _journal(app)
    if event_id in data['outcomes']:
        raise ValueError('intention opportunity is already closed')
    grant = data['assessments'].get(event_id)
    if grant is None:
        from .interaction_observer import KEY
        grant=app.runtime.session.profile.get(KEY,{}).get('ticks',{}).get(event_id)
        if grant is None:raise ValueError('intention has no accepted grant')
        from .intent_history import FACTS_KEY
        chain=app.runtime.session.profile.get(FACTS_KEY,{}).get(event_id,())
        if chain and chain[-1]['phase'] in {'completed','abandoned','interrupted'}:
            from model_packages.cx_six_seven_v1.intention_facts import technical_interruption
            from .intention_progress import PROOFS
            proof=app.runtime.session.profile.get(PROOFS,{}).get(chain[-1]['evidence_ref'],{})
            if not technical_interruption(chain,proof):
                raise ValueError('intention opportunity is already closed')
    _verify(grant)
    memory = current(app, event_id)
    if memory is None:
        raise ValueError('intention planning state is stale')
    base_id=base_planning_id(app,event_id)
    formation=validate_planning(app,event_id,base_id,_formation_only=True)
    # A carried source references the existing grant lineage; it is not a new
    # private formation and must not be presented as one in authorization.
    from .deferred_intention import marker
    deferred=marker(app,event_id)
    provenance=({'formation_evidence_fingerprint':deferred['fingerprint'],
                 'input_fact_fingerprint':formation.fact.fingerprint} if deferred else
        {'carried_fact_fingerprint' if base_id==event_id else 'intention_fact_fingerprint':formation.fact.fingerprint})
    from framework.action_protocol import ActionAuthorization
    from product_adapters.desktop_pet_v1.settlement_view import KEY, current_projection
    from .destinations import primary
    parent = app.runtime.memory_instance(event_id)
    projection = current_projection(app)
    view = app.runtime.session.profile[KEY]
    return ActionAuthorization.create(session_binding=app.runtime.session.session_binding,
        current_position=app.runtime.session.position, action_kind='direct_reply',
        subject_ref=app.owner_ref, target_ref=primary(app,event_id), source_fact_ref=event_id,
        source_settlement_fingerprint=parent.fact.settlement_ref['acceptance_fingerprint'],
        package_eligibility_evidence={'version':'cx67-intention-continuation-v1',
            'eligible':True, 'grant_fingerprint':grant['fingerprint'],
            **provenance},
        host_authorization_evidence={'origin':'deferred_direct_reply_v1',
            'intention_continuation_version':'cx67-intention-continuation-v1',
            'planning_view_fingerprint':view['fingerprint'],
            'planning_acceptance_fingerprint':view['acceptance']['fingerprint'],
            'planning_event_ref':projection.context.event_ref})
