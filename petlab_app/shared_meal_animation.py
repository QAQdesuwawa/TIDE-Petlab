"""Shared, display-only meal choreography for rigs with independent arms."""
from functools import lru_cache
import json
from pathlib import Path

PROP = 'shared_rice_bowl'
VERSION = 'petlab-bound-shared-meal-v1'


def _channel(part, parameter, amplitude, curve):
    return dict(part=part, parameter=parameter, amplitude=amplitude,
                curve=[list(point) for point in curve])


def _clips():
    pulse = ((0, 0), (.30, 1), (.70, 1), (1, 0))
    extend = ((0, 0), (.65, 1), (1, 1))
    bites = ((0, 0), (.15, 1), (.30, 0), (.45, 1), (.60, 0), (.75, 1), (1, 0))
    steady = ((0, 1), (.50, 1), (1, 1))
    held = [
        _channel(PROP, 'opacity', 1, steady),
        _channel('left_arm', 'angle', -28, steady),
        _channel('right_arm', 'angle', 28, steady),
    ]

    def clip(duration, channels):
        return dict(source='bound_rig', duration_ms=duration, channels=channels)

    return {
        'eat_rice': clip(2400, held + [
            _channel('head', 'y', 12, bites), _channel(PROP, 'y', -12, bites),
            _channel('left_arm', 'y', -12, bites), _channel('right_arm', 'y', -12, bites),
        ]),
        'request_bowl': clip(1200, [
            _channel('right_arm', 'angle', -55, pulse), _channel('head', 'angle', -5, pulse),
        ]),
        'offer_bowl': clip(1400, held + [
            _channel(PROP, 'x', 40, extend),
            _channel('left_arm', 'x', 34, extend), _channel('right_arm', 'x', 36, extend),
        ]),
        'keep_bowl': clip(1200, held + [
            _channel(PROP, 'y', -10, pulse),
            _channel('left_arm', 'y', -10, pulse), _channel('right_arm', 'y', -10, pulse),
        ]),
        # The receiver reaches first. The existing committed transfer puts
        # the sole bowl in the receiver's held pose.
        'accept_bowl': clip(1400, [_channel('right_arm', 'angle', -65, pulse)]),
        'decline_bowl': clip(1200, [
            _channel('right_arm', 'angle', -50, pulse),
            _channel('head', 'angle', 7, ((0, 0), (.25, 1), (.50, -1), (.75, 1), (1, 0))),
        ]),
    }


def read_activities(asset_dir):
    path = Path(asset_dir) / 'activities.json'
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding='utf8'))
    if 'shared_meal' not in data:
        return data
    if data.pop('shared_meal') != VERSION or not data.get('rig_binding'):
        raise ValueError('invalid shared meal activity declaration')
    shared = _clips()
    if shared.keys() & data['clips'].keys():
        raise ValueError('duplicate shared meal activity')
    return {**data, 'clips': {**data['clips'], **shared}}


@lru_cache(maxsize=1)
def _texture():
    """Draw one small independent porcelain prop in the existing alpha format."""
    import cv2
    import numpy as np
    from .windows_alpha import AlphaFrame

    width, height, scale = 88, 58, 3
    canvas = np.zeros((height * scale, width * scale, 4), dtype=np.uint8)

    def fill(points, color):
        cv2.fillConvexPoly(canvas, np.array(points, np.int32) * scale, color)

    def ellipse(center, axes, color):
        cv2.ellipse(canvas, tuple(n * scale for n in center),
                    tuple(n * scale for n in axes), 0, 0, 360, color, -1)

    fill([(5, 29), (83, 29), (74, 44), (62, 54), (26, 54), (14, 44)], (110, 80, 57, 255))
    fill([(8, 29), (80, 29), (72, 43), (61, 51), (27, 51), (16, 43)], (242, 214, 174, 255))
    ellipse((44, 29), (40, 11), (110, 80, 57, 255))
    ellipse((44, 28), (37, 8), (218, 176, 132, 255))
    for center, axes in [((22, 23), (12, 7)), ((36, 17), (13, 10)),
                         ((51, 15), (14, 11)), ((65, 23), (14, 7))]:
        ellipse(center, axes, (235, 243, 248, 255))
    ellipse((44, 24), (27, 8), (250, 251, 253, 255))
    pixels = cv2.resize(canvas, (width, height), interpolation=cv2.INTER_AREA)
    return AlphaFrame(width, height, pixels.tobytes())


def validate_side_hold(offsets):
    from math import isfinite

    if set(offsets) != {'left_arm', 'right_arm', PROP}:
        raise ValueError('side bowl pose requires both shoulders and the bowl')
    for row in offsets.values():
        if set(row) - {'x', 'y', 'angle'} or not all(
                type(value) in (int, float) and isfinite(value) for value in row.values()):
            raise ValueError('invalid side bowl pose offsets')
    return offsets


def side_hold_pose(rig, pose):
    offsets = rig.get('meal_side_hold')
    visibility = pose.get(PROP, {}).get('opacity', 0)
    if not offsets or visibility <= 0:
        return pose
    result = dict(pose)
    for part, row in offsets.items():
        adjusted = dict(pose.get(part, {}))
        if 'x' in adjusted:
            # The front reach slides a whole arm cutout. Limit that slide
            # in profile so the sleeve stays seated at the shoulder.
            scale = .5 if part == PROP else .35
            adjusted['x'] *= 1 + visibility * (scale - 1)
        for name, delta in row.items():
            adjusted[name] = adjusted.get(name, 0) + visibility * delta
        result[part] = adjusted
    return result


def install_prop(rig):
    if PROP in rig['base']:
        return
    texture = _texture()
    anchor = [texture.width / 2, texture.height / 2]
    for direction, view in rig.get('views', {0: rig}).items():
        ids = {part['id'] for part in view['parts']}
        if not {'body', 'head', 'left_arm', 'right_arm'} <= ids:
            raise ValueError('shared meal needs bound body, head and arms')
        view['parts'].append(dict(id=PROP, texture=PROP, parent='body',
                                  joint=[direction * 12, 76], anchor=anchor, order=.5))
        view['textures'][PROP] = texture
        view['base'][PROP] = {'opacity': 0}

    # Use the ordinary part registration so that the bowl travels through the
    # same continuous turn as the body, including when held between actions.
    points = [[x, y] for y in (-anchor[1], 0, anchor[1])
              for x in (-anchor[0], 0, anchor[0])]
    triangles = [[0,1,4],[0,4,3],[1,2,5],[1,5,4],
                 [3,4,7],[3,7,6],[4,5,8],[4,8,7]]
    for rows in rig.get('_view_registration', {}).values():
        rows[PROP] = dict(target=PROP, source=points, destination=points,
                          triangles=triangles, handoff=.5)
