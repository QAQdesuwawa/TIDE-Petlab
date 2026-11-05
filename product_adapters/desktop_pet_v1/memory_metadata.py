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


GROUPS='记忆字段组'
GROUP_REF='字段组参照'


def compact_grouped_fields(fields):
    """Share repeated metadata within this card, preserving every event row."""
    fields=expand_fields(fields)
    rows=fields.get(ROWS)
    if not isinstance(rows,list) or len(rows)<2:return fields
    if GROUPS in fields or any(GROUP_REF in row for row in rows):
        expand_grouped_fields(fields)
        return fields
    by_signature={}
    for index,row in enumerate(rows):
        common={k:row[k] for k in SHAREABLE if k in row}
        if common:
            key=json.dumps(common,ensure_ascii=False,sort_keys=True,separators=(',',':'))
            by_signature.setdefault(key,[]).append(index)
    groups={};replacements={}
    for key,indices in by_signature.items():
        if len(indices)<2:continue
        value=json.loads(key);label='组'+str(len(groups)+1)
        proposed=[{GROUP_REF:label,**{k:v for k,v in rows[i].items() if k not in value}} for i in indices]
        if _size({label:value})+_size(proposed)>=_size([rows[i] for i in indices]):continue
        groups[label]=value
        replacements.update(zip(indices,proposed))
    baseline=compact_fields(fields)
    if not groups:return baseline
    result={**fields,GROUPS:groups,ROWS:[replacements.get(i,row) for i,row in enumerate(rows)]}
    return result if _size(result)<_size(baseline) else baseline


def expand_grouped_fields(fields):
    fields=thaw_model_data(fields)
    if GROUPS not in fields:return expand_fields(fields)
    groups=fields[GROUPS];rows=fields.get(ROWS)
    if SHARED in fields or not isinstance(groups,dict) or not groups or not isinstance(rows,list):
        raise ValueError('ambiguous grouped memory metadata')
    if any(not isinstance(g,dict) or not g or set(g)-set(SHAREABLE) for g in groups.values()):
        raise ValueError('invalid grouped memory metadata')
    expanded=[]
    for row in rows:
        if not isinstance(row,dict):raise ValueError('invalid grouped memory row')
        label=row.get(GROUP_REF)
        if label is None:expanded.append(row);continue
        if label not in groups or (set(row)-{GROUP_REF})&set(groups[label]):
            raise ValueError('ambiguous grouped memory reference')
        expanded.append({**groups[label],**{k:v for k,v in row.items() if k!=GROUP_REF}})
    return {k:(expanded if k==ROWS else v) for k,v in fields.items() if k!=GROUPS}
