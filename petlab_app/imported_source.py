"""Imported historical facts, distinct from current input and execution.

Source acceptance is product-owned. Numerical settlement and History remain
the original runtime's authority; no Writer, animation or external tool runs.
"""
from framework.model_api import model_data_fingerprint as fingerprint,thaw_model_data
from model_packages.desktop_pet_v1.events import Action, Interaction, PetEvent, Speech
from model_packages.desktop_pet_v1.import_event import ImportedEventTag

VERSION='desktop-pet-imported-speech-source-v1'
TYPED_VERSION='desktop-pet-imported-typed-source-v1'
PAIR_VERSION='desktop-pet-imported-npc-pair-speech-v1'
PAIR_SCOPE='petlab:peers'
_TYPES={'action': Action, 'interaction': Interaction}


def build(*,definition,message,source_digest,coordinate,estimate_ref,pair=None,original_index=None):
    from temporal_packages.wall_clock_ms_v1 import WallClockMillisecondsV1
    if not isinstance(source_digest,str) or len(source_digest)!=64 or any(c not in '0123456789abcdef' for c in source_digest):
        raise ValueError('import requires source file SHA256')
    if not isinstance(estimate_ref,str) or not estimate_ref:raise ValueError('import estimate source required')
    if (type(message.index) is not int or message.index<0 or message.role not in ('user','assistant')
            or not isinstance(message.text,str) or not message.text.strip()
            or (message.timestamp_ms is not None and (type(message.timestamp_ms) is not int or message.timestamp_ms<0))):
        raise ValueError('import message source or perspective differs')
    position=WallClockMillisecondsV1().normalize_position(coordinate=coordinate)
    event_id='import:'+fingerprint({'dataset':source_digest,'owner':definition.owner_ref})+':'+str(message.index)
    own=message.role=='assistant'
    peer=definition.user_ref
    scope=definition.scope_ref
    tag = message.event_tag
    if pair is not None:
        roles=pair['roles'];owner_role=pair['owner_source_role']
        if (tag is not None or set(roles)!={'user','assistant'} or owner_role not in roles
                or roles[owner_role]!=definition.owner_ref or len(set(roles.values()))!=2
                or definition.user_ref in roles.values() or definition.user_ref!=pair['host_user_ref']
                or pair['name']!=definition.name):
            raise ValueError('paired transcript identity differs')
        peer=next(ref for role,ref in roles.items() if role!=owner_role)
        scope=PAIR_SCOPE
    actor=definition.owner_ref if own else peer
    targets=(peer,) if own else (definition.owner_ref,)
    if tag is None:
        content=Speech(content_id=event_id+':speech',description=message.text,recipient_refs=targets)
    else:
        tag=ImportedEventTag.read(tag.source_record(),role=message.role,resolved_key=tag.semantic_key)
        content=_TYPES[tag.kind](content_id=event_id+':'+tag.kind,description=message.text,
            recipient_refs=targets,semantic_key=tag.semantic_key)
    evidence={'version':VERSION if tag is None else TYPED_VERSION,'source_digest':source_digest,'source_index':message.index,
        'source_message_id':message.source_id,'source_role':message.role,'text_fingerprint':fingerprint(message.text),
        'original_timestamp_ms':message.timestamp_ms,'reconstruction_position':position.to_record(),
        'identity_fingerprint':fingerprint({key:getattr(definition,key) for key in
            ('session_id','owner_ref','user_ref','scope_ref','name','background')}),
        'estimate_ref':estimate_ref,'owner_ref':definition.owner_ref,
        'actor_ref':actor,'target_refs':list(targets),'scope_ref':definition.scope_ref,'event_id':event_id}
    if pair is not None:
        original_role=next(role for role,ref in pair['roles'].items() if ref==actor)
        evidence.update(version=PAIR_VERSION,pair=pair,original_source_role=original_role,scope_ref=scope)
    if original_index is not None:
        if pair is None or type(original_index) is not int or original_index<message.index:
            raise ValueError('paired source row differs')
        evidence['original_source_index']=original_index
    if tag is not None:
        evidence.update(source_event=tag.source_record(), semantic_key=tag.semantic_key)
    event=PetEvent(event_id=event_id,source_ref='transcript:'+source_digest+':'+str(message.index if original_index is None else original_index),
        source_receipt_ref=fingerprint(evidence),owner_ref=definition.owner_ref,actor_ref=actor,
        target_refs=targets,scope_ref=scope,position=position,
        event_kind='npc_output' if own else 'peer_input',contents=(content,))
    return event,evidence


def verify(app,event,source):
    evidence=thaw_model_data(source.get('evidence'))
    if not isinstance(evidence,dict) or evidence.get('version') not in (VERSION, TYPED_VERSION, PAIR_VERSION):
        raise ValueError('import source version differs')
    from .history_import import TranscriptMessage
    tag = None
    if evidence['version'] == TYPED_VERSION:
        tag = ImportedEventTag.read(evidence['source_event'], role=evidence['source_role'],
                                    resolved_key=evidence['semantic_key'])
    expected_type = Speech if tag is None else _TYPES[tag.kind]
    if len(event.contents) != 1 or type(event.contents[0]) is not expected_type:
        raise ValueError('imported event content differs from its source type')
    message=TranscriptMessage(evidence['source_index'],evidence['source_message_id'],
        evidence['source_role'],event.contents[0].description,evidence['original_timestamp_ms'],tag)
    expected,proof=build(definition=app.definition,message=message,source_digest=evidence['source_digest'],
        coordinate=evidence['reconstruction_position']['coordinate'],estimate_ref=evidence['estimate_ref'],
        pair=evidence.get('pair') if evidence['version']==PAIR_VERSION else None,
        original_index=evidence.get('original_source_index'))
    if (source.get('origin')!='transcript' or proof!=evidence or expected.to_record()!=event.to_record()
            or thaw_model_data(source.get('event'))!=event.to_record()):raise ValueError('import source binding differs')


def ingest(app,*,event,evidence,measurement):
    """Use the same application settlement and original full-memory transaction."""
    with app._lock:
        row={'origin':'transcript','event':event.to_record(),'evidence':evidence}
        verify(app,event,row)
        product=app._product(source_id=event.event_id)
        previous=product['sources'].get(event.event_id)
        if previous is not None:
            if previous['event']!=row['event'] or previous['evidence']!=row['evidence']:
                raise ValueError('imported event conflicts with accepted source')
            if previous.get('measurement') is not None and fingerprint(previous['measurement'])!=fingerprint(measurement):
                raise ValueError('imported measurement conflicts with accepted source')
        else:
            app._require_ready()
            product['sources'][event.event_id]=row
            app._publish_product(product)
        return app._settle(event,lambda current:measurement)
