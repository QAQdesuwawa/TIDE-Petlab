"""Versioned storage for exact HistoryRead identities, not recall mathematics."""
from bisect import bisect_left
from collections.abc import Mapping
from types import MappingProxyType
import hashlib
from framework.model_api import freeze_model_payload, model_data_fingerprint, thaw_model_data
from framework.model_api.immutable_record import ImmutableModelRecord

VERSION='framework-history-snapshot-storage-v2'
READ_VERSION='history-read-range-reference-v1'


def _step(previous, fingerprint):
    return hashlib.sha256((previous+':'+fingerprint).encode('utf8')).hexdigest()


def fact_prefixes(facts):
    roots=[_step(VERSION,'facts')]
    for fact in facts:roots.append(_step(roots[-1],fact.fingerprint))
    return roots


def _immutable_record(value, name):
    record=getattr(value,name,None)
    if record is None:
        record=ImmutableModelRecord(value.to_record())
        object.__setattr__(value,name,record)
    return record


def _roots(rows,kind):
    root=_step(VERSION,kind)
    for row in rows:
        if not isinstance(row,Mapping) or not isinstance(row.get('fingerprint'),str):
            raise ValueError('snapshot history row fingerprint is invalid')
        # Fact integrity is checked by the canonical fact parser. Other record
        # bodies must also bind their storage-specific selection/range fields.
        fp=(row['fingerprint'] if kind=='facts' else row.data_fingerprint
            if type(row)is ImmutableModelRecord else model_data_fingerprint(row))
        root=_step(root,fp)
    return root


def _header(record):
    return {'version':VERSION,'session_binding_fingerprint':record['session_binding_fingerprint'],
            'counts':{key:len(record[key]) for key in ('facts','reads','actual_recalls','influence_edges')},
            'roots':dict(record['roots'])}


def pack(history):
    # Cache only objects admitted by this authority. Append-only immutable
    # records keep their identity; external records are fully parsed on restore.
    from itertools import islice
    positions=history._fact_positions;ordinals=history._fact_ordinals
    prefixes=history._fact_roots

    def read_record(read):
        cached=getattr(read,'_range_storage_record',None)
        if cached is not None:return cached
        count=bisect_left(ordinals,read.before_runtime_ordinal);ranges=[]
        for fact in read.facts:
            i=positions[fact.fact_id]
            if i>=count:raise ValueError('snapshot read contains future fact')
            if ranges and ranges[-1][0]+ranges[-1][1]==i:ranges[-1][1]+=1
            else:ranges.append([i,1])
        cached=ImmutableModelRecord({'storage_version':READ_VERSION,
            'version':read.version,'session_binding_fingerprint':read.session_binding_fingerprint,
            'reader_ref':read.reader_ref,'trigger_fact_id':read.trigger_fact_id,
            'before_runtime_ordinal':read.before_runtime_ordinal,
            'snapshot_ref':{'count':count,'root':prefixes[count]},'fact_ranges':ranges,
            'authorization_ref':read.authorization_ref,'fingerprint':read.fingerprint})
        object.__setattr__(read,'_range_storage_record',cached)
        return cached

    cache=dict(getattr(history,'_snapshot_pack_cache',{}));material={
        'version':VERSION,'session_binding_fingerprint':history.session_binding_fingerprint}
    roots={}
    for kind,objects in [('facts',history._facts),('reads',history._reads),
                         ('actual_recalls',history._recalls),('influence_edges',history._edges)]:
        old,root=cache.get(kind,((),_step(VERSION,kind)))
        if len(old)>len(objects):raise ValueError('snapshot history is not append-only')
        added=[]
        for value in islice(objects.values(),len(old),None):
            record=read_record(value) if kind=='reads' else _immutable_record(value,'_history_frozen_record')
            fp=record['fingerprint'] if kind=='facts' else record.data_fingerprint
            root=_step(root,fp);added.append(record)
        rows=old+tuple(added) if added else old
        material[kind]=rows;roots[kind]=root;cache[kind]=(rows,root)
    history._snapshot_pack_cache=cache
    material['roots']=MappingProxyType(roots)
    material['fingerprint']=model_data_fingerprint(_header(material))
    return MappingProxyType(material)


def unpack(record):
    fields={'version','session_binding_fingerprint','facts','reads','actual_recalls','influence_edges','roots','fingerprint'}
    if not isinstance(record,Mapping) or set(record)!=fields or record['version']!=VERSION:
        raise ValueError('snapshot history record schema is invalid')
    for key in ('facts','reads','actual_recalls','influence_edges'):
        if not isinstance(record[key],(list,tuple)):raise ValueError('snapshot history rows must be a sequence')
    if not isinstance(record['roots'],Mapping):raise ValueError('snapshot roots must be a mapping')
    expected={key:_roots(record[key],key) for key in ('facts','reads','actual_recalls','influence_edges')}
    if dict(record['roots'])!=expected or record['fingerprint']!=model_data_fingerprint(_header(record)):
        raise ValueError('snapshot history roots or fingerprint are invalid')
    facts=record['facts'];ordinals=[row['accepted_runtime_ordinal'] for row in facts]
    prefixes=[_step(VERSION,'facts')]
    for row in facts:prefixes.append(_step(prefixes[-1],row['fingerprint']))
    reads=[]
    required={'storage_version','version','session_binding_fingerprint','reader_ref','trigger_fact_id',
              'before_runtime_ordinal','snapshot_ref','fact_ranges','authorization_ref','fingerprint'}
    for read in record['reads']:
        if set(read)!=required or read['storage_version']!=READ_VERSION:
            raise ValueError('snapshot history read schema is invalid')
        count=bisect_left(ordinals,read['before_runtime_ordinal'])
        if dict(read['snapshot_ref'])!={'count':count,'root':prefixes[count]}:
            raise ValueError('history read snapshot is invalid')
        selected=[];seen=set()
        for pair in read['fact_ranges']:
            if (not isinstance(pair,(list,tuple)) or len(pair)!=2 or any(type(v)is not int for v in pair)
                    or pair[0]<0 or pair[1]<=0 or pair[0]+pair[1]>count):
                raise ValueError('history read range exceeds prior snapshot')
            for i in range(pair[0],pair[0]+pair[1]):
                if i in seen:raise ValueError('duplicate snapshot read fact')
                seen.add(i);selected.append(facts[i])
        row={key:read[key] for key in required-{'storage_version','snapshot_ref','fact_ranges'}}
        row['fact_ids']=[fact['fact_id'] for fact in selected]
        row['fact_fingerprints']=[fact['fingerprint'] for fact in selected]
        reads.append(row)
    from . import HISTORY_CONTRACT_VERSION
    material={'version':HISTORY_CONTRACT_VERSION,'session_binding_fingerprint':record['session_binding_fingerprint'],
        'facts':facts,'reads':reads,'actual_recalls':record['actual_recalls'],'influence_edges':record['influence_edges']}
    return {**material,'fingerprint':model_data_fingerprint(material)}
