"""Versioned storage-only deltas over an ordinary framework checkpoint.
Every returned save is flushed before the host publishes state. No clock/model logic.
"""
from collections.abc import Mapping
from framework.model_api.immutable_record import ImmutableModelRecord
import json, os, tempfile, math, uuid
try:
    import orjson as _native_json
except ImportError:
    _native_json=None
from pathlib import Path
from framework.model_api import _canonical_model_data, _fingerprint_canonical_model_data, model_data_fingerprint
from ops.runtime_full_memory_profile_commit import FRAMEWORK_HISTORY_PROFILE_KEY

VERSION = 'petlab-checkpoint-delta-v1'
_MISSING = object()

def _native_default(value):
    if isinstance(value,Mapping):return dict(value)
    raise TypeError('not native JSON data')

def _same(value, old):
    if value is old: return True
    if type(value) is ImmutableModelRecord and type(old) is ImmutableModelRecord:
        return value.data_fingerprint == old.data_fingerprint
    t=type(value)
    if value is None or t in (str,bool,int,float): return type(old) is t and value == old and (t is not float or value!=0 or math.copysign(1,value)==math.copysign(1,old))
    if isinstance(value, Mapping):
        return isinstance(old,Mapping) and len(value)==len(old) and all(type(k) is str and k in old and _same(v,old[k]) for k,v in value.items())
    if isinstance(value,(list,tuple)):
        return isinstance(old,list) and len(value)==len(old) and all(_same(a,b) for a,b in zip(value,old))
    return False

def _reconcile(value, old, path, patches):
    if type(value) is ImmutableModelRecord:
        if not _same(value,old):
            patches.append(['set',list(path),_canonical_model_data(value)])
        # After the first complete comparison, retain the protected source
        # object. A mutable old dict never grants an identity shortcut.
        return value
    if value is old:return old
    if not isinstance(value,(Mapping,list,tuple)) and _same(value,old):return old
    if isinstance(value,Mapping) and isinstance(old,dict) and all(type(k) is str for k in value):
        new=dict(old)
        for k in old.keys()-value.keys():
            del new[k];patches.append(['delete',list(path)+[k],None])
        for k,v in value.items(): new[k]=_reconcile(v,old.get(k,_MISSING),path+(k,),patches)
        return new
    if isinstance(value,(list,tuple)) and isinstance(old,list) and len(value)>=len(old):
        new=list(old)
        for i,v in enumerate(value[:len(old)]):new[i]=_reconcile(v,old[i],path+(i,),patches)
        if len(value)>len(old):
            tail=value[len(old):];extra=_canonical_model_data(tail)
            new.extend(v if type(v) is ImmutableModelRecord else encoded for v,encoded in zip(tail,extra))
            patches.append(['append',list(path),extra])
        return new
    new=_canonical_model_data(value);patches.append(['set',list(path),new]);return new

def _apply(material,patch):
    operation,path,value=patch
    target=material
    for key in path[:-1]:target=target[key]
    if operation=='set':
        if not path:raise ValueError('checkpoint delta cannot replace envelope')
        target[path[-1]]=value
    elif operation=='delete': del target[path[-1]]
    elif operation=='append':
        for key in path[-1:]:target=target[key]
        if not isinstance(target,list) or not isinstance(value,list):raise ValueError('invalid checkpoint array append')
        target.extend(value)
    else:raise ValueError('invalid checkpoint delta operation')

