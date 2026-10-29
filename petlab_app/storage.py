"""A leased, atomically replaced framework checkpoint; no model semantics."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import tempfile
from contextlib import contextmanager

from framework.history import HistoryAuthority
from framework.runtime_checkpoint import create_runtime_checkpoint, read_runtime_checkpoint
from ops.runtime_full_memory_profile_commit import FRAMEWORK_HISTORY_PROFILE_KEY

RUNTIME_IDENTITY = "tide.desktop-pet.single-v1"
DISK_VERSION = "desktop-pet-checkpoint-history-reference-v1"


def _disk_encode(checkpoint):
    history=checkpoint['profile'].get(FRAMEWORK_HISTORY_PROFILE_KEY)
    if (history is None or history.get('version')!='framework-history-snapshot-storage-v2'):
        return checkpoint
    if history!=checkpoint['history']:
        raise ValueError('checkpoint History differs from the profile')
    return {'storage_version':DISK_VERSION,
            'checkpoint':{k:v for k,v in checkpoint.items() if k!='history'}}


def _disk_decode(value):
    if 'storage_version' not in value:return value
    if set(value)!={'storage_version','checkpoint'} or value['storage_version']!=DISK_VERSION:
        raise ValueError('checkpoint disk storage version is invalid')
    checkpoint=value['checkpoint']
    if not isinstance(checkpoint,dict) or 'history' in checkpoint:
        raise ValueError('checkpoint disk history reference is invalid')
    history=checkpoint.get('profile',{}).get(FRAMEWORK_HISTORY_PROFILE_KEY)
    if not isinstance(history,dict) or history.get('version')!='framework-history-snapshot-storage-v2':
        raise ValueError('checkpoint disk history reference is missing')
    # Restore the exact signed framework envelope. Its existing fingerprint and
    # canonical readers validate content; this codec grants no new authority.
    return {**checkpoint,'history':history}


class SessionFile:
    def __init__(self, path):
        self._incremental = None
        self._save_scope_depth = 0
        from .ordered_checkpoint import OrderedCheckpoint
        self._checkpoint_writer = OrderedCheckpoint(lambda session:self._save_now(session))
        self._validated_base_sha = None
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lease = self.path.with_suffix(self.path.suffix + ".lock").open("a+b")
        self._lease.seek(0, os.SEEK_END)
        if self._lease.tell() == 0:
            self._lease.write(b"\0")
            self._lease.flush()
        self._lease.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._lease.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self._lease.close()
            self._lease = None
            raise RuntimeError("session file is already in use") from error

    def _require_lease(self):
        if self._lease is None:
            raise RuntimeError("session file lease is closed")

    def read(self, *, session_id):
        self.flush_pending()
        self._require_lease()
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        self._validated_base_sha = None
        raw = self.path.read_bytes()
        value = _disk_decode(json.loads(raw.decode("utf-8")))
        record = read_runtime_checkpoint(value, expected_runtime_identity=RUNTIME_IDENTITY,
                                         expected_session_id=session_id)
        delta_path=Path(str(self.path)+".delta.jsonl")
        if delta_path.exists():
            from .checkpoint_journal import CheckpointJournal
            self._incremental=CheckpointJournal.load(self.path,record)
            record=read_runtime_checkpoint(self._incremental.checkpoint(),expected_runtime_identity=RUNTIME_IDENTITY,expected_session_id=session_id)
        history = record["profile"].get(FRAMEWORK_HISTORY_PROFILE_KEY)
        if history is None:
            history = HistoryAuthority(session_binding_fingerprint=record["session_binding_fingerprint"]).to_record()
        if history != record["history"]:
            raise ValueError("checkpoint History differs from the profile")
        if self._incremental is None:
            self._validated_base_sha = hashlib.sha256(raw).digest()
        return record

    def _reusable_base(self, session):
        # The digest comes only from this lease's original checkpoint reader or
        # atomic save, never a caller-owned mutable result or a disk sidecar.
        if self._validated_base_sha is None or not self.path.exists():return None
        raw = self.path.read_bytes()
        if hashlib.sha256(raw).digest() != self._validated_base_sha:return None
        base = _disk_decode(json.loads(raw.decode("utf-8")))
        from .checkpoint_journal import _same
        history = session.profile.get(FRAMEWORK_HISTORY_PROFILE_KEY)
        if history is None:
            history = HistoryAuthority(session_binding_fingerprint=session.session_binding.fingerprint).to_record()
        if (type(session.position.runtime_ordinal) is not int
                or base["session_id"] != session.session_binding.session_id
                or base["session_binding_fingerprint"] != session.session_binding.fingerprint
                or base["terminal_runtime_ordinal"] != session.position.runtime_ordinal
                or base["bootstrap_mode"] != "formal-single-pet"
                or not _same(session.profile, base["profile"])
                or not _same(history, base["history"])):
            return None
        return base

    def enable_incremental(self,session):
        """Opt into durable deltas; decode the base into the original checkpoint.

        Consumers read through SessionFile.read. export_checkpoint supplies a
        standalone full snapshot. Existing snapshot-only consumers remain unchanged.
        """
        self.flush_pending()
        self._require_lease()
        if self._incremental is not None:
            self._prime_owned_baseline(session)
            return
        if Path(str(self.path)+'.delta.jsonl').exists():
            self.read(session_id=session.session_binding.session_id)
        else:
            base = self._reusable_base(session)
            if base is None:
                self.save(session)
                base=_disk_decode(json.loads(self.path.read_text(encoding='utf-8')))
            from .checkpoint_journal import CheckpointJournal
            self._incremental=CheckpointJournal.create(self.path,base)

        self._prime_owned_baseline(session)

    def _prime_owned_baseline(self,session):
        journal=self._incremental
        if journal is None:return
        base=journal.material
        ordinal=session.position.runtime_ordinal
        if (type(ordinal) is int and ordinal==base['terminal_runtime_ordinal']
                and session.session_binding.session_id==base['session_id']
                and session.session_binding.fingerprint==base['session_binding_fingerprint']):
            journal.adopt_unchanged_profile(session.profile)

    @property
    def owned_profile(self):
        """Internal read-only baseline; only factory-owned rows grant reuse."""
        latest=self._checkpoint_writer.latest_profile
        return latest if latest is not None else None if self._incremental is None else self._incremental.material['profile']

    def export_checkpoint(self):
        self.flush_pending()
        self._require_lease()
        if self._incremental is not None:return self._incremental.checkpoint()
        return _disk_decode(json.loads(self.path.read_text(encoding='utf-8')))

    @contextmanager
    def background_saves(self):
        # Legacy snapshot-only/direct SessionFile consumers remain synchronous.
        from .ordered_checkpoint import _ACTIVE_CHECKPOINT
        enabled=self._incremental is not None
        token=_ACTIVE_CHECKPOINT.set(self._checkpoint_writer if enabled else None)
        if enabled:self._save_scope_depth+=1
        try:yield
        finally:
            try:
                if enabled:
                    self._save_scope_depth-=1
                    if self._save_scope_depth==0:self.flush_pending()
            finally:_ACTIVE_CHECKPOINT.reset(token)

    def flush_pending(self):
        self._checkpoint_writer.flush()

    def save(self, session):
        self._require_lease()
        if self._save_scope_depth:
            self._checkpoint_writer.submit(session)
            return None
        self.flush_pending()
        result=self._save_now(session)
        self._checkpoint_writer.latest_profile=None
        return result

    def _save_now(self, session):
        self._require_lease()
        binding = session.session_binding
        history = session.profile.get(FRAMEWORK_HISTORY_PROFILE_KEY)
        if history is None:
            history = HistoryAuthority(session_binding_fingerprint=binding.fingerprint).to_record()
        if self._incremental is None and Path(str(self.path)+'.delta.jsonl').exists():
            self.read(session_id=binding.session_id)
        if self._incremental is not None:
            if (binding.session_id!=self._incremental.material['session_id'] or
                    binding.fingerprint!=self._incremental.material['session_binding_fingerprint']):
                raise ValueError('incremental checkpoint session binding differs')
            ordinal=session.position.runtime_ordinal
            if isinstance(ordinal,bool) or not isinstance(ordinal,int) or ordinal<0:
                raise ValueError('runtime checkpoint terminal ordinal is invalid')
            self._incremental.save(profile=session.profile,ordinal=ordinal,history=history)
            return None
        checkpoint = create_runtime_checkpoint(
            runtime_identity=RUNTIME_IDENTITY, session_id=binding.session_id,
            session_binding_fingerprint=binding.fingerprint,
            terminal_runtime_ordinal=session.position.runtime_ordinal,
            profile=session.profile, history=history, bootstrap_mode="formal-single-pet",
        )
        encoded = json.dumps(_disk_encode(checkpoint), ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        descriptor, temporary = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            self._validated_base_sha = hashlib.sha256(encoded.encode("utf-8")).digest()
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return checkpoint

    def close(self):
        try:self._checkpoint_writer.close()
        finally:self._release_lease()

    def _release_lease(self):
        if self._lease is not None:
            self._lease.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self._lease.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._lease.fileno(), fcntl.LOCK_UN)
            finally:
                self._lease.close()
                self._lease = None

    def __enter__(self):
        self._require_lease()
        return self

    def __exit__(self, *_):
        self.close()
