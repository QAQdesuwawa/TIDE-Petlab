"""Deterministic screen-space placement, independent of conversation scheduling."""
def overlap(a,b):
    return max(0,min(a[0]+a[2],b[0]+b[2])-max(a[0],b[0]))*max(0,min(a[1]+a[3],b[1]+b[3])-max(a[1],b[1]))

def arrange(rows,screen,gap=12):
    result={};placed=[];sw,sh=screen
    for key,x,y,w,h in sorted(rows,key=lambda r:(r[1],str(r[0]))):
        clamp=lambda px,py:(max(0,min(px,max(0,sw-w))),max(0,min(py,max(0,sh-h))),w,h)
        candidates=[clamp(x,y)]
        for a,b,c,d in placed:
            candidates.extend((clamp(a+c+gap,y),clamp(a-w-gap,y),clamp(x,b-h-gap),clamp(x,b+d+gap)))
        def score(r):
            return (sum(overlap(r,(a-gap,b-gap,c+2*gap,d+2*gap)) for a,b,c,d in placed),
                    abs(r[0]-x)+2*abs(r[1]-y))
        chosen=min(candidates,key=score);result[key]=chosen;placed.append(chosen)
    return result
