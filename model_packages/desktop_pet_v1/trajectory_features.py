"""Fixed-size physical interval features, O(n); no psychological inference."""
import hashlib
import json
import math

VERSION = 'petlab-trajectory-features-v1'


def summarize_features(points, *, allow_point=False):
    if not isinstance(points, (list, tuple)) or len(points) < (1 if allow_point else 2):
        raise ValueError('trajectory requires at least two samples')
    for i, p in enumerate(points):
        if (len(p) != 3 or any(isinstance(v, bool) or not isinstance(v, int) for v in p)
                or (i and p[2] < points[i-1][2])):
            raise ValueError('invalid physical trajectory')
    first, last = points[0], points[-1]
    distance = signed_turn = absolute_turn = 0.0
    pause = longest_pause = run = 0
    reversals_x = reversals_y = 0
    previous_x = previous_y = 0
    previous_vector = None
    for a, b in zip(points, points[1:]):
        dx, dy = b[0]-a[0], b[1]-a[1]
        distance += math.hypot(dx, dy)
        if dx == dy == 0:
            run += b[2]-a[2]
            pause += b[2]-a[2]
            longest_pause = max(longest_pause, run)
            continue
        run = 0
        sx, sy = (dx > 0)-(dx < 0), (dy > 0)-(dy < 0)
        if sx:
            reversals_x += bool(previous_x and sx != previous_x)
            previous_x = sx
        if sy:
            reversals_y += bool(previous_y and sy != previous_y)
            previous_y = sy
        if previous_vector is not None:
            px, py = previous_vector
            turn = math.degrees(math.atan2(px*dy-py*dx, px*dx+py*dy))
            signed_turn += turn
            absolute_turn += abs(turn)
        previous_vector = dx, dy
    rounded = lambda value: float(format(value, '.6g'))
    return {
        'version': VERSION,
        'source_sha256': hashlib.sha256(json.dumps(points, separators=(',', ':')).encode()).hexdigest(),
        'source_point_count': len(points), 'duration_ms': last[2]-first[2],
        'net_displacement_px': [last[0]-first[0], last[1]-first[1]],
        'relative_bounds_px': [min(p[0] for p in points)-first[0], min(p[1] for p in points)-first[1],
                               max(p[0] for p in points)-first[0], max(p[1] for p in points)-first[1]],
        'path_length_px': rounded(distance), 'signed_turn_degrees': rounded(signed_turn),
        'absolute_turn_degrees': rounded(absolute_turn),
        'axis_reversals': [reversals_x, reversals_y],
        'sampled_pause_ms': pause, 'longest_sampled_pause_ms': longest_pause,
        'interpretation': 'whole new interval; sampled positions only; turns are not circle counts; reversals include pointer jitter; no ordered path delivered',
    }
