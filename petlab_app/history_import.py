"""Transcript intake and resumable reconstruction workspace, with no state authority.

Records here are import sources and reconstruction estimates, never formal History.
Measurement, joint estimation and publication consume this host through existing
package/provider/runtime interfaces; this module cannot play or regenerate replies.
"""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3

VERSION = 'petlab-chat-import-workspace-v1'
MAX_SOURCE_BYTES = 64 * 1024 * 1024


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':'))


def _digest(value):
    return hashlib.sha256(_json(value).encode('utf8')).hexdigest()


def _identifier(value, label):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(label+' must be a nonempty exact identifier')
    return value


@dataclass(frozen=True)
class TranscriptMessage:
    index: int
    source_id: str | None
    role: str
    text: str
    timestamp_ms: int | None

    def record(self):
        return dict(index=self.index, source_id=self.source_id, role=self.role,
                    text=self.text, timestamp_ms=self.timestamp_ms)


@dataclass(frozen=True)
class Transcript:
    source_path: str
    digest: str
    messages: tuple[TranscriptMessage, ...]


def read_transcript(path):
    """Read explicit text-only messages. Timestamp, if supplied, is Unix ms.

    Accept a messages array or {"messages": [...]} with user/assistant roles.
    Missing timestamps remain None; no clock or age is guessed at intake.
    """
    path = Path(path).resolve()
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError('source exceeds 64 MiB intake bound; split conversations explicitly')
    raw = path.read_bytes()
    if len(raw) > MAX_SOURCE_BYTES:raise ValueError('source exceeds intake bound')
    try:
        data = json.loads(raw.decode('utf-8-sig'),
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite source number')))
    except (UnicodeError, ValueError) as error:
        raise ValueError('source must be finite UTF-8 JSON') from error
    if isinstance(data, dict):
        if set(data) != {'messages'}:raise ValueError('source object must contain only messages')
        data = data['messages']
    if not isinstance(data, list) or not data:raise ValueError('source requires a nonempty messages array')
    messages = [];ids = set()
    for index, row in enumerate(data):
        if not isinstance(row, dict) or not {'role', 'content'} <= set(row) or set(row)-{'id', 'role', 'content', 'timestamp'}:
            raise ValueError(f'message {index+1}: supported fields are id/role/content/timestamp')
        if row['role'] not in ('user', 'assistant'):
            raise ValueError(f'message {index+1}: map source identity explicitly to user or assistant')
        text = row['content']
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f'message {index+1}: only nonempty text is supported, not media or tool executions')
        identity = row.get('id')
        if identity is not None:
            _identifier(identity, f'message {index+1} id')
            if identity in ids:raise ValueError('duplicate source message id')
            ids.add(identity)
        timestamp = row.get('timestamp')
        if timestamp is not None and (type(timestamp) is not int or timestamp < 0):
            raise ValueError(f'message {index+1}: timestamp must be Unix milliseconds or null')
        messages.append(TranscriptMessage(index, identity, row['role'], text, timestamp))
    return Transcript(str(path), hashlib.sha256(raw).hexdigest(), tuple(messages))


