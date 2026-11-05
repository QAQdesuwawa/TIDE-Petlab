"""Apply the existing gaze warp in material coordinates, before parent motion."""
import numpy as np
from .cutout_binding import matrices


def material_transform(rig,renderer,horizontal,vertical):
    part=rig['gaze_part']
    def transform(identity,texture,code,pose):
        if identity!=part:return texture
        linear=matrices(rig['parts'],pose)[part][:2,:2]
        # Remove material/display scaling; preserve rotation and reflection.
        axes=linear/np.linalg.norm(linear,axis=0)
        local=np.linalg.solve(axes,np.array([horizontal,vertical]))
        options={'layout':rig['gaze_layout']} if 'gaze_layout' in rig else {}
        return renderer.apply(texture,float(np.clip(local[0],-1,1)),
                              float(np.clip(local[1],-1,1)),code,**options)
    transform.for_view=lambda view:material_transform(view,renderer,horizontal,vertical)
    return transform
