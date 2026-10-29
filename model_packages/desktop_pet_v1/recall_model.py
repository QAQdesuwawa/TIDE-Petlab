"""Select an explicit sealed pet query contract."""
def select(version):
    from . import grounded_recall
    if version in (None,grounded_recall.VERSION):return grounded_recall
    from . import declared_recall
    if version==declared_recall.VERSION:return declared_recall
    from . import source_bound_recall
    if version==source_bound_recall.VERSION:return source_bound_recall
    from . import owner_bound_recall
    if version==owner_bound_recall.VERSION:return owner_bound_recall
    from . import address_recall
    if version==address_recall.VERSION:return address_recall
    from . import hierarchy_recall
    if version==hierarchy_recall.VERSION:return hierarchy_recall
    raise ValueError('unsupported pet recall contract')
