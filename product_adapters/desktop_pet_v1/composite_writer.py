"""One speech child from the existing Writer; no execution/settlement authority."""
from dataclasses import dataclass,replace
from framework.model_api import freeze_model_payload


@dataclass(frozen=True)
class SpeechChild:
    content: object
    prepared: object
    evidence: object


def prepare_speech_child(product,event_id,*,child_id):
    if product.writer is None:raise RuntimeError('speech child requires Writer')
    app=product.application
    # Called by the owning Actor while playback progresses independently in UI.
    # Do not move this call to a second state-writing Actor or rebuild prompts.
    with app._lock:
        prepared=product.writer.prepare(event_id)
        from .quiet_realization import QuietRealization,SpeechNotSelected
        if isinstance(prepared,QuietRealization):raise SpeechNotSelected(prepared)
        _,contents=prepared.validate(binding=app.runtime.session.session_binding,
            owner_ref=app.owner_ref,user_ref=prepared.authorization.target_ref)
        if len(contents)!=1 or contents[0].kind!='speech':raise ValueError('Writer child must contain one speech')
        if prepared.authorization.source_fact_ref!=event_id:raise ValueError('speech source changed')
        from petlab_app.idle_opening import is_idle_tick,source
        actual_source=source(app,event_id,current=True)[0] if is_idle_tick(app,event_id) else app.runtime.memory_instance(event_id)
        product.writer.verify_prepared(prepared,actual_source)
        # Identity is local to the parent. Original Writer candidate is retained
        # untouched, including presentation metadata, in the explicit derivation.
        original=contents[0]
        content=replace(original,content_id=child_id)
        evidence=freeze_model_payload({'version':'desktop-pet-speech-child-v2',
            'prepared_fingerprint':prepared.fingerprint,'request':prepared.request.to_record(),
            'attempt':prepared.attempt.to_record(),'candidate':prepared.candidate.to_record(),
            'derivation':{'original_content':original.to_record(),'parent_content':content.to_record(),
                          'presentation_owner':'temporary_speech_over_settled_expression'}})
        return SpeechChild(content,prepared,evidence)


def verify_speech_child(*,parent,evidence,binding,owner_ref,user_ref,child_id):
    from framework.realizer_protocol import RealizerRequest,RealizerAttempt,RealizerCandidate
    from framework.model_api import model_data_fingerprint
    from .execution import PreparedExecution
    version=evidence.get('version')
    if version not in {'desktop-pet-speech-child-v1','desktop-pet-speech-child-v2'}:raise ValueError('unknown speech child evidence')
    request=RealizerRequest(**{**dict(evidence['request']),
        'control':freeze_model_payload(evidence['request']['control']),
        'readonly_facts':freeze_model_payload(evidence['request']['readonly_facts'])})
    attempt=RealizerAttempt(**{**dict(evidence['attempt']),
        'invocation_evidence':freeze_model_payload(evidence['attempt']['invocation_evidence'])})
    candidate=RealizerCandidate(**{**dict(evidence['candidate']),
        'candidate_payload':freeze_model_payload(evidence['candidate']['candidate_payload']),
        'realization_evidence':freeze_model_payload(evidence['candidate']['realization_evidence'])})
    prepared=PreparedExecution(position=parent.position,scope_ref=parent.scope_ref,
        authorization=parent.authorization,request=request,attempt=attempt,candidate=candidate)
    _,contents=prepared.validate(binding=binding,owner_ref=owner_ref,user_ref=user_ref)
    if prepared.fingerprint!=evidence['prepared_fingerprint'] or len(contents)!=1 or contents[0].kind!='speech':
        raise ValueError('speech child differs from original prepared candidate')
    content=replace(contents[0],content_id=child_id,
        parameters={} if version=='desktop-pet-speech-child-v1' else contents[0].parameters)
    expected={'original_content':contents[0].to_record(),'parent_content':content.to_record(),
              'presentation_owner':'independent_parent_expression' if version=='desktop-pet-speech-child-v1' else 'temporary_speech_over_settled_expression'}
    if model_data_fingerprint(expected)!=model_data_fingerprint(evidence['derivation']):
        raise ValueError('speech child derivation changed original text')
    return content,prepared


