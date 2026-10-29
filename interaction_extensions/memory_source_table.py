"""Lossless structural sharing for numeric source inputs; no model semantics.

Repeated strings, vectors, mappings and arrays appear once in a backward-only
value table. This is inspectable structured JSON, not compressed text. The
normal stage signature binds the exact encoded table; the consumer still runs
its original numeric/source validation after decoding.
"""
from collections.abc import Mapping
import math

VERSION = 'memory-shared-value-table-v1'
CONTEXT_KEY = 'memory_source_encoding'


def encode(value):
    table, index = [], {}
    def visit(item):
        if isinstance(item, Mapping):
            if any(not isinstance(key, str) for key in item):
                raise ValueError('source mapping keys must be strings')
            tag, data = 'map', tuple((visit(key), visit(item[key])) for key in sorted(item))
        elif isinstance(item, (tuple, list)):
            tag, data = 'array', tuple(visit(val) for val in item)
        elif isinstance(item, str):
            tag, data = 'string', item
        elif item is None:
            tag, data = 'null', None
        elif type(item) is bool:
            tag, data = 'bool', item
        elif type(item) is int:
            tag, data = 'int', item
        elif type(item) is float and math.isfinite(item):
            # Preserve -0.0 as distinct from +0.0, and ints from floats.
            tag, data = 'float', item
        else:
            raise ValueError('unsupported source value type')
        key = (tag, data.hex() if tag == 'float' else data)
        if key in index:
            return index[key]
        identity = len(table)
        index[key] = identity
        table.append([tag, data])
        return identity
    root = visit(value)
    return {'format': VERSION, 'table': table, 'root': root}


def decode(record):
    if (not isinstance(record, Mapping) or set(record) != {'format', 'table', 'root'}
            or record['format'] != VERSION or not isinstance(record['table'], (tuple, list))):
        raise ValueError('invalid shared source table contract')
    values = []
    def ref(identity):
        if type(identity) is not int or not 0 <= identity < len(values):
            raise ValueError('shared source reference must point backward')
        return values[identity]
    for entry in record['table']:
        if not isinstance(entry, (tuple, list)) or len(entry) != 2:
            raise ValueError('invalid shared source table entry')
        tag, data = entry
        if tag == 'map' and isinstance(data, (tuple, list)):
            value = {}
            for pair in data:
                if not isinstance(pair, (tuple, list)) or len(pair) != 2:
                    raise ValueError('invalid shared source mapping entry')
                key, val = ref(pair[0]), ref(pair[1])
                if not isinstance(key, str) or key in value:
                    raise ValueError('invalid or duplicate shared source key')
                value[key] = val
        elif tag == 'array' and isinstance(data, (tuple, list)):
            value = [ref(identity) for identity in data]
        elif tag == 'string' and isinstance(data, str):
            value = data
        elif tag == 'null' and data is None:
            value = None
        elif tag == 'bool' and type(data) is bool:
            value = data
        elif tag == 'int' and type(data) is int:
            value = data
        elif tag == 'float' and type(data) is float and math.isfinite(data):
            value = data
        else:
            raise ValueError('unsupported shared source entry')
        values.append(value)
    return ref(record['root'])
