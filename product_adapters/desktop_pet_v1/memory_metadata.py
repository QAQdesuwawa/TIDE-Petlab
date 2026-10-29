"""Lossless, card-scoped sharing of identical visible memory metadata.

This presentation transform has no selection, source or state authority.
Event-specific text, markers, times and directed links always stay on each row.
"""
from collections.abc import Mapping
import json
from framework.model_api import thaw_model_data

SHARED='以下记忆共用字段'
ROWS='相关记忆（按发生顺序）'
SHAREABLE=('内容属性','对象','记录含义','说话者')

def _size(value):
    return len(json.dumps(value,ensure_ascii=False,separators=(',',':'),sort_keys=True))

def compact_fields(fields):
    fields=thaw_model_data(fields)
    if SHARED in fields:
        expand_fields(fields)  # Never silently accept an ambiguous scoped view.
        return fields
    rows=fields.get(ROWS)
    if not isinstance(rows,list) or len(rows)<2 or any(not isinstance(r,Mapping) for r in rows):
        return fields
    common={key:rows[0][key] for key in SHAREABLE if key in rows[0]
            and all(key in row and row[key]==rows[0][key] for row in rows)}
    if not common:return fields
    result={SHARED:common,**fields,ROWS:[{k:v for k,v in row.items() if k not in common} for row in rows]}
    return result if _size(result)<_size(fields) else fields

def expand_fields(fields):
    fields=thaw_model_data(fields)
    if SHARED not in fields:return fields
    common=fields[SHARED];rows=fields.get(ROWS)
    if (not isinstance(common,dict) or not common or set(common)-set(SHAREABLE)
            or not isinstance(rows,list) or len(rows)<2
            or any(not isinstance(row,dict) or set(row)&set(common) for row in rows)):
        raise ValueError('ambiguous shared memory metadata')
    return {k:([{**common,**row} for row in rows] if k==ROWS else v)
            for k,v in fields.items() if k!=SHARED}