@dataclass(frozen=True)
class ExpressionChildren:
    contents: tuple
    prepared: object
    evidence: object


def _expression_derivation(contents, child_ids):
    if set(child_ids)!={'speech','thought'} or len(set(child_ids.values()))!=2:
        raise ValueError('expression slots must be distinct speech and thought identities')
    if any(not isinstance(key,str) or not key for key in child_ids.values()):
        raise ValueError('expression slot identity missing')
    if any(c.kind not in child_ids for c in contents) or len({c.kind for c in contents})!=len(contents):
        raise ValueError('expression children differ from declared channels')
    derived=tuple(replace(c,content_id=child_ids[c.kind]) for c in contents)
    return derived,{'original_contents':[c.to_record() for c in contents],
        'parent_contents':[c.to_record() for c in derived],
        'presentation_owner':'temporary_speech_over_settled_expression'}


def prepare_expression_children(product,event_id,*,child_ids):
    """One sealed Writer result, two optional siblings; no child settlement."""
    if product.writer is None:raise RuntimeError('expression children require Writer')
    app=product.application
    with app._lock:
        prepared=product.writer.prepare(event_id)
        from .quiet_realization import QuietRealization,SpeechNotSelected
        if isinstance(prepared,QuietRealization):raise SpeechNotSelected(prepared)
        from model_packages.desktop_pet_v1.writer import PRIVATE_EXPRESSION_VERSION
        if prepared.request.control.get('version')!=PRIVATE_EXPRESSION_VERSION:
            raise ValueError('expression children require private expression contract')
        _,contents=prepared.validate(binding=app.runtime.session.session_binding,
            owner_ref=app.owner_ref,user_ref=prepared.authorization.target_ref)
        if prepared.authorization.source_fact_ref!=event_id:raise ValueError('expression source changed')
        from petlab_app.idle_opening import is_idle_tick,source
        actual=source(app,event_id,current=True)[0] if is_idle_tick(app,event_id) else app.runtime.memory_instance(event_id)
        product.writer.verify_prepared(prepared,actual)
        derived,derivation=_expression_derivation(contents,child_ids)
        evidence=freeze_model_payload({'version':'desktop-pet-expression-children-v1',
            'prepared_fingerprint':prepared.fingerprint,'request':prepared.request.to_record(),
            'attempt':prepared.attempt.to_record(),'candidate':prepared.candidate.to_record(),'derivation':derivation})
        return ExpressionChildren(derived,prepared,evidence)


def verify_expression_children(*,parent,evidence,binding,owner_ref,user_ref,child_ids):
    from framework.realizer_protocol import RealizerRequest,RealizerAttempt,RealizerCandidate
    from framework.model_api import model_data_fingerprint
    from .execution import PreparedExecution
    from model_packages.desktop_pet_v1.writer import PRIVATE_EXPRESSION_VERSION
    if evidence.get('version')!='desktop-pet-expression-children-v1':raise ValueError('unknown expression child evidence')
    request=RealizerRequest(**{**dict(evidence['request']),
        'control':freeze_model_payload(evidence['request']['control']),
        'readonly_facts':freeze_model_payload(evidence['request']['readonly_facts'])})
    attempt=RealizerAttempt(**{**dict(evidence['attempt']),
        'invocation_evidence':freeze_model_payload(evidence['attempt']['invocation_evidence'])})
    candidate=RealizerCandidate(**{**dict(evidence['candidate']),
        'candidate_payload':freeze_model_payload(evidence['candidate']['candidate_payload']),
        'realization_evidence':freeze_model_payload(evidence['candidate']['realization_evidence'])})
    prepared=PreparedExecution(position=parent.position,scope_ref=parent.scope_ref,
        authorization=parent.authorization,request=request,attempt=attempt,candidate=candidate)
    _,contents=prepared.validate(binding=binding,owner_ref=owner_ref,user_ref=user_ref)
    if request.control.get('version')!=PRIVATE_EXPRESSION_VERSION or prepared.fingerprint!=evidence['prepared_fingerprint']:
        raise ValueError('expression children differ from original candidate')
    derived,expected=_expression_derivation(contents,child_ids)
    if model_data_fingerprint(expected)!=model_data_fingerprint(evidence['derivation']):
        raise ValueError('expression child derivation changed original contents')
    return derived,prepared