class ImportWorkspace:
    """One transcript/body per workspace; transactional step checkpoints use refs.

    A completed step is an estimate, not a published or accepted memory. SQLite
    serializes checkpoint updates; source records remain immutable to this API.
    """
    def __init__(self, path, connection):
        self.path = Path(path).resolve();self.connection = connection

    @classmethod
    def create(cls, path, transcript, *, definition_ref):
        if not isinstance(transcript, Transcript):raise TypeError('validated transcript required')
        _identifier(definition_ref, 'definition_ref')
        path = Path(path).resolve();path.mkdir(parents=True, exist_ok=False)
        db = sqlite3.connect(path/'workspace.sqlite3')
        try:
            db.executescript('''
                CREATE TABLE metadata (id INTEGER PRIMARY KEY CHECK(id=1), version TEXT NOT NULL,
                    source_digest TEXT NOT NULL, source_path TEXT NOT NULL, definition_ref TEXT NOT NULL,
                    message_count INTEGER NOT NULL, next_index INTEGER NOT NULL);
                CREATE TABLE messages (idx INTEGER PRIMARY KEY, source_id TEXT, role TEXT NOT NULL,
                    body TEXT NOT NULL, timestamp_ms INTEGER, fingerprint TEXT NOT NULL);
                CREATE TABLE steps (idx INTEGER PRIMARY KEY REFERENCES messages(idx),
                    estimate_record TEXT NOT NULL, fingerprint TEXT NOT NULL);
                CREATE TABLE calls (operation_key TEXT PRIMARY KEY, reservation TEXT NOT NULL);
                CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, fingerprint TEXT NOT NULL);
            ''')
            db.execute('INSERT INTO metadata VALUES (1,?,?,?,?,?,0)',
                (VERSION, transcript.digest, transcript.source_path, definition_ref, len(transcript.messages)))
            db.executemany('INSERT INTO messages VALUES (?,?,?,?,?,?)', [
                (m.index, m.source_id, m.role, m.text, m.timestamp_ms, _digest(m.record()))
                for m in transcript.messages])
            db.commit()
            return cls(path, db)
        except Exception:
            db.close();raise

    @classmethod
    def restore(cls,path):
        """Open an existing local workspace by its original persisted bindings."""
        path=Path(path).resolve();file=path/'workspace.sqlite3'
        if not file.is_file():raise ValueError('import workspace does not exist')
        db=sqlite3.connect(file.as_uri()+'?mode=ro',uri=True)
        try:
            digest,definition=db.execute('SELECT source_digest,definition_ref FROM metadata WHERE id=1').fetchone()
        finally:db.close()
        return cls.open(path,source_digest=digest,definition_ref=definition)

    @classmethod
    def open(cls, path, *, source_digest, definition_ref):
        path = Path(path).resolve()
        if not (path/'workspace.sqlite3').is_file():raise ValueError('import workspace does not exist')
        db = sqlite3.connect(path/'workspace.sqlite3')
        try:
            meta = db.execute('SELECT version,source_digest,definition_ref FROM metadata WHERE id=1').fetchone()
            if meta != (VERSION, source_digest, definition_ref):
                raise ValueError('workspace source, role definition or version differs')
            # Additive host checkpoint support, without changing source rows.
            db.execute('CREATE TABLE IF NOT EXISTS calls (operation_key TEXT PRIMARY KEY, reservation TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, fingerprint TEXT NOT NULL)')
            db.commit()
            return cls(path, db)
        except Exception:
            db.close();raise

    def status(self):
        digest, definition, count, index = self.connection.execute(
            'SELECT source_digest,definition_ref,message_count,next_index FROM metadata WHERE id=1').fetchone()
        published=self.setting('published')
        return {'version': VERSION, 'source_digest': digest, 'definition_ref': definition,
                'message_count': count, 'next_index': index,
                'stage': 'published' if published else 'estimates_complete' if index == count else 'estimating',
                'formal_history_published': bool(published)}

    def message(self, index):
        row = self.connection.execute(
            'SELECT idx,source_id,role,body,timestamp_ms,fingerprint FROM messages WHERE idx=?', (index,)).fetchone()
        if row is None:raise ValueError('source index unavailable')
        message = TranscriptMessage(*row[:5])
        if _digest(message.record()) != row[5]:raise ValueError('stored source fingerprint differs')
        return message

    def record_step(self, index, estimate):
        if not isinstance(estimate, dict) or not estimate:raise ValueError('step requires an estimate record')
        encoded = _json(estimate);fingerprint = _digest(estimate)
        db = self.connection
        try:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT estimate_record,fingerprint FROM steps WHERE idx=?', (index,)).fetchone()
            if old is not None:
                if old != (encoded, fingerprint):raise ValueError('completed reconstruction step conflicts')
                db.rollback();return
            progress = self.status()
            if type(index) is not int or index != progress['next_index'] or index >= progress['message_count']:
                raise ValueError('reconstruction steps must continue the accepted source order')
            self.message(index)
            db.execute('INSERT INTO steps VALUES (?,?,?)', (index, encoded, fingerprint))
            db.execute('UPDATE metadata SET next_index=? WHERE id=1', (index+1,))
            db.commit()
        except Exception:
            db.rollback();raise

    def step(self, index):
        row = self.connection.execute('SELECT estimate_record,fingerprint FROM steps WHERE idx=?', (index,)).fetchone()
        if row is None:return None
        value = json.loads(row[0])
        if _digest(value) != row[1]:raise ValueError('step fingerprint differs')
        return value

    def reserve_call(self, key, binding):
        """Allocate one paid invocation before dispatch; existing calls never redispatch."""
        import uuid
        _identifier(key, 'operation_key')
        if not isinstance(binding, dict):raise ValueError('call binding must be a mapping')
        encoded = _json(binding)
        db = self.connection
        try:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT reservation FROM calls WHERE operation_key=?',(key,)).fetchone()
            if old:
                row = json.loads(old[0])
                if _json(row['binding']) != encoded:raise ValueError('reserved import call source or request changed')
                if row['fingerprint'] != _digest({k:v for k,v in row.items() if k!='fingerprint'}):
                    raise ValueError('import invocation reservation integrity differs')
                db.rollback();return row,False
            row = {'operation_key':key,'binding':binding,'invocation_id':uuid.uuid4().hex}
            row['fingerprint'] = _digest(row)
            db.execute('INSERT INTO calls VALUES (?,?)',(key,_json(row)))
            db.commit();return row,True
        except Exception:
            db.rollback();raise

    def close(self):self.connection.close()

    def setting(self,key,value=None):
        row=self.connection.execute('SELECT value,fingerprint FROM settings WHERE key=?',(key,)).fetchone()
        if row is not None:
            stored=json.loads(row[0])
            if _digest(stored)!=row[1]:raise ValueError('import settings integrity differs')
            if value is not None and _digest(value)!=row[1]:raise ValueError('import settings changed during reconstruction')
            return stored
        if value is None:return None
        with self.connection:
            self.connection.execute('INSERT INTO settings VALUES (?,?,?)',(key,_json(value),_digest(value)))
        return value
    def __enter__(self):return self
    def __exit__(self, *args):self.close()
