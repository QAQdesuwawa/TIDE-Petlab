"""Source bindings for retaining an intention across an ineligible input.

No new threshold, state contribution or execution permission is computed here.
"""
from dataclasses import replace
from framework.model_api import model_data_fingerprint as fp,thaw_model_data

KEY='cx67-retained-intention-planning-v1'


def _id(root,planning):return fp([root,planning])


def head(app,root):
    return app.runtime.session.profile.get(KEY,{}).get('heads',{}).get(root)


def validate(app,root,planning):
    from .peer_reaction import KEY as PEER,_verify,is_peer
    from .intention_continuation import validate_planning
    nodes=app.runtime.session.profile.get(KEY,{}).get('nodes',{})
    original=planning;seen=set()
    while _id(root,planning) in nodes:
        if planning in seen:raise ValueError('retained planning cycle')
        seen.add(planning)
        row=nodes[_id(root,planning)];_verify(row)
        memory=app.runtime.memory_instance(planning)
        previous=app.runtime.memory_instance(row['previous'])
        grant=app.runtime.session.profile.get(PEER,{}).get('assessments',{}).get(planning)
        if grant is None:raise ValueError('retained planning lacks assessment')
        _verify(grant)
        if (row['version']!=KEY or row['root']!=root or row['planning']!=planning
            or row['fact']!=memory.fact.fingerprint or row['previous_fact']!=previous.fact.fingerprint
            or row['assessment']!=grant['fingerprint'] or grant['version']!=PEER
            or grant['eligible'] or grant['fact_fingerprint']!=memory.fact.fingerprint
            or not is_peer(app,planning)
            or memory.fact.accepted_runtime_ordinal!=previous.fact.accepted_runtime_ordinal+1):
            raise ValueError('retained planning differs from adjacent ineligible source')
        planning=row['previous']
    # Terminates at the original formation or a verified inherited planning source.
    validate_planning(app,root,planning,_formation_only=True)
    return app.runtime.memory_instance(original)


def retain(app,root,after):
    from .peer_reaction import _journal,_verify,_signed,is_peer,KEY as PEER
    from .intent_history import FACTS_KEY
    from .intention_continuation import validate_planning,carried_grant,base_planning_id
    from .owned_journals import publish_session
    with app._lock:
        from .idle_opening import is_idle_tick,journal as idle_journal,source as idle_source
        if is_idle_tick(app,root):
            data=idle_journal(app)
            if root in data['outcomes'] or data['pending_tick']!=root:
                raise ValueError('retained intention is not open')
            tick,accepted=idle_source(app,root)
            if accepted.authorization is None:raise ValueError('retained intention lacks original qualification')
        else:
            data=_journal(app);grant=data['assessments'].get(root)
            if grant is None or root in data['outcomes']:
                raise ValueError('retained intention is not open')
            _verify(grant)
            if not grant['eligible'] or grant['fact_fingerprint']!=app.runtime.memory_instance(root).fact.fingerprint:
                raise ValueError('retained intention lacks original qualification')
        chain=app.runtime.session.profile.get(FACTS_KEY,{}).get(root,())
        carried=carried_grant(app,root)
        if carried is None and (not chain or chain[-1]['phase'] in {'completed','abandoned'}):
            raise ValueError('retained intention is not active')
        if carried is None and chain[-1]['phase']=='interrupted':
            from model_packages.cx_six_seven_v1.intention_facts import technical_interruption
            from .intention_progress import PROOFS
            proof=app.runtime.session.profile.get(PROOFS,{}).get(chain[-1]['evidence_ref'],{})
            if not technical_interruption(chain,proof):
                raise ValueError('retained intention is not active')
        current=app.runtime.memory_instance(after)
        position=app.runtime.session.position
        if (current.fact.accepted_runtime_ordinal!=position.runtime_ordinal
                or current.fact.temporal_position_fingerprint!=position.position.fingerprint):
            raise ValueError('retained planning source is not current')
        low=_journal(app)['assessments'].get(after)
        if low is None:raise ValueError('retained planning requires a formal assessment')
        _verify(low)
        if low['version']!=PEER or low['eligible'] or not is_peer(app,after):
            raise ValueError('new qualification cannot retain an old intention')
        journal=app.runtime.session.profile.get(KEY,{})
        previous=head(app,root) or base_planning_id(app,root)
        if previous==after:
            return validate(app,root,after)
        parent=validate_planning(app,root,previous)
        row=_signed({'version':KEY,'root':root,'planning':after,'previous':previous,
            'fact':current.fact.fingerprint,'previous_fact':parent.fact.fingerprint,
            'assessment':low['fingerprint']})
        # Validate the exact new link before publication, without staging a
        # counterfeit session or changing authoritative roots.
        if (low['fact_fingerprint']!=current.fact.fingerprint
                or current.fact.accepted_runtime_ordinal!=parent.fact.accepted_runtime_ordinal+1):
            raise ValueError('retained planning must follow its previous planning source')
        updated={'nodes':{**journal.get('nodes',{}),_id(root,after):row},
                 'heads':{**journal.get('heads',{}),root:after}}
        publish_session(app,replace(app.runtime.session,profile={**app.runtime.session.profile,KEY:updated}))
        return validate(app,root,after)
