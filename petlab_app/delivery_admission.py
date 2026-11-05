"""Intake priority, not occurrence order or a psychological interrupt gate."""
from .visible_delivery import delivery_order


def human_frontier(rows):
    # Public human input is current input for witnesses too; addressing is
    # a visibility/response contract, not permission to prioritize intake.
    return max((delivery_order(r) for r in rows if r['sender_kind']=='human'),default=None)


def ordered(rows,frontier,started=(),*,human_anchor=None):
    started=set(started)
    if human_anchor is None:human_anchor=human_frontier(rows)
    # A frontier can be a terminal checkpoint of a response whose speech is
    # still pending. Prioritize its received siblings, keeping their real order.
    boundary=next((r for r in rows if delivery_order(r)==frontier),None)
    parent=boundary.get('visible',{}).get('parent_event_id') if boundary is not None else None
    def key(row):
        order=delivery_order(row)
        if row['delivery']['delivery_id'] in started:rank=0
        elif row['sender_kind']=='human' and order==human_anchor:rank=1
        elif parent is not None and row.get('visible',{}).get('parent_event_id')==parent:rank=2
        elif frontier is None or order>=frontier:rank=3
        else:rank=4
        return rank,order
    return sorted(rows,key=key)


def after_source(row,source):
    """Old unconsumed history is not a new interruption of a newer intention."""
    return delivery_order(row)>delivery_order(source)


def current_input(row,source,frontier):
    return (source is None or after_source(row,source)) and (frontier is None or delivery_order(row)>=frontier)
