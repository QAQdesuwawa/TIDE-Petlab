"""Inventory already persisted work; never create or discard a source."""
from product_adapters.desktop_pet_v1 import EVENT_JOURNAL_KEY


def pending_settlement(app):
    product=app._product()
    journal=app.runtime.session.profile.get(EVENT_JOURNAL_KEY,{})
    return (any(key not in journal for key in product['sources']) or
            any(row['status']!='executed' for row in product['executions'].values()))
