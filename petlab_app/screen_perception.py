"""Opt-in foreground metadata and a durable, owner-local observation queue."""
import ctypes
import os
import sys
from ctypes import wintypes
from uuid import uuid4

from framework.model_api import model_data_fingerprint as fp, thaw_model_data
from model_packages.desktop_pet_v1 import spatial
from model_packages.desktop_pet_v1.events import Interaction

from .multi_pet import _immutable, _key, _read
from .product import wall_clock_ms

VERSION = 'pet-desktop-foreground-observation-v1'
ACTOR = 'screen:foreground'
WORLD_VERSION = 'pet-desktop-world-observation-v1'
VERSIONS = frozenset((VERSION, WORLD_VERSION))


class ForegroundWindow:
    """Read one native foreground window; app-owned surfaces do not become objects."""
    def __init__(self, *, allowed_handle=None):
        self.allowed_handle = allowed_handle
        self.api = ctypes.WinDLL('user32', use_last_error=True) if sys.platform == 'win32' else None
        if self.api is not None:
            self.api.GetForegroundWindow.restype = wintypes.HWND
            self.api.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            self.api.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
            self.api.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
            self.api.IsWindowVisible.argtypes = [wintypes.HWND]
            self.api.IsIconic.argtypes = [wintypes.HWND]

    def __call__(self):
        if self.api is None:
            return None
        handle = self.api.GetForegroundWindow()
        if not handle or not self.api.IsWindowVisible(handle) or self.api.IsIconic(handle):
            return None
        pid = wintypes.DWORD()
        self.api.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        if self.allowed_handle is not None:
            if handle != self.allowed_handle:
                return None
        elif pid.value == os.getpid():
            return None
        rect = wintypes.RECT()
        title = ctypes.create_unicode_buffer(128)
        if not self.api.GetWindowRect(handle, ctypes.byref(rect)) or not self.api.GetWindowTextW(handle, title, len(title)):
            return None
        label = ' '.join(title.value.split())[:32]
        if not label or rect.left >= rect.right or rect.top >= rect.bottom:
            return None
        return (handle, pid.value, label, (rect.left, rect.top, rect.right, rect.bottom))


