"""A first sighting backed by an objective scene, with no NPC output implied."""
import json

from framework.model_api import model_data_fingerprint as fp, thaw_model_data
from model_packages.desktop_pet_v1 import spatial
from model_packages.desktop_pet_v1.events import Interaction
from .external_input import archive_root
from .multi_pet import _immutable

VERSION = 'pet-scene-encounter-v1'
ACTIVITY_VERSION = 'pet-scene-activity-observation-v1'
DESKTOP_VERSION = 'pet-scene-desktop-arrival-v1'
VERSIONS = frozenset((VERSION, ACTIVITY_VERSION, DESKTOP_VERSION))


def _identity(app, peer, activity=None):
    parts = [app.runtime.session.session_binding.fingerprint, app.owner_ref, peer]
    if activity is not None:
        parts.append(activity)
    return 'encounter:' + fp(parts)


def content(row):
    description = f"现场观察：附近看见了同伴{row['label']}（{row['actor_ref']}）。"
    if row.get('version') == ACTIVITY_VERSION:
        activity = next(obj for obj in row['scene']['objects'] if obj['ref'] == row['activity_ref'])
        description = f"现场观察：看见{row['label']}（{row['actor_ref']}）{activity['label']}。"
    if row.get('version') == DESKTOP_VERSION:
        place = next(obj for obj in row['scene']['objects'] if obj['ref'] == row['arrival']['place'])
        verb = '走到' if row['actor_ref'] == row['arrival']['mover'] else '在'
        description = f"现场观察：看见{row['label']}（{row['actor_ref']}）{verb}{place['label']}旁。"
    return Interaction(content_id=row['event_id'], semantic_key='scene_encounter',
        description=description,
        recipient_refs=(row['reader_ref'],), parameters={'scene_fingerprint': row['scene']['fingerprint']})


def capture(app, scene, peer, *, activity_ref=None, arrival=None, received_at=None):
    scene = spatial.validate(scene)
    if peer not in app.multi_workspace.aliases or peer == app.owner_ref or scene['owner_ref'] != app.owner_ref:
        raise ValueError('encounter participant differs from the registered scene')
    if activity_ref is not None and not any(obj['ref'] == activity_ref for obj in scene.get('objects', ())):
        raise ValueError('observed activity is absent from the scene')
    if arrival is not None and (activity_ref is not None or not proves_arrival(scene, peer, arrival)):
        raise ValueError('desktop arrival is outside the visible encounter')
    identity = _identity(app, peer, arrival['id'] if arrival else activity_ref)
    label = next(r['label'] for r in scene['participants'] if r['ref'] == peer)
    row = {'version': ACTIVITY_VERSION if activity_ref is not None else VERSION,
           'event_id': identity, 'reader_ref': app.owner_ref, 'actor_ref': peer,
           'session_binding_fingerprint': app.runtime.session.session_binding.fingerprint,
           'scene': scene, 'label': label, 'received_at_ms': scene['sampled_at_ms']}
    if activity_ref is not None:
        row['activity_ref'] = activity_ref
    if arrival is not None:
        row.update(version=DESKTOP_VERSION, arrival=arrival, received_at_ms=received_at)
    row['fingerprint'] = fp(row)
    _immutable(archive_root(app)/'scene-encounters'/(identity.split(':')[1]+'.json'), row)
    return row


def proves_arrival(scene, peer, arrival):
    """Both sprites must share the observed location at actual playback completion."""
    if set(arrival) != {'id', 'mover', 'place', 'end'} or not arrival['id'].startswith('routine:'):
        return False
    people = {r['ref']: r['bounds'] for r in scene['participants']}
    place = next((r['point'] for r in scene.get('objects', ()) if r['ref'] == arrival['place']), None)
    if set(people) != {scene['owner_ref'], peer} or arrival['mover'] not in people or place is None:
        return False
    if people[arrival['mover']][:2] != arrival['end']:
        return False
    def near(bounds):
        x, y, w, h = bounds
        return x-48 <= place[0] <= x+w+48 and y-48 <= place[1] <= y+h+48
    return all(near(bounds) for bounds in people.values())


def validate(app, receipt, contents, *, event_id=None):
    row = thaw_model_data(receipt)
    peer = row['actor_ref']
    activity_ref = row.get('activity_ref')
    arrival = row.get('arrival')
    identity = _identity(app, peer, arrival['id'] if arrival else activity_ref)
    scene = spatial.validate(row['scene'])
    people = {r['ref']: r['label'] for r in scene['participants']}
    if (row['version'] not in VERSIONS or (row['version'] == ACTIVITY_VERSION) != (activity_ref is not None)
            or (activity_ref is not None and not any(obj['ref'] == activity_ref for obj in scene.get('objects', ())))
            or row['event_id'] != identity or event_id not in (None, identity)
            or peer in (app.owner_ref, app.user_ref) or not peer.startswith('npc:')
            or row['reader_ref'] != app.owner_ref or scene['owner_ref'] != app.owner_ref
            or people != {app.owner_ref: people.get(app.owner_ref), peer: row['label']}
            or (arrival is None and row['received_at_ms'] != scene['sampled_at_ms'])
            or (row['version'] == DESKTOP_VERSION) != (arrival is not None)
            or (arrival is not None and (type(row['received_at_ms']) is not int
                or row['received_at_ms'] < scene['sampled_at_ms'] or not proves_arrival(scene, peer, arrival)))
            or row['session_binding_fingerprint'] != app.runtime.session.session_binding.fingerprint
            or row['fingerprint'] != fp({k: v for k, v in row.items() if k != 'fingerprint'})):
        raise ValueError('encounter source differs')
    path = archive_root(app)/'scene-encounters'/(identity.split(':')[1]+'.json')
    if json.loads(path.read_text('utf8')) != row or tuple(contents) != (content(row),):
        raise ValueError('encounter observation differs')
    return row