def _atomic(path,data):
    fd,tmp=tempfile.mkstemp(prefix=path.name+'.',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

class CheckpointJournal:
    def __init__(self,path,base):
        self.path=Path(str(path)+'.delta.jsonl');self.base=base['fingerprint']
        self.material={k:v for k,v in base.items() if k!='fingerprint'}
        self.previous=self.base;self.sequence=0;self.failed=False
        self.last_write_bytes=0

    @classmethod
    def create(cls,path,base):
        result=cls(path,base)
        header={'version':VERSION,'base':result.base,'session_id':base['session_id'],'session_binding_fingerprint':base['session_binding_fingerprint']}
        _atomic(result.path,(json.dumps(header,separators=(',',':'))+'\n').encode())
        return result

    @classmethod
    def load(cls,path,base):
        result=cls(path,base);raw=result.path.read_bytes();lines=raw.splitlines(keepends=True)
        if not lines:raise ValueError('empty checkpoint delta journal')
        header=json.loads(lines[0])
        if header!={'version':VERSION,'base':result.base,'session_id':base['session_id'],'session_binding_fingerprint':base['session_binding_fingerprint']}:raise ValueError('checkpoint delta base differs')
        valid_size=len(lines[0])
        for index,line in enumerate(lines[1:],1):
            try:row=json.loads(line)
            except (ValueError,UnicodeError):
                if index!=len(lines)-1 or line.endswith(b'\n'):raise ValueError('checkpoint delta is corrupt')
                # Preserve a torn, uncommitted tail before repairing appendability.
                backup=Path(str(result.path)+'.incomplete')
                if backup.exists():backup=Path(str(backup)+'.'+uuid.uuid4().hex)
                _atomic(backup,line);_atomic(result.path,raw[:valid_size]);break
            if set(row)!={'version','base','sequence','previous','patches','fingerprint'} or row['version']!=VERSION or row['base']!=result.base or type(row['sequence']) is not int or row['sequence']!=result.sequence+1 or row['previous']!=result.previous:raise ValueError('checkpoint delta lineage differs')
            material={k:v for k,v in row.items() if k!='fingerprint'}
            # json.loads has already produced only plain JSON keys/containers.
            # The original canonical encoder still checks every value (including
            # rejecting NaN/Infinity) and verifies the exact original row hash.
            # Avoid allocating a second complete copy of the decoded patch graph.
            if _fingerprint_canonical_model_data(material)!=row['fingerprint']:raise ValueError('checkpoint delta fingerprint differs')
            for patch in row['patches']:_apply(result.material,patch)
            result.previous=row['fingerprint'];result.sequence=row['sequence'];valid_size+=len(line)
        result._sync_history();return result

    def _sync_history(self):
        history=self.material['profile'].get(FRAMEWORK_HISTORY_PROFILE_KEY)
        if history is not None:self.material['history']=history

    def adopt_unchanged_profile(self,profile):
        """Warm the storage baseline with equal, protected runtime rows.

        Called after restoration, before accepting input. Full comparison is
        mandatory once; differences cannot become an uncommitted baseline.
        Only _reconcile's factory-immutable leaves are borrowed. No disk write,
        ordinal, chain fingerprint, or mutable caller container is adopted.
        """
        if self.failed:raise RuntimeError('checkpoint delta write failed; reopen before continuing')
        patches=[]
        updated=_reconcile(profile,self.material['profile'],('profile',),patches)
        if patches:return False
        self.material={**self.material,'profile':updated}
        self._sync_history()
        return True

    def save(self,*,profile,ordinal,history):
        if self.failed:raise RuntimeError('checkpoint delta write failed; reopen before continuing')
        patches=[]
        updated=_reconcile(profile,self.material['profile'],('profile',),patches)
        fallback_history=None
        if FRAMEWORK_HISTORY_PROFILE_KEY not in profile:
            fallback_history=_reconcile(history,self.material['history'],('history',),patches)
        if ordinal!=self.material['terminal_runtime_ordinal']:patches.append(['set',['terminal_runtime_ordinal'],ordinal])
        row={'version':VERSION,'base':self.base,'sequence':self.sequence+1,'previous':self.previous,'patches':patches}
        row['fingerprint']=model_data_fingerprint(row)
        encoded=(json.dumps(row,ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n').encode('utf-8')
        try:
            with self.path.open('ab') as f:f.write(encoded);f.flush();os.fsync(f.fileno())
        except BaseException:self.failed=True;raise
        self.material={**self.material,'profile':updated,'terminal_runtime_ordinal':ordinal};self._sync_history()
        if fallback_history is not None:self.material['history']=fallback_history
        self.previous=row['fingerprint'];self.sequence=row['sequence'];self.last_write_bytes=len(encoded)

    def checkpoint(self):
        # Export on demand, never hash/copy the full growing archive on every save.
        payload={**self.material,'fingerprint':_fingerprint_canonical_model_data(self.material)}
        if _native_json is not None:
            try:
                # Encoding here only makes an independent plain-JSON copy. The
                # fingerprint above still uses the original canonical encoder.
                return json.loads(_native_json.dumps(payload,default=_native_default))
            except (TypeError,ValueError):
                # Preserve stdlib support for arbitrarily large Python integers
                # and legacy text that the optional native encoder cannot store.
                pass
        return json.loads(json.dumps(payload,ensure_ascii=False,allow_nan=False,default=_native_default))