class ScreenPerception:
    def __init__(self, read_foreground):
        self.read_foreground = read_foreground
        self.identity = None
        self.event_ref = None
        self.observed = set()
        self.rect = None
        self.object = None

    def refresh(self):
        row = self.read_foreground()
        if row is None:
            self.object = self.rect = None
            return
        handle, pid, label, rect = row
        identity = (handle, pid, label)
        if identity != self.identity:
            self.identity = identity
            self.event_ref = uuid4().hex
            self.observed.clear()
        left, top, right, bottom = rect
        self.rect = rect
        self.object = spatial.world_object({'ref': spatial.SCREEN_PREFIX + self.event_ref,
            'label': label, 'point': [(left + right)//2, (top + bottom)//2]})

    def visible(self, bounds):
        if self.object is None:
            return False
        left, top, right, bottom = self.rect
        x, y, w, h = bounds
        margin = 240  # Product visibility range, in physical desktop pixels.
        return x <= right+margin and x+w >= left-margin and y <= bottom+margin and y+h >= top-margin

    def queue(self, host, snapshots):
        viewers = sorted(owner for owner, scene in snapshots.items()
                         if scene is not None and any(obj['ref'] == self.object['ref']
                             for obj in scene.get('objects', ()))) if self.object else []
        for owner in viewers:
            if owner in self.observed:
                continue
            alias = host.workspace.aliases[owner]
            value = {'version': VERSION, 'observation': self.event_ref, 'object': self.object,
                     'reader': owner, 'viewers': viewers, 'sampled_at_ms': snapshots[owner]['sampled_at_ms']}
            directory = host.workspace.actor_path(alias)/'scene-inbox'
            _immutable(directory/(_key(self.event_ref)+'.json'), value)
            self.observed.add(owner)


class WorldFacts:
    """Publish declared scene facts on appearance or change, per actual viewer."""
    def __init__(self):
        self.active = {}

    def queue(self, host, snapshots, objects):
        visible = {}
        for owner, scene in snapshots.items():
            if scene is None:
                continue
            for obj in scene.get('objects', ()):
                if 'fact' in obj:
                    visible.setdefault(obj['ref'], {})[owner] = (scene['sampled_at_ms'], obj)
        present = {obj['ref'] for obj in objects if 'fact' in obj}
        self.active = {ref: row for ref, row in self.active.items() if ref in present}
        for ref, observations in visible.items():
            viewers = sorted(observations)
            fact = observations[viewers[0]][1]['fact']
            if any(obj['fact'] != fact for _, obj in observations.values()):
                raise ValueError('desktop fact differs across viewers')
            if ref not in self.active or self.active[ref][0] != fact:
                self.active[ref] = (fact, uuid4().hex, set())
            _, identity, seen = self.active[ref]
            for owner in viewers:
                if owner in seen:
                    continue
                at, obj = observations[owner]
                material = {'version': WORLD_VERSION, 'observation': identity, 'object': obj,
                            'reader': owner, 'viewers': viewers, 'sampled_at_ms': at}
                directory = host.workspace.actor_path(host.workspace.aliases[owner])/'scene-inbox'
                _immutable(directory/(_key(identity)+'.json'), material)
                seen.add(owner)


def _world(material):
    return material['version'] == WORLD_VERSION


def _actor(material):
    return material['object']['ref'] if _world(material) else ACTOR


def content(source):
    obj = source['material']['object']
    if _world(source['material']):
        return Interaction(content_id=source['event_id'], semantic_key='world_observation',
            description=f"现场观察：{obj['fact']}",
            recipient_refs=(source['reader_ref'],),
            parameters={'object_ref': obj['ref'], 'observation_kind': 'world_fact'})
    return Interaction(content_id=source['event_id'], semantic_key='screen_observation',
        description=f"现场观察：当前前台窗口标题为「{obj['label']}」。",
        recipient_refs=(source['reader_ref'],),
        parameters={'object_ref': obj['ref'], 'observation_kind': 'foreground_window'})


def _identity(app, material):
    prefix = 'world-observation:' if _world(material) else 'screen-observation:'
    return prefix+fp([app.runtime.session.session_binding.fingerprint, material['observation']])


def validate(app, receipt, contents, *, event_id=None):
    value = thaw_model_data(receipt)
    material = value['material']
    obj = spatial.world_object(material['object'])
    binding = app.runtime.session.session_binding.fingerprint
    identity = _identity(app, material)
    path = app.store.path.with_suffix('.resources')/'screen'/(_key(identity)+'.json')
    valid_object = 'fact' in obj if _world(material) else obj['ref'] == spatial.SCREEN_PREFIX + material['observation']
    if (value['version'] not in VERSIONS or material['version'] != value['version']
            or value['event_id'] != identity or event_id not in (None, identity)
            or value['reader_ref'] != app.owner_ref or material['reader'] != app.owner_ref
            or app.owner_ref not in material['viewers'] or value['actor_ref'] != _actor(material)
            or value['session_binding_fingerprint'] != binding
            or not valid_object
            or value['received_at_ms'] < material['sampled_at_ms']
            or value['fingerprint'] != fp({k: v for k, v in value.items() if k != 'fingerprint'})
            or _read(path) != value or tuple(contents) != (content(value),)):
        raise ValueError('screen observation source differs')
    return value


def settle(link, material, *, executor, is_cancelled, on_reply_ready):
    product = link.product
    app = product.application
    identity = _identity(app, material)
    path = app.store.path.with_suffix('.resources')/'screen'/(_key(identity)+'.json')
    if path.exists():
        source = _read(path)
        if source['material'] != material:
            raise ValueError('screen queue differs from its archive')
    else:
        at = max(wall_clock_ms(), material['sampled_at_ms'], app.runtime.session.position.position.coordinate)
        source = {'version': material['version'], 'event_id': identity, 'actor_ref': _actor(material), 'reader_ref': app.owner_ref,
                  'session_binding_fingerprint': app.runtime.session.session_binding.fingerprint,
                  'received_at_ms': at, 'material': material}
        source['fingerprint'] = fp(source)
        _immutable(path, source)
    product.interact(event_id=identity, contents=(content(source),), coordinate=source['received_at_ms'],
        input_source=source, response_policy='eligible', executor=executor, is_cancelled=is_cancelled)
    from .peer_reaction import react
    outcome = react(product, identity, executor=executor, is_cancelled=is_cancelled, on_reply_ready=on_reply_ready)
    return {'delivery_id': identity, 'event_id': identity, 'status': 'settled',
            'sender_kind': 'world' if _world(material) else 'screen', 'outcome': outcome['status']}
