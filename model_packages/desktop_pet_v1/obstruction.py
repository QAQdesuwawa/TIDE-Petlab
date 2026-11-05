"""Owner-observed occupied destination, not user contact or actual motion."""
from .events import Interaction
VERSION='pet-desktop-obstruction-input-v1'

def content(row):
    o=row['observation']
    return Interaction(content_id=row['event_id']+':observation',semantic_key='movement_blocked',
        description=f"自己准备开始下一段移动时，发现预定落脚处被角色 {o['blocker_ref']} 占据；这段尚未迈步，停在本段起点。",
        recipient_refs=(row['reader_ref'],), parameters={'capture_version':VERSION,
            'observer_ref':row['reader_ref'],'blocker_ref':o['blocker_ref'],
            'position':o['position'],'destination':o['destination'],
            'occurred_at_ms':o['occurred_at_ms']})

