"""Optional desktop errands and owner-local, source-bound arrival observations."""
from concurrent.futures import ThreadPoolExecutor

from model_packages.desktop_pet_v1 import movement, spatial

from . import scene_encounter
from .multi_pet import _immutable, _key, _read
from .product import wall_clock_ms


def sighting(snapshot, peer, arrival):
    if snapshot is None:
        return None
    people = [r for r in snapshot['participants'] if r['ref'] in (snapshot['owner_ref'], peer)]
    if len(people) != 2:
        return None
    scene = spatial.create(owner_ref=snapshot['owner_ref'], source_ref=snapshot['source_ref'],
        sampled_at_ms=snapshot['sampled_at_ms'], viewport=snapshot['viewport'],
        participants=people, objects=[o for o in snapshot.get('objects', ()) if o['ref'] == arrival['place']])
    return scene if scene_encounter.proves_arrival(scene, peer, arrival) else None


def publish_arrival(host, identity, alias, place, outcome):
    result = outcome.details.get('action_results', {}).get('activity', {}).get('movement_result')
    if ('activity' not in outcome.completed_content_ids or outcome.details.get('interrupted')
            or not result or result['target_ref'] != place or result['start'] == result['end']):
        return []
    mover = host.configurations[alias].definition.owner_ref
    arrival = {'id': identity, 'mover': mover, 'place': place, 'end': list(result['end'])}
    sent = []
    for reader, window in host.windows.items():
        if reader == alias:
            continue
        other = window.configuration.definition.owner_ref
        for observer, peer in ((reader, mover), (alias, other)):
            owner = host.configurations[observer].definition.owner_ref
            scene = sighting(host.scene.snapshot(owner), peer, arrival)
            if scene is None:
                continue
            path = host.workspace.actor_path(observer)/'scene-inbox'/(_key(identity+'|'+peer)+'.json')
            _immutable(path, {'scene': scene, 'peer': peer, 'arrival': arrival})
            sent.append({'reader': owner, 'peer': peer})
    return sent


def pending(link):
    directory = link.local/'scene-inbox'
    if not directory.is_dir():
        return ()
    return tuple(sorted((p for p in directory.glob('*.json')
                         if not (directory/'settled'/p.name).exists()),
                         key=lambda p: (p.stat().st_mtime_ns, p.name)))


def pump(link, path, *, executor, is_cancelled, on_reply_ready):
    product = link.product
    app = product.application
    material = _read(path)
    from . import screen_perception
    if material.get('version') in screen_perception.VERSIONS:
        link.last_receipt = screen_perception.settle(link, material, executor=executor,
            is_cancelled=is_cancelled, on_reply_ready=on_reply_ready)
        _immutable(path.parent/'settled'/path.name, link.last_receipt)
        return True
    scene, peer, arrival = material['scene'], material['peer'], material['arrival']
    identity = scene_encounter._identity(app, peer, arrival['id'])
    archived = app.store.path.with_suffix('.resources')/'scene-encounters'/(identity.split(':')[1]+'.json')
    if archived.is_file():
        source = _read(archived)
        if source['scene'] != scene or source['arrival'] != arrival:
            raise ValueError('queued desktop encounter differs from its archive')
    else:
        source = scene_encounter.capture(app, scene, peer, arrival=arrival,
            received_at=max(wall_clock_ms(), app.runtime.session.position.position.coordinate,
                            scene['sampled_at_ms']))
    product.interact(event_id=identity, contents=(scene_encounter.content(source),),
        coordinate=source['received_at_ms'], input_source=source, response_policy='eligible',
        executor=executor, is_cancelled=is_cancelled)
    from .peer_reaction import react
    outcome = react(product, identity, executor=executor, is_cancelled=is_cancelled,
                    on_reply_ready=on_reply_ready)
    link.last_receipt = {'delivery_id': identity, 'event_id': identity, 'status': 'settled',
                         'sender_kind': 'npc', 'peer': peer, 'outcome': outcome['status']}
    _immutable(path.parent/'settled'/path.name, link.last_receipt)
    return True


class DesktopRoutines:
    def __init__(self, host, schedule, *, on_arrival):
        self.host = host
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='desktop-routine')
        self.on_arrival = on_arrival
        self.queue = iter(schedule)
        self.closed = False
        self.pending = None
        self.timer = None
        self._next()

    def _next(self):
        row = next(self.queue, None)
        if row is not None:
            self.timer = self.host.root.after(row['delay_ms'], lambda: self._start(row))

    def _start(self, row):
        if self.closed:
            return
        window = self.host.windows[row['alias']]
        if window.closed:
            self._next()
            return
        if window.view.active is not None or window._pending_job_kinds:
            self.timer = self.host.root.after(100, lambda: self._start(row))
            return
        obj = next(o for o in self.host.scene.objects if o['ref'] == row['place'])
        action = movement.content(obj['ref'], {'key': 'move_near', 'description': '走到'+obj['label']+'旁',
                                              'world_object': obj, 'motion': movement.arrival_definition()})
        self.pending = self.worker.submit(window.view.execute_guarded, row['id'], (action,),
                                          is_cancelled=lambda: self.closed)
        self.timer = self.host.root.after(20, lambda: self._finish(row))

    def _finish(self, row):
        if not self.pending.done():
            self.timer = self.host.root.after(20, lambda: self._finish(row))
            return
        try:
            outcome = self.pending.result()
            sent = publish_arrival(self.host, row['id'], row['alias'], row['place'], outcome)
            self.on_arrival(row, outcome, sent, None)
        except Exception as error:
            self.on_arrival(row, None, [], error)
        finally:
            self.pending = None
        self._next()

    def close(self):
        self.closed = True
        if self.timer is not None:
            self.host.root.after_cancel(self.timer)
        self.worker.shutdown(wait=False)
