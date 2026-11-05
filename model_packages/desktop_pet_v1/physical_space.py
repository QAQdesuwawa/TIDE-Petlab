"""Visible body envelopes; rendering geometry only, never psychological state."""
def body(bounds):
 x,y,w,h=bounds
 # Same transparent horizontal margins used by the whole-pose walk assets.
 return (x+.22*w,y+.08*h,x+.78*w,y+.98*h)

def overlaps(a,b):
 a=body(a);b=body(b)
 return min(a[2],b[2])-max(a[0],b[0])>1 and min(a[3],b[3])-max(a[1],b[1])>1
