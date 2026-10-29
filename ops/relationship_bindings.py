"""Compatibility exports for optional preference relationship bindings."""
from interaction_extensions.preference_calibration.relationship_bindings import (
    HumanNpcB0Binding,
    RelationshipBindingSchemaError,
    bind_human_to_npc,
    resolve_relation_b0,
    revise_relation_b0,
)


__all__ = (
    "HumanNpcB0Binding",
    "RelationshipBindingSchemaError",
    "bind_human_to_npc",
    "resolve_relation_b0",
    "revise_relation_b0",
)
