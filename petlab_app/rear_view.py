"""Authored rear sprites over the existing display clock, never actor state."""
import json
import math
from pathlib import Path
import cv2
import numpy as np
from .windows_alpha import AlphaFrame, load_alpha_frames


def route_vector(points, fraction):
    lengths = [math.dist(a, b) for a, b in zip(points, points[1:])]
    remaining = sum(lengths) * max(0., min(1., fraction))
    result = (0., 0.)
    for a, b, length in zip(points, points[1:], lengths):
        if not length:
            continue
        result = (b[0] - a[0], b[1] - a[1])
        if remaining <= length:
            return result
        remaining -= length
    return result


def is_rear(vector, previous=False):
    x, y = vector
    if not x and not y:
        return previous
    # Angular hysteresis keeps a diagonal route from flickering at a sector edge.
    return y < 0 and -y > abs(x) * (.85 if previous else 1.15)


class RearPresenter:
    def __init__(self, root):
        path = Path(root) / 'rear.json'
        self.config = json.loads(path.read_text('utf-8-sig')) if path.exists() else None
        self.image = None
        self.target = self.shown = False
        self.started = -1.
        self.duration = .24
        self.base = None

    def prepare(self, reference):
        if self.base is not None or self.config is None:
            return
        cfg = self.config
        # Resolved by bind(), kept local to the installed asset tree.
        image = self.path
        import hashlib
        if hashlib.sha256(image.read_bytes()).hexdigest() != cfg['sha256']:
            raise ValueError('rear artwork fingerprint differs')
        cell = load_alpha_frames(image, cfg['grid'], subsample=1)[cfg['index']]
        src = np.frombuffer(cell.bgra, np.uint8).reshape(cell.height, cell.width, 4)
        ys, xs = np.where(src[:, :, 3] > 32)
        src = src[ys.min():ys.max()+1, xs.min():xs.max()+1]
        ref = np.frombuffer(reference.bgra, np.uint8).reshape(reference.height, reference.width, 4)
        ry, rx = np.where(ref[:, :, 3] > 32)
        height = int(ry.max()-ry.min()+1)
        width = round(src.shape[1]*height/src.shape[0])
        self.base = np.zeros_like(ref)
        scaled = cv2.resize(src, (width, height), interpolation=cv2.INTER_AREA)
        left = max(0, (reference.width-width)//2)
        top = int(ry.max()+1-height)
        self.base[top:top+height, left:left+width] = scaled[:, :self.base.shape[1]-left]
        self.feet_y = top + round(height*cfg['feet_start'])
        self.center = (left+width/2, float(ry.max()))
        self.rows, self.columns = np.indices(ref.shape[:2], dtype=np.float32)
        # Anchor the deformation at the hem and reach full lift at the sole.
        leg_height = max(1., self.center[1]-self.feet_y)
        ramp = np.clip((self.rows-self.feet_y)/leg_height, 0, 1)
        self.foot_weight = ramp*ramp*(3-2*ramp)
        self.foot_left = np.clip((self.center[0]+2-self.columns)/4, 0, 1)
        self.step_height = min(height*.048, leg_height*.32)

    def bind(self, root):
        if self.config:
            self.path = (Path(root)/self.config['image']).resolve()
        return self

    def present(self, front, vector, now, *, phase=None, count=32, enabled=True):
        if self.config is None:
            return front, False
        self.prepare(front)
        wanted = is_rear(vector, self.target) if enabled else False
        if wanted != self.target:
            self.target = wanted
            self.started = now
        t = min(1., max(0., (now-self.started)/self.duration))
        if t >= .5:
            self.shown = self.target
        if not self.shown and t >= 1:
            return front, False
        data = self.base.copy() if self.shown else np.frombuffer(front.bgra, np.uint8).reshape(front.height,front.width,4).copy()
        if self.shown and phase is not None:
            # Both feet share a continuous join at the hem. Only exposed legs
            # shorten alternately; the upper material keeps its authored outline.
            stride=math.sin(2*math.pi*phase/max(1,count))
            swing=(self.foot_left*max(0,stride)+(1-self.foot_left)*max(0,-stride))*self.foot_weight
            lift=self.rows+swing*self.step_height
            sway=self.columns+swing*self.step_height*.24*(2*self.foot_left-1)
            data=cv2.remap(data,sway,lift,cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)
        if t < 1:
            # Single silhouette through the turn, never an overlapping face/back.
            scale=1-.13*math.sin(math.pi*t)
            x=self.center[0]
            data=cv2.warpAffine(data,np.array([[scale,0,x*(1-scale)],[0,1,0]],float),(front.width,front.height))
        return AlphaFrame(front.width,front.height,data.tobytes()), t<1
