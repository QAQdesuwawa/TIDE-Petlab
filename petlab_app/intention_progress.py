"""Collect actual plan/execution evidence for package-owned intention chains."""
from dataclasses import replace
from framework.model_api import model_data_fingerprint as fp
from model_packages.cx_six_seven_v1 import intention_facts as facts
from .owned_journals import publish_session
from .intention_source import enabled

PROOFS='cx67-intention-progress-evidence-v1'


def roots(app, source_ref):
    from .peer_reaction import KEY
    grants=app.runtime.session.profile.get(KEY,{}).get('assessments',{})
    found=[]; seen=set(); pending=[source_ref]
    while pending:
        key=pending.pop()
        if key in seen:continue
        seen.add(key)
        if key in app.runtime.session.profile.get(facts.VERSION,{}):found.append(key)
        else:pending.extend(grants.get(key,{}).get('carried_from',()))
    return tuple(found)


def record(app, source_ref, *, phase, evidence, actual_effect_ref=None):
    if not enabled(app):return
    identities=roots(app,source_ref)
    if not identities:return
    profile=dict(app.runtime.session.profile)
    # A new sealed plan is a new attempt, not a new intention. Later effects
    # and stop evidence belong to that exact plan even on the same input.
    attempt_ref=evidence.get('prepared_ref') if phase=='planned' else None
    if attempt_ref is None:
        for identity in identities:
            for row in reversed(profile[facts.VERSION][identity][1:]):
                prior=profile.get(PROOFS,{}).get(row['evidence_ref'],{})
                if prior.get('source_ref')==source_ref:
                    attempt_ref=row['attempt_id'];break
            if attempt_ref is not None:break
    attempt_ref=attempt_ref or source_ref
    proof={'version':PROOFS,'owner':app.owner_ref,'visibility':'self',
           'source_ref':source_ref,'phase':phase,**evidence,
           **({'attempt_ref':attempt_ref} if attempt_ref!=source_ref else {})}
    # Existing archives used the source as attempt identity. Re-reading the
    # exact same durable plan must not append a migration as a new occurrence.
    legacy={k:v for k,v in proof.items() if k!='attempt_ref'}
    if phase=='planned' and fp(legacy) in profile.get(PROOFS,{}):return
    digest=fp(proof)
    records=dict(profile[facts.VERSION]);proofs=dict(profile.get(PROOFS,{}))
    # The same durable evidence is idempotent even when later state has advanced.
    if digest in proofs:return
    coordinate=app.runtime.session.position.position.coordinate
    for identity in identities:
        records[identity]=facts.append(records[identity],transition_id=digest,
            attempt_id=attempt_ref,phase=phase,at_ms=coordinate,evidence_ref=digest,
            actual_effect_ref=actual_effect_ref)
    proofs[digest]=proof
    profile[facts.VERSION]=records;profile[PROOFS]=proofs
    publish_session(app,replace(app.runtime.session,profile=profile))


def planned(app, source_ref, prepared, *, writer=None):
    if not enabled(app):return
    prepared.validate(binding=app.runtime.session.session_binding,owner_ref=app.owner_ref,
                      user_ref=prepared.authorization.target_ref)
    identity=prepared.request.readonly_facts.get('writer_packet',{}).get('writer_request_id')
    archive=(writer.attempt_journal._artifact_path(identity) if writer is not None and identity else
             prepared.candidate.realization_evidence.get('trace_ref'))
    record(app,source_ref,phase='planned',evidence={
        'prepared_ref':prepared.fingerprint,'candidate_ref':prepared.candidate.candidate_ref,
        'writer_request_id':identity,
        **({'candidate_archive':str(archive),'candidate_fingerprint':fp(prepared.candidate.to_record()),
            'archive_kind':'writer_attempt' if writer is not None and identity else 'local_realizer'} if archive else {})})


def actual(app, source_ref, source):
    if not enabled(app):return
    from model_packages.desktop_pet_v1.events import PetEvent
    event=PetEvent.from_record(source['event']);app._verify_source(event)
    if source['origin']!='execution':raise ValueError('intention effect requires actual execution')
    receipt=source['evidence']['fact']['model_payload']['execution_receipt']
    record(app,source_ref,phase='effect',actual_effect_ref=event.event_id,evidence={
        'event_ref':event.event_id,'source_binding_ref':event.source_binding_ref,
        'execution_receipt_ref':receipt['fingerprint']})
    interrupted=facts.interrupted_children(receipt['outcome'])
    if interrupted:
        takeover=receipt['outcome'].get('details',{}).get('qualification_takeover')
        evidence={'reason':'partial_execution','actual_effect_ref':event.event_id,
            'execution_receipt_ref':receipt['fingerprint'],'interrupted_children':list(interrupted)}
        if takeover is not None:
            from .peer_reaction import _verify,KEY
            from .execution_inputs import KEY as LIVE_INPUTS
            grant=app.runtime.session.profile.get(KEY,{}).get('assessments',{}).get(takeover.get('event_id'))
            binding=app.runtime.session.profile.get(LIVE_INPUTS,{}).get(takeover.get('event_id'))
            if grant is None or binding is None:raise ValueError('actual takeover lacks formal qualification')
            _verify(grant);_verify(binding)
            if (not grant['eligible'] or grant['version']!=KEY or grant['fingerprint']!=takeover.get('grant_fingerprint')
                or binding['grant']!=grant['fingerprint'] or binding['action_id']!=event.event_id):
                raise ValueError('actual takeover qualification differs')
            evidence['qualification_takeover']=dict(takeover)
        record(app,source_ref,phase='interrupted',evidence=evidence)


def stopped(app, source_ref, status, *, superseded_by=()):
    if status not in {'executed','pending','cancelled','quiet','no_language_realizer','technical_failure'}:return
    if status=='cancelled':
        latest=[app.runtime.session.profile[facts.VERSION][identity][-1] for identity in roots(app,source_ref)]
        if latest and all(row['phase']=='interrupted' and
            app.runtime.session.profile.get(PROOFS,{}).get(row['evidence_ref'],{}).get('reason')=='destination_occupied' and
            app.runtime.session.profile[PROOFS][row['evidence_ref']].get('source_ref')==source_ref for row in latest):return
    if status=='executed':
        # Execution produced a durable fact, but its unplayed suffix did not
        # become a completed intention. Keep the exact partial-effect proof.
        latest=[app.runtime.session.profile[facts.VERSION][identity][-1] for identity in roots(app,source_ref)]
        if latest and all(row['phase']=='interrupted' and
                app.runtime.session.profile.get(PROOFS,{}).get(row['evidence_ref'],{}).get('reason')=='partial_execution'
                and app.runtime.session.profile[PROOFS][row['evidence_ref']].get('source_ref')==source_ref for row in latest):
            return
    phase=('completed' if status=='executed' else 'abandoned' if status in {'quiet','no_language_realizer'}
           else 'interrupted')
    record(app,source_ref,phase=phase,evidence={'reason':status,'superseded_by':list(superseded_by)})
