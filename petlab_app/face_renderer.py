"""Precomputed face composition; physical frames and timing remain independent."""
import math
from .windows_alpha import AlphaFrame,load_alpha_frames

def compose_face(base,face,source_box,target_box,*,rotation_degrees=0):
    sw,sh=face.width,face.height;w,h=base.width,base.height
    sx0,sy0,sx1,sy1=[v*n for v,n in zip(source_box,(sw,sh,sw,sh))]
    x0,y0,x1,y1=[v*n for v,n in zip(target_box,(w,h,w,h))]
    if x1<=x0 or y1<=y0:raise ValueError('face transform has no area')
    cx,cy=(x0+x1)/2,(y0+y1)/2;hw,hh=(x1-x0)/2,(y1-y0)/2
    angle=math.radians(rotation_degrees);co,si=math.cos(angle),math.sin(angle)
    ex,ey=abs(co)*hw+abs(si)*hh,abs(si)*hw+abs(co)*hh
    pixels=bytearray(base.bgra)
    for y in range(max(0,math.floor(cy-ey)),min(h,math.ceil(cy+ey))):
        for x in range(max(0,math.floor(cx-ex)),min(w,math.ceil(cx+ex))):
            dx,dy=x+.5-cx,y+.5-cy
            ux,uy=co*dx+si*dy+hw,-si*dx+co*dy+hh
            if not (0<=ux<2*hw and 0<=uy<2*hh):continue
            offset=(y*w+x)*4
            if not pixels[offset+3]:continue
            sx=min(sw-1,max(0,sx0+ux/(2*hw)*(sx1-sx0)-.5));sy=min(sh-1,max(0,sy0+uy/(2*hh)*(sy1-sy0)-.5))
            ix,iy=int(sx),int(sy);fx,fy=sx-ix,sy-iy
            values=[0.,0.,0.,0.]
            for px,py,weight in ((ix,iy,(1-fx)*(1-fy)),(min(sw-1,ix+1),iy,fx*(1-fy)),(ix,min(sh-1,iy+1),(1-fx)*fy),(min(sw-1,ix+1),min(sh-1,iy+1),fx*fy)):
                at=(py*sw+px)*4
                for channel in range(4):values[channel]+=face.bgra[at+channel]*weight
            feather=min(1.,min(ux,2*hw-ux,uy,2*hh-uy)/2)
            alpha=values[3]/255*feather
            for c in range(3):pixels[offset+c]=min(pixels[offset+3],round(values[c]*feather*pixels[offset+3]/255+pixels[offset+c]*(1-alpha)))
    return AlphaFrame(w,h,bytes(pixels))

def build_faces(base_frames,manifest,path,base_frame_count):
    transforms=manifest.get('frame_transforms')
    if transforms is not None and len(transforms)!=len(base_frames):raise ValueError('face registration does not cover every animation frame')
    faces=load_alpha_frames(path,manifest['grid'],subsample=1)
    result={}
    for code,index in manifest['codes'].items():
        prepared=[]
        for i,base in enumerate(base_frames):
            transform=transforms[i] if transforms is not None else {'target_box':manifest['idle_face_box'] if i<base_frame_count else manifest['drag_face_box'],'rotation_degrees':0}
            prepared.append(compose_face(base,faces[manifest['neutral_blink_frame'] if code=='normal' and i==2 else index],manifest['source_face_box'],transform['target_box'],rotation_degrees=transform['rotation_degrees']))
        result[code]=tuple(prepared)
    return result
