"""Deterministic desktop obstacle routing; no model or state authority."""
import heapq
import math
from model_packages.desktop_pet_v1.physical_space import body,obstacle_envelopes

def route(scene,start,end,*,reserved=()):
    own=next(r['bounds'] for r in scene['participants'] if r['ref']==scene['owner_ref'])
    w,h=own[2:];vx,vy,vw,vh=scene['viewport']
    envelopes=[bounds for _,bounds in obstacle_envelopes(scene)]
    envelopes.extend(body(bounds) for bounds in reserved)
    obstacles=[(l-.78*w,t-.98*h,r-.22*w,b-.08*h) for l,t,r,b in envelopes]
    def valid(p):
        return vx<=p[0]<=vx+vw-w and vy<=p[1]<=vy+vh-h and not any(l<p[0]<r and t<p[1]<b for l,t,r,b in obstacles)
    def clear(a,b):
        for l,t,r,bt in obstacles:
            lo,hi=0.,1.
            for x,d,mn,mx in ((a[0],b[0]-a[0],l,r),(a[1],b[1]-a[1],t,bt)):
                if not d:
                    if not mn<x<mx:lo,hi=1.,0.;break
                else:
                    x1,x2=sorted(((mn-x)/d,(mx-x)/d));lo=max(lo,x1);hi=min(hi,x2)
            if lo<hi:return False
        return True
    start,end=tuple(start),tuple(end)
    if start==end:return [list(start),list(end)]
    if not valid(start) or not valid(end):return None
    nodes=[start,end]
    for l,t,r,b in obstacles:
        for p in ((math.floor(l)-2,math.floor(t)-2),(math.ceil(r)+2,math.floor(t)-2),
                  (math.floor(l)-2,math.ceil(b)+2),(math.ceil(r)+2,math.ceil(b)+2)):
            if valid(p) and p not in nodes:nodes.append(p)
    queue=[(0.,0)];cost={0:0.};prev={}
    while queue:
        distance,i=heapq.heappop(queue)
        if distance!=cost[i]:continue
        if i==1:
            indices=[1]
            while indices[-1]!=0:indices.append(prev[indices[-1]])
            return [list(nodes[j]) for j in reversed(indices)]
        for j,p in enumerate(nodes):
            if j==i or not clear(nodes[i],p):continue
            new=distance+math.dist(nodes[i],p)
            if new<cost.get(j,float('inf')):
                cost[j]=new;prev[j]=i;heapq.heappush(queue,(new,j))
    return None

def sample(points,fraction,*,heading=0):
    """Position and horizontal facing of the same segment of the route."""
    lengths=[math.dist(a,b) for a,b in zip(points,points[1:])]
    remaining=sum(lengths)*max(0.,min(1.,fraction))
    for a,b,length in zip(points,points[1:],lengths):
        dx=b[0]-a[0]
        if dx:heading=1 if dx>0 else -1
        if remaining<=length and length:
            return [round(x+(y-x)*remaining/length) for x,y in zip(a,b)],heading
        remaining-=length
    return list(points[-1]),heading


def point(points,fraction):
    return sample(points,fraction)[0]
