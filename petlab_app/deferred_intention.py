"""Durable formation now; original private-memory settlement after presentation.

This is actor-owned work, not a second state-writing background worker.
"""
from dataclasses import replace
from framework.model_api import model_data_fingerprint as fp
from .intention_source import build, ingest
from .owned_journals import publish_session

KEY = 'cx67-deferred-formation-v1'


def marker(app, source_ref):
    row = app.runtime.session.profile.get(KEY, {}).get(source_ref)
    if row is None:
        return None
    if row['fingerprint'] != fp({k:v for k,v in row.items() if k != 'fingerprint'}):
        raise ValueError('deferred formation evidence differs')
    event, evidence = build(app, source_ref=source_ref, coordinate=row['formed_at'])
    if (row['version'] != KEY or row['source_ref'] != source_ref
            or row['memory_id'] != event.event_id or row['evidence'] != evidence):
        raise ValueError('deferred formation source differs')
    return row


def defer(app, source_ref):
    with app._lock:
        if marker(app, source_ref) is not None:
            return True
        event, evidence = build(app, source_ref=source_ref,
                                coordinate=app.runtime.session.position.position.coordinate)
        # Already accepted old transactions retain their original order/schema.
        if event.event_id in app._product()['sources']:
            return False
        row = {'version':KEY, 'source_ref':source_ref, 'memory_id':event.event_id,
               'formed_at':event.position.coordinate, 'evidence':evidence}
        row['fingerprint'] = fp(row)
        records = dict(app.runtime.session.profile.get(KEY, {}))
        records[source_ref] = row
        publish_session(app, replace(app.runtime.session,
            profile={**app.runtime.session.profile, KEY:records}))
        return True


def settle_finished(product, source_ref):
    app = product.application
    with app._lock:
        row = marker(app, source_ref)
        if row is None:
            from .intention_progress import roots
            for root in roots(app, source_ref):
                if root != source_ref:
                    settle_finished(product, root)
            return
        from .peer_reaction import KEY as PEER
        outcome = app.runtime.session.profile.get(PEER, {}).get('outcomes', {}).get(source_ref)
        from .intent_history import FACTS_KEY
        chain = app.runtime.session.profile.get(FACTS_KEY, {}).get(source_ref, ())
        finished = bool(chain and chain[-1]['phase'] in {'completed','abandoned'})
        if not finished and (outcome is None or outcome['status'] not in {'executed','quiet','cancelled','no_language_realizer'}):
            return
        from product_adapters.desktop_pet_v1 import EVENT_JOURNAL_KEY
        if row['memory_id'] in app.runtime.session.profile.get(EVENT_JOURNAL_KEY, {}):
            return
        from .measurement_pipeline import tail_admission
        with tail_admission(app,source_ref,row['memory_id']):
            return ingest(app, source_ref=source_ref,
                          coordinate=app.runtime.session.position.position.coordinate,
                          measure=product.semantic.measure)
