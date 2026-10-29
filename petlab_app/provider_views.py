"""Derived audit files, backed by the provider's pre-dispatch durable record.

No model/state/clock/execution authority. Legacy standalone callers stay inline.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from collections.abc import Mapping
import json,logging
from framework.model_api import freeze_model_payload,thaw_model_data,model_data_fingerprint

_ACTIVE=ContextVar('petlab_provider_readonly_views',default=None)
VERSION='petlab-provider-readonly-views-v1'


def _entry(root,path,record):
    root=Path(root).resolve();path=Path(path).resolve()
    try:relative=path.relative_to(root)
    except ValueError:raise ValueError('readonly view path is outside provider archive') from None
    if not isinstance(record,Mapping):raise ValueError('readonly view must be record data')
    material={k:v for k,v in record.items() if k!='fingerprint'}
    if (len(relative.parts)!=2 or relative.parts[0] not in {'measurement-contexts','self-effect-contexts'}
            or path.name!=str(record.get('fingerprint'))+'.json'
            or record.get('fingerprint')!=model_data_fingerprint(material)):
        raise ValueError('readonly view path or integrity differs')
    return {'path':relative.as_posix(),'record':thaw_model_data(record)}


@contextmanager
def scope(gateway):
    current=_ACTIVE.get()
    if current is not None and current[0] is gateway:
        yield;return
    token=_ACTIVE.set((gateway,{}))
    try:yield
    finally:_ACTIVE.reset(token)


def archive(gateway,path,record):
    current=_ACTIVE.get()
    if current is None or current[0] is not gateway:
        from .providers import _save_trace
        return _save_trace(Path(path),record)
    row=_entry(gateway.trace_dir,path,record)
    previous=current[1].get(row['path'])
    if previous is not None and previous!=row:raise ValueError('conflicting readonly view')
    current[1][row['path']]=freeze_model_payload(row)


def take(gateway):
    current=_ACTIVE.get()
    if current is None or current[0] is not gateway:return ()
    rows=tuple(current[1].values());current[1].clear()
    return rows


def publish(gateway,rows):
    if not rows:return
    from .readonly_archive import ReadOnlyArchives
    if gateway._readonly_views is None:gateway._readonly_views=ReadOnlyArchives()
    for row in rows:
        gateway._readonly_views.defer(gateway.trace_dir/row['path'],row['record'])
    gateway._readonly_views.publish()


def restore_views(trace_path):
    """Explicit cold audit repair. Never rerun a provider or mutate its trace."""
    from .providers import _save_trace
    trace_path=Path(trace_path).resolve();record=json.loads(trace_path.read_text('utf8'))
    if record.get('version')!='petlab-provider-trace-v1':raise ValueError('readonly view requires provider trace')
    rows=record.get('readonly_views',())
    if (not isinstance(rows,(tuple,list)) or rows and record.get('readonly_views_version')!=VERSION):
        raise ValueError('readonly views are invalid')
    checked=[];seen=set()
    for row in rows:
        if not isinstance(row,dict) or set(row)!={'path','record'}:raise ValueError('readonly view fields differ')
        path=trace_path.parent/row['path'];actual=_entry(trace_path.parent,path,row['record'])
        if actual['path']!=row['path'] or actual['path'] in seen:raise ValueError('readonly view path differs')
        seen.add(actual['path']);checked.append((path,row['record']))
    # Validate every source before repairing the first file.
    for path,value in checked:
        if path.exists() and json.loads(path.read_text('utf8'))!=value:raise ValueError('existing readonly view integrity differs')
    for path,value in checked:
        if not path.exists():_save_trace(path,value)
    return tuple(path for path,_ in checked)
