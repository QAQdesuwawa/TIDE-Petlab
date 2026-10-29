"""Deterministic physical geometry only; no model, state or event scheduling.

Ramer-Douglas-Peucker with distance to segments retains collinear reversals.
The tolerance is in screen pixels. Time spans describe equal sampled positions,
not a claim about unobserved motion. Consumers must bind a delivery version.
"""
from __future__ import annotations
import hashlib
import json
import math

VERSION="petlab-trajectory-geometry-v1"

def _segment_distance_squared(point,start,end):
    dx,dy=end[0]-start[0],end[1]-start[1]
    px,py=point[0]-start[0],point[1]-start[1]
    length_squared=dx*dx+dy*dy
    if not length_squared:return px*px+py*py
    ratio=max(0.0,min(1.0,(px*dx+py*dy)/length_squared))
    return (px-ratio*dx)**2+(py-ratio*dy)**2

def _retained_indices(points,tolerance):
    kept={0,len(points)-1};stack=[(0,len(points)-1)];limit=tolerance*tolerance
    while stack:
        start,end=stack.pop()
        if end-start<2:continue
        farthest=max(range(start+1,end),key=lambda index:_segment_distance_squared(points[index],points[start],points[end]))
        if _segment_distance_squared(points[farthest],points[start],points[end])>limit:
            kept.add(farthest);stack.extend(((start,farthest),(farthest,end)))
    return sorted(kept)

def summarize_trajectory(trajectory,*,tolerance_px=2.0):
    """Publish a source-bound ordered summary; original capture is not modified.

    No fixed point count forces extra geometric loss. Arbitrarily intricate paths
    may remain long: a consumer must disclose its own delivery/budget policy.
    """
    if not isinstance(trajectory,(list,tuple)) or len(trajectory)<2:
        raise ValueError("trajectory requires at least two captured samples")
    points=[]
    for point in trajectory:
        if (not isinstance(point,(list,tuple)) or len(point)!=3
                or any(isinstance(value,bool) or not isinstance(value,int) for value in point)):
            raise ValueError("trajectory samples require integer screen x, y and actual milliseconds")
        if points and point[2]<points[-1][2]:raise ValueError("trajectory time moved backwards")
        points.append(tuple(point))
    if (isinstance(tolerance_px,bool) or not isinstance(tolerance_px,(int,float))
            or not math.isfinite(tolerance_px) or tolerance_px<0):
        raise ValueError("trajectory tolerance must be a finite non-negative pixel distance")
    kept=_retained_indices(points,tolerance_px)
    first=points[0];last=points[-1]
    relative=[[points[i][0]-first[0],points[i][1]-first[1],points[i][2]-first[2]] for i in kept]
    max_error=0.0
    for start,end in zip(kept,kept[1:]):
        for index in range(start+1,end):
            max_error=max(max_error,_segment_distance_squared(points[index],points[start],points[end]))
    path_length=sum(math.hypot(b[0]-a[0],b[1]-a[1]) for a,b in zip(points,points[1:]))
    vectors=[(b[0]-a[0],b[1]-a[1]) for a,b in zip(relative,relative[1:]) if a[:2]!=b[:2]]
    signed_turn=sum(math.atan2(a[0]*b[1]-a[1]*b[0],a[0]*b[0]+a[1]*b[1]) for a,b in zip(vectors,vectors[1:]))
    spans=[];index=0
    while index<len(points)-1:
        if points[index][:2]!=points[index+1][:2]:index+=1;continue
        start=index;index+=1
        while index+1<len(points) and points[index][:2]==points[index+1][:2]:index+=1
        spans.append({"sample_indices":[start,index],"relative_time_ms":[points[start][2]-first[2],points[index][2]-first[2]],
            "relative_position_px":[points[start][0]-first[0],points[start][1]-first[1]]})
    canonical=json.dumps(points,separators=(",",":"),allow_nan=False).encode("utf8")
    return {"version":VERSION,"source_sha256":hashlib.sha256(canonical).hexdigest(),
        "source_point_count":len(points),"origin":list(first),"tolerance_px":tolerance_px,
        "retained_indices":kept,"relative_polyline":relative,
        "duration_ms":last[2]-first[2],"net_displacement_px":[last[0]-first[0],last[1]-first[1]],
        "relative_bounds_px":[min(p[0] for p in points)-first[0],min(p[1] for p in points)-first[1],
            max(p[0] for p in points)-first[0],max(p[1] for p in points)-first[1]],
        "path_length_px":path_length,"max_error_px":math.sqrt(max_error),
        "signed_turn_degrees":math.degrees(signed_turn),"turn_source":"simplified ordered segments; not a circle count",
        "same_position_spans":spans,"time_interpretation":"observed callback positions; no inferred continuous stop"}
