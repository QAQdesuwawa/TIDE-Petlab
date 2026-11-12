"""Visible body envelopes; rendering geometry only, never psychological state."""
def body(bounds):
 x,y,w,h=bounds
 # Same transparent horizontal margins used by the whole-pose walk assets.
 return (x+.22*w,y+.08*h,x+.78*w,y+.98*h)

def overlaps(a,b):
 a=body(a);b=body(b)
 return min(a[2],b[2])-max(a[0],b[0])>1 and min(a[3],b[3])-max(a[1],b[1])>1


def obstacle_envelopes(scene, *, include_objects=True):
    for row in scene['participants']:
        if row['ref'] != scene['owner_ref']:
            yield row['ref'], body(row['bounds'])
    if include_objects:
        for obj in scene.get('objects',()):
            bounds=obj.get('bounds')
            if bounds is not None:
                x,y,w,h=bounds
                yield obj['ref'], (x,y,x+w,y+h)


def blocks(bounds, obstacle_ref, envelope):
    left,top,right,bottom=body(bounds)
    l,t,r,b=envelope
    margin=0 if obstacle_ref.startswith('world:') else 1
    return min(right,r)-max(left,l)>margin and min(bottom,b)-max(top,t)>margin


def position_clear(scene, bounds):
    return not any(blocks(bounds,ref,env) for ref,env in obstacle_envelopes(scene))
