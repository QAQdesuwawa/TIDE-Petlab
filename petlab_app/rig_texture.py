"""One-time authored texture registration into a common local canvas."""
import cv2
import numpy as np
from .windows_alpha import AlphaFrame

def register(source,declaration):
    if declaration is None:return source
    if not isinstance(declaration,dict) or set(declaration)-{'source_rect'}!={'size','source_to_local'}:raise ValueError('invalid texture registration')
    size=declaration['size']
    if not isinstance(size,list) or len(size)!=2 or any(type(v) is not int or not 1<=v<=4096 for v in size):raise ValueError('invalid registration canvas')
    matrix=np.asarray(declaration['source_to_local'],dtype=float)
    if matrix.shape!=(2,3) or not np.isfinite(matrix).all() or abs(np.linalg.det(matrix[:,:2]))<1e-8:raise ValueError('invalid texture transform')
    pixels=np.frombuffer(source.bgra,np.uint8).reshape(source.height,source.width,4)
    if 'source_rect' in declaration:
        rect=declaration['source_rect']
        if not isinstance(rect,list) or len(rect)!=4 or any(type(v) is not int for v in rect):raise ValueError('invalid texture source rectangle')
        x,y,w,h=rect
        if min(x,y)<0 or min(w,h)<1 or x+w>source.width or y+h>source.height:raise ValueError('texture source rectangle outside image')
        pixels=pixels[y:y+h,x:x+w]
    result=cv2.warpAffine(pixels,matrix,tuple(size),flags=cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)
    return AlphaFrame(*size,result.tobytes())
