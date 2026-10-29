"""Physical reach for a handover; does not assess emotion or willingness."""
from . import spatial
VERSION='desktop-pet-meal-reach-v1'

def check(scene,actor,target):
    value=spatial.validate(scene)
    if value['owner_ref']!=actor or actor==target:raise ValueError('handover geometry owner differs')
    bounds={r['ref']:r['bounds'] for r in value['participants']}
    if actor not in bounds or target not in bounds:raise ValueError('handover participant is not visible')
    a,b=bounds[actor],bounds[target]
    gap=max(0,a[0]-b[0]-b[2],b[0]-a[0]-a[2])
    floor=abs(a[1]+a[3]-b[1]-b[3])
    # Pixel tolerance belongs to the handover animation, never psychological eligibility.
    if gap>64 or floor>64:raise ValueError('handover target is out of reach')
    return {'version':VERSION,'scene_fingerprint':value['fingerprint'],'gap_px':gap,'floor_difference_px':floor}
