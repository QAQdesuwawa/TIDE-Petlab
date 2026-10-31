"""Already-spoken transcript facts, distinct from current input and execution.

Source acceptance is product-owned. Numerical settlement and History remain
the original runtime's authority; no Writer, animation or external tool runs.
"""
from framework.model_api import model_data_fingerprint as fingerprint,thaw_model_data
from model_packages.desktop_pet_v1.events import PetEvent,Speech

VERSION='desktop-pet-imported-speech-source-v1'


def build(*,definition,message,source_digest,coordinate,estimate_ref):
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
    own=message.role=='assistant';actor=definition.owner_ref if own else definition.user_ref
    targets=(definition.user_ref,) if own else (definition.owner_ref,)
    content=Speech(content_id=event_id+':speech',description=message.text,recipient_refs=targets)
    evidence={'version':VERSION,'source_digest':source_digest,'source_index':message.index,
        'source_message_id':message.source_id,'source_role':message.role,'text_fingerprint':fingerprint(message.text),
        'original_timestamp_ms':message.timestamp_ms,'reconstruction_position':position.to_record(),
        'identity_fingerprint':fingerprint({key:getattr(definition,key) for key in
            ('session_id','owner_ref','user_ref','scope_ref','name','background')}),
        'estimate_ref':estimate_ref,'owner_ref':definition.owner_ref,
        'actor_ref':actor,'target_refs':list(targets),'scope_ref':definition.scope_ref,'event_id':event_id}
    event=PetEvent(event_id=event_id,source_ref='transcript:'+source_digest+':'+str(message.index),
        source_receipt_ref=fingerprint(evidence),owner_ref=definition.owner_ref,actor_ref=actor,
        target_refs=targets,scope_ref=definition.scope_ref,position=position,
        event_kind='npc_output' if own else 'peer_input',contents=(content,))
    return event,evidence


def verify(app,event,source):
    evidence=thaw_model_data(source.get('evidence'))
    if not isinstance(evidence,dict) or evidence.get('version')!=VERSION:raise ValueError('import source version differs')
    from .history_import import TranscriptMessage
    if len(event.contents)!=1 or type(event.contents[0]) is not Speech:
        raise ValueError('imported speech is not an animation or tool execution')
    message=TranscriptMessage(evidence['source_index'],evidence['source_message_id'],
        evidence['source_role'],event.contents[0].description,evidence['original_timestamp_ms'])
    expected,proof=build(definition=app.definition,message=message,source_digest=evidence['source_digest'],
        coordinate=evidence['reconstruction_position']['coordinate'],estimate_ref=evidence['estimate_ref'])
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
                raise ValueError('imported speech conflicts with accepted source')
            if previous.get('measurement') is not None and fingerprint(previous['measurement'])!=fingerprint(measurement):
                raise ValueError('imported measurement conflicts with accepted source')
        else:
            app._require_ready()
            product['sources'][event.event_id]=row
            app._publish_product(product)
        return app._settle(event,lambda current:measurement)
