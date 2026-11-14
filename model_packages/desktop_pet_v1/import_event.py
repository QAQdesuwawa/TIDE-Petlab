"""Type labels for imported facts. These labels carry no execution permission."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ImportedEventTag:
    kind: str
    key: str | None
    semantic_key: str

    def source_record(self):
        return {'kind': self.kind, **({'key': self.key} if self.key is not None else {})}

    @classmethod
    def read(cls, source, *, role, resolved_key=None):
        if not isinstance(source, dict) or 'kind' not in source or set(source) - {'kind', 'key'}:
            raise ValueError('event requires kind and optional key')
        kind = source['kind']
        if (kind, role) not in (('action', 'assistant'), ('interaction', 'user')):
            raise ValueError('event kind must be action for assistant or interaction for user')
        key = source.get('key')
        if 'key' in source and (not isinstance(key, str) or not 0 < len(key) <= 128
                                or key != key.strip() or not key.isprintable()):
            raise ValueError('event key must be a nonempty exact source label of at most 128 characters')
        parent = 'imported_' + kind
        if resolved_key is None:
            resolved_key = key if key in _known_keys(kind) else parent
        elif not isinstance(resolved_key, str) or resolved_key not in (key, parent):
            raise ValueError('saved event subtype differs from source or parent')
        return cls(kind, key, resolved_key)


def _known_keys(kind):
    if kind == 'action':
        from .action_candidates import CATALOG
        from . import basic_actions, movement, shared_meal, web_activity
        return {row['key'] for row in CATALOG['candidates']} | {
            *basic_actions.KEYS, *movement.KEYS, *shared_meal.KEYS, web_activity.KEY,
        }
    return {'touch', 'drag', 'drag_contact', 'mouse.batch'}
