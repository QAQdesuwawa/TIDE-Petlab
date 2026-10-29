"""Preserve stale sealed candidates; never reinterpret them as new replies."""
from petlab_app.owned_journals import publish_session
from dataclasses import replace
from framework.model_api import thaw_model_data,model_data_fingerprint
from framework.runtime_kernel import RuntimeStateCommitAuthority
from .execution import PreparedExecution,ExecutionUncertain
from .settlement_view import KEY as VIEW_KEY,current_projection

KEY='desktop_pet_retired_writer_v1'


def _original(service,artifact):
    app=service.application
    position=RuntimeStateCommitAuthority(session_binding=app.runtime.session.session_binding).restore_position_state(
        artifact['source_evidence']['position'])
    prepared=service._restore_work(artifact['source_evidence']['event_id'],position=position,
        only_request_id=artifact['writer_request_id'],ignore_retired=True)
    if not isinstance(prepared,PreparedExecution):
        raise ExecutionUncertain('stale Writer lacks a sealed result; reconcile before replanning')
    if any(row['prepared_fingerprint']==prepared.fingerprint for row in app._product()['executions'].values()):
        raise ExecutionUncertain('executed or uncertain candidate cannot be retired')
    return prepared


def is_retired(service,artifact):
    row=service.application.runtime.session.profile.get(KEY,{}).get(artifact['writer_request_id'])
    if row is None:return False
    row=thaw_model_data(row)
    if (row.get('version')!=KEY or row.get('artifact_fingerprint')!=model_data_fingerprint(artifact)
            or row.get('fingerprint')!=model_data_fingerprint({k:v for k,v in row.items() if k!='fingerprint'})):
        raise ValueError('retired Writer record differs from sealed original')
    prepared=_original(service,artifact)
    if prepared.fingerprint!=row['prepared_fingerprint']:
        raise ValueError('retired Writer candidate differs from original')
    current_projection(service.application,record=row['superseding_view'],historical=True)
    if row['superseding_view']['acceptance']['position']['runtime_ordinal']<=prepared.position.runtime_ordinal:
        raise ValueError('retired Writer needs a later accepted state')
    return True


def retire_stale(service,event_id):
    app=service.application
    for receipt in service.attempt_journal.read_receipts():
        if receipt['stage']!='allocated':continue
        artifact=service.attempt_journal.read_attempt(receipt['writer_request_id'])
        sealed=artifact.get('source_evidence',{})
        if sealed.get('event_id')!=event_id or 'commit' in artifact or artifact.get('terminal',{}).get('status')=='failed':continue
        if is_retired(service,artifact) or sealed.get('position')==app.runtime.session.position.to_record():continue
        if artifact.get('terminal',{}).get('status')!='candidate_ready':
            raise ExecutionUncertain('stale Writer has no confirmed candidate; reconcile before replanning')
        prepared=_original(service,artifact)
        current_projection(app)
        row={'version':KEY,'artifact_fingerprint':model_data_fingerprint(artifact),
            'prepared_fingerprint':prepared.fingerprint,'superseding_view':thaw_model_data(app.runtime.session.profile[VIEW_KEY])}
        row['fingerprint']=model_data_fingerprint(row)
        profile=dict(app.runtime.session.profile);profile[KEY]=dict(profile.get(KEY,{}));profile[KEY][artifact['writer_request_id']]=row
        session=replace(app.runtime.session,profile=profile);publish_session(app,session)
