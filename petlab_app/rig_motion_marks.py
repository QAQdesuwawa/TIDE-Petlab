"""Display-only motion marks following the existing visible chibi limbs."""
import cv2
import numpy as np
from .cutout_binding import matrices


def behind_limbs(data, rig, pose, size):
    names = [p['id'] for p in rig['parts'] if p['id'].startswith('dot_')
             and pose.get(p['id'], {}).get('opacity', 0) > .5]
    if not names:
        return data
    width, height = size
    transforms = matrices(rig['parts'], pose)
    mask = np.zeros((height, width), np.uint8)
    radius = max(3, round(width * .025))
    for name in names:
        x, y = transforms[name][:2, 2]
        side = -1 if name.endswith('_l') else 1
        center = (round(x + side * radius * 1.7), round(y))
        cv2.ellipse(mask, center, (radius, radius * 2), 0,
                    -65 if side > 0 else 115, 65 if side > 0 else 245,
                    170, max(1, round(width / 220)), cv2.LINE_AA)
    # Only fill transparent background: never paint over face, hair or limbs.
    pixels = np.frombuffer(data, np.uint8).reshape(height, width, 4).copy()
    mask[pixels[:, :, 3] != 0] = 0
    alpha = mask.astype(np.uint16)
    for channel, color in enumerate((115, 70, 52)):
        pixels[:, :, channel] += (alpha * color // 255).astype(np.uint8)
    pixels[:, :, 3] += mask
    return pixels.tobytes()
