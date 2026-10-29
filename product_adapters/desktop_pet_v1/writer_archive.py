"""Desktop-only read scope for opening an existing Writer audit archive.

All artifact validation stays in WriterAttemptJournal. The receipt snapshot is
local to one locked read pass, and exact index bytes must remain unchanged.
"""
from contextlib import contextmanager
from petlab_app.journal_reads import JournalReads
from pathlib import Path
import io
import json
import os
from framework.model_api import freeze_model_payload,thaw_model_data
from product_adapters.cx_six_seven_v1.writer_attempts import WriterAttemptJournal,WriterAttemptJournalError,_identifier


class PetWriterAttemptJournal(WriterAttemptJournal):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self._opening_receipts=None
        self._artifact_snapshot=None
        self._opening_allocations=None
        self._identity_receipts=None
        self._request_receipts=None
        self._allocation_receipts=None
        self._index_snapshot=None
        self._attempt_snapshots={}
        self._line_snapshots={}
        self._artifact_rows=None
        self._readers=JournalReads()
        self._batch_buffers=None
        self._batch_failed=False

    @contextmanager
    def batch(self):
        """Keep original logical rows; persist at explicit side-effect boundaries."""
        with self._lock:
            if self._batch_buffers is not None:
                yield
                return
            self._batch_buffers={};self._batch_failed=False
            try:
                yield
            finally:
                try:
                    if not self._batch_failed:self.flush_batch()
                finally:
                    if self._batch_failed:
                        self._index_snapshot=None
                        self._attempt_snapshots.clear();self._line_snapshots.clear()
                    self._batch_buffers=None

    def _bytes(self,path):
        path=os.fspath(path)
        if self._batch_buffers is None:return self._readers.read(path)
        if path not in self._batch_buffers:
            self._batch_buffers[path]=[self._readers.read(path),[]]
        base,lines=self._batch_buffers[path]
        return base+b''.join(lines)

    def _append_jsonl(self,path,record):
        if self._batch_buffers is None:return super()._append_jsonl(path,record)
        path=os.fspath(path);self._bytes(path)
        line=(json.dumps(dict(record),ensure_ascii=False,separators=(',',':'))+'\n').encode('utf8')
        self._batch_buffers[path][1].append(line)

    def flush_batch(self):
        if self._batch_buffers is None:return
        if self._batch_failed:raise WriterAttemptJournalError('writer batch previously failed')
        changes=[(path,base,b''.join(lines)) for path,(base,lines) in self._batch_buffers.items() if lines]
        if not changes:return {}
        try:
            # Reuse existing read handles for the actual-byte check; opening
            # them again here would repeat the same physical read boundary.
            # Check every actual prefix before publishing any new bytes.
            for path,base,_ in changes:
                if self._readers.read(path)!=base:
                    raise WriterAttemptJournalError('writer batch actual prefix changed')
            self._readers.release()
            # Same artifact-before-index ordering as the native journal. Each
            # file contains the exact original JSONL rows, not a new schema.
            for path,base,tail in sorted(changes,key=lambda item:item[0]==self.index_path):
                Path(path).parent.mkdir(parents=True,exist_ok=True)
                with open(path,'ab') as output:
                    output.write(tail);output.flush()
                    if self._durable_writes:os.fsync(output.fileno())
            self._batch_buffers.clear()
            # Returned only after every canonical file is durable. This is a
            # comparison baseline, never evidence that the path is unchanged.
            return {path:base+tail for path,base,tail in changes}
        except BaseException:
            self._batch_failed=True
            raise

    def _publish_index(self,raw,rows):
        allocations={};requests={};parents={}
        for row in rows:
            identity=row['writer_request_id']
            requests.setdefault(identity,[]).append(row)
            if row['stage']=='allocated':
                allocations.setdefault(identity,[]).append(row)
                parents.setdefault(row['parent_event_id'],[]).append(row)
        self._index_snapshot=(raw,tuple(rows),allocations,requests,parents)
        return self._index_snapshot

    def _validated_index(self):
        raw=self._index_bytes()
        cached=self._index_snapshot
        if cached is not None and raw==cached[0]:return cached
        rows=super().read_receipts()
        if self._index_bytes()!=raw:
            raise WriterAttemptJournalError('writer receipt index changed during read')
        return self._publish_index(raw,freeze_model_payload({'rows':rows})['rows'])

    def read_receipts(self):
        with self._lock:
            for rows in (self._identity_receipts,self._request_receipts,
                    self._allocation_receipts,self._opening_receipts):
                if rows is not None:return tuple(thaw_model_data(row) for row in rows)
            return tuple(thaw_model_data(row) for row in self._validated_index()[1])

    def read_parent_receipts(self,parent_event_id):
        with self._lock:
            parent=_identifier(parent_event_id,'parent event ID')
            snapshot=self._validated_index();identities=set()
            for row in snapshot[4].get(parent,()):identities.add(row['writer_request_id'])
            return tuple(thaw_model_data(row) for identity in sorted(identities)
                for row in snapshot[3][identity])

    def _identity(self,writer_request_id):
        with self._lock:
            request_id=_identifier(writer_request_id,'writer request ID')
            allocations=(self._opening_allocations if self._opening_allocations is not None
                else self._validated_index()[2])
            previous=self._identity_receipts
            self._identity_receipts=allocations.get(request_id,())
            try:return super()._identity(request_id)
            finally:self._identity_receipts=previous

    def allocate_request(self,*,parent_event_id):
        with self._lock:
            parent=_identifier(parent_event_id,'parent event ID')
            snapshot=self._validated_index();previous=self._allocation_receipts
            self._allocation_receipts=snapshot[4].get(parent,())
            try:return super().allocate_request(parent_event_id=parent)
            finally:self._allocation_receipts=previous

    def _append_stage(self,identity,**kwargs):
        with self._lock:
            snapshot=self._validated_index();previous=self._request_receipts
            self._request_receipts=snapshot[3].get(identity.writer_request_id,())
            try:
                receipt=super()._append_stage(identity,**kwargs)
            finally:self._request_receipts=previous
            # Adopt only our own successful native append and its exact bytes.
            # An external edit or a failed partial write takes the full reader.
            line=(json.dumps(dict(receipt),ensure_ascii=False,separators=(',',':'))+'\n').encode('utf8')
            actual=self._index_bytes()
            if actual==snapshot[0]+line:
                row=freeze_model_payload(receipt)
                # The checked native append extends only one request/parent.
                # Do not rebuild every unrelated index bucket after each stage.
                allocations,requests,parents=snapshot[2:]
                requests.setdefault(row['writer_request_id'],[]).append(row)
                if row['stage']=='allocated':
                    allocations.setdefault(row['writer_request_id'],[]).append(row)
                    parents.setdefault(row['parent_event_id'],[]).append(row)
                self._index_snapshot=(actual,(*snapshot[1],row),allocations,requests,parents)
            else:self._index_snapshot=None
            return receipt

    def read_attempt(self,writer_request_id):
        with self._lock:
            identity=self._identity(writer_request_id)
            raw=self._bytes(self._artifact_path(identity.writer_request_id))
            return self.read_attempt_bytes(identity.writer_request_id,raw)

    def _index_bytes(self):
        return self._bytes(self.index_path)

    @contextmanager
    def opening_receipts(self):
        with self._lock:
            if self._opening_receipts is not None:
                raise WriterAttemptJournalError('writer opening receipt snapshot is already active')
            raw=self._index_bytes()
            rows=super().read_receipts()
            if self._index_bytes()!=raw:
                raise WriterAttemptJournalError('writer receipt index changed during opening')
            frozen=freeze_model_payload({'rows':rows})['rows']
            allocations={}
            for row in frozen:
                if row['stage']=='allocated' and isinstance(row['writer_request_id'],str):
                    allocations.setdefault(row['writer_request_id'],[]).append(row)
            self._opening_receipts=frozen
            self._opening_allocations=allocations
            try:
                yield self.read_receipts()
            finally:
                self._opening_receipts=None
                self._opening_allocations=None
                self._identity_receipts=None
                if self._index_bytes()!=raw:
                    raise WriterAttemptJournalError('writer receipt index changed during opening')


    def read_attempt_bytes(self,writer_request_id,raw):
        """Reuse only an exact validated prefix; native-check every new row."""
        if type(raw)is not bytes:raise TypeError('writer artifact snapshot requires bytes')
        with self._lock:
            if self._artifact_snapshot is not None:
                raise WriterAttemptJournalError('writer artifact snapshot is already active')
            identity=self._identity(writer_request_id).to_record()
            cached=self._attempt_snapshots.get(writer_request_id)
            if cached is not None and cached[0]==raw and cached[1]==identity:
                return thaw_model_data(cached[2])
            path=self._artifact_path(writer_request_id)
            rows=self._decode_rows(path,raw)
            incremental=(cached is not None and cached[1]==identity
                and cached[0].endswith(b'\n') and raw.startswith(cached[0]))
            if incremental:
                tail=rows[len(cached[3]):]
                if any(row.get('stage') in cached[3] for row in tail):
                    raise WriterAttemptJournalError('writer attempt artifact contains duplicate or invalid stages')
                self._artifact_rows=tail
            self._artifact_snapshot=(path,raw)
            try:delta=super().read_attempt(writer_request_id)
            finally:
                self._artifact_snapshot=None;self._artifact_rows=None
            result=({**thaw_model_data(cached[2]),**delta} if incremental else delta)
            stages=frozenset(row.get('stage') for row in rows)
            if len(self._attempt_snapshots)>=32:self._attempt_snapshots.pop(next(iter(self._attempt_snapshots)))
            self._attempt_snapshots[writer_request_id]=(raw,identity,freeze_model_payload(result),stages)
            return thaw_model_data(result)

    def _decode_rows(self,path,raw):
        cached=self._line_snapshots.get(str(path))
        if cached is not None and cached[0]==raw:return cached[1]
        incremental=(cached is not None and cached[0].endswith(b'\n') and raw.startswith(cached[0]))
        content=raw[len(cached[0]):] if incremental else raw
        try:
            with io.StringIO(content.decode('utf-8-sig'),newline=None) as handle:
                added=tuple(json.loads(line) for line in handle if line.strip())
        except (OSError,ValueError,json.JSONDecodeError) as exc:
            raise WriterAttemptJournalError('writer attempt archive is unreadable') from exc
        rows=freeze_model_payload({'rows':added})['rows']
        if incremental:rows=(*cached[1],*rows)
        if len(self._line_snapshots)>=32:self._line_snapshots.pop(next(iter(self._line_snapshots)))
        self._line_snapshots[str(path)]=(raw,rows)
        return rows

    def _read_jsonl(self,path):
        snapshot=self._artifact_snapshot
        if snapshot is not None and str(path)==snapshot[0]:
            if self._artifact_rows is not None:return self._artifact_rows
            return self._decode_rows(path,snapshot[1])
        try:raw=self._bytes(path)
        except OSError as exc:raise WriterAttemptJournalError('writer attempt archive is unreadable') from exc
        return self._decode_rows(path,raw)

    def close(self):
        self._readers.close()
        self._attempt_snapshots.clear();self._line_snapshots.clear();self._index_snapshot=None


def _io_scope(method):
    from functools import wraps
    @wraps(method)
    def scoped(self,*args,**kwargs):
        with self._readers.scope():return method(self,*args,**kwargs)
    return scoped


# Existing API, original native stage validation and fsync remain unchanged.
# Share only readers nested within one operation; no handle escapes its return.
for _name in ('read_parent_receipts','read_receipts','read_attempt','read_attempt_bytes','_identity',
        '_append_stage','allocate_request','record_packet','record_prompt',
        'record_provider_dispatch','record_provider_result','record_terminal',
        'link_committed_action'):
    setattr(PetWriterAttemptJournal,_name,_io_scope(getattr(PetWriterAttemptJournal,_name)))
