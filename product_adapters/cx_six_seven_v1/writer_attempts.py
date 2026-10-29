"""CX67 product audit archive for legacy Writer realization attempts.

An attempt is operational evidence, not a committed interaction event.  This
module intentionally has no Store, Memory, Actor, scheduler, UI, or provider
dependency.  Callers may resolve a complete failure scene by request ID, while
the lightweight receipt index never duplicates prompt or candidate bodies.
"""
from __future__ import annotations
from pathlib import Path

from dataclasses import dataclass
import hashlib
import json
import os
import threading
from collections.abc import Mapping
from typing import Any

from framework.model_api import model_data_fingerprint, freeze_model_payload, thaw_model_data
from product_adapters.cx_six_seven_v1.realizer_packet import (
    WriterPacket,
    verify_writer_packet,
)


WRITER_ATTEMPT_SCHEMA_VERSION = 1
_TERMINAL_STATUSES = frozenset({
    "candidate_ready", "failed", "cancelled", "timeout",
})
_SINGLETON_STAGES = frozenset({
    "allocated", "packet", "prompt", "provider_dispatch", "provider", "terminal", "commit",
})


class WriterAttemptJournalError(ValueError):
    """The audit journal cannot preserve an exact append-only attempt."""


def _identifier(value: object, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise WriterAttemptJournalError(f"{field} must be a non-empty identifier")
    return result


def _suffix(ordinal: int) -> str:
    """Human-readable 1 -> a, 26 -> z, 27 -> aa alias."""
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1:
        raise WriterAttemptJournalError("attempt ordinal must be positive")
    chars = []
    current = ordinal
    while current:
        current, remainder = divmod(current - 1, 26)
        chars.append(chr(ord("a") + remainder))
    return "".join(reversed(chars))


def _mapping(value: Mapping[str, Any] | None, field: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise WriterAttemptJournalError(f"{field} must be a mapping or None")
    # Round-trip through JSON now so a later archive read cannot fail on a
    # provider-specific object that was never persistable.
    try:
        return json.loads(json.dumps(dict(value), ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise WriterAttemptJournalError(f"{field} must be JSON serializable") from exc


@dataclass(frozen=True)
class WriterAttemptIdentity:
    writer_request_id: str
    parent_event_id: str
    attempt_ordinal: int
    display_suffix: str

    def to_record(self) -> dict[str, Any]:
        return {
            "writer_request_id": self.writer_request_id,
            "parent_event_id": self.parent_event_id,
            "attempt_ordinal": self.attempt_ordinal,
            "display_suffix": self.display_suffix,
        }


class WriterAttemptJournal:
    """Persistent two-layer audit: body-free index plus full stage artifacts."""

    def __init__(self, root: str | os.PathLike[str], *, durable_writes: bool = True):
        self.root = os.path.abspath(os.fspath(root))
        self.index_path = os.path.join(self.root, "receipts.jsonl")
        self.artifacts_dir = os.path.join(self.root, "artifacts")
        self._durable_writes = bool(durable_writes)
        self._lock = threading.RLock()

    def _artifact_ref(self, writer_request_id: str) -> str:
        digest = hashlib.sha256(writer_request_id.encode("utf-8")).hexdigest()
        return f"artifacts/{digest}.jsonl"

    def _artifact_path(self, writer_request_id: str) -> str:
        return os.path.join(self.root, *self._artifact_ref(writer_request_id).split("/"))

    def _read_jsonl(self, path: str) -> tuple[dict[str, Any], ...]:
        if not os.path.isfile(path):
            return ()
        try:
            with open(path, "r", encoding="utf-8-sig") as handle:
                return tuple(json.loads(line) for line in handle if line.strip())
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise WriterAttemptJournalError("writer attempt archive is unreadable") from exc

    def _append_jsonl(self, path: str, record: Mapping[str, Any]) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        line = json.dumps(dict(record), ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            with open(path, "a", encoding="utf-8", newline="") as handle:
                handle.write(line)
                handle.flush()
                if self._durable_writes:
                    os.fsync(handle.fileno())
        except OSError as exc:
            raise WriterAttemptJournalError("writer attempt archive append failed") from exc

    def read_receipts(self) -> tuple[dict[str, Any], ...]:
        """Return the no-body audit index; this grants no interaction authority."""
        with self._lock:
            raw = Path(self.index_path).read_bytes() if os.path.exists(self.index_path) else b''
            cached=getattr(self,'_receipt_read_cache',None)
            if cached is not None and raw==cached[0]:
                return tuple(dict(row) for row in cached[1])
            rows = self._read_jsonl(self.index_path)
        for row in rows:
            if row.get("schema_version") != WRITER_ATTEMPT_SCHEMA_VERSION:
                raise WriterAttemptJournalError("writer attempt receipt schema is invalid")
            if row.get("stage") not in _SINGLETON_STAGES:
                raise WriterAttemptJournalError("writer attempt receipt stage is invalid")
            _identifier(row.get("writer_request_id"), "writer request ID")
            _identifier(row.get("parent_event_id"), "parent event ID")
            _identifier(row.get("artifact_ref"), "attempt artifact ref")
        # Flat, no-body index records only. Compare actual file bytes each time.
        if all(all(v is None or type(v) in (str,int,float,bool) for v in row.values()) for row in rows):
            self._receipt_read_cache=(raw,tuple(dict(row) for row in rows))
        return rows

    def _identity(self, writer_request_id: str) -> WriterAttemptIdentity:
        request_id = _identifier(writer_request_id, "writer request ID")
        allocated = [
            row for row in self.read_receipts()
            if row["writer_request_id"] == request_id and row["stage"] == "allocated"
        ]
        if len(allocated) != 1:
            raise WriterAttemptJournalError("writer request is not allocated exactly once")
        row = allocated[0]
        return WriterAttemptIdentity(
            writer_request_id=request_id,
            parent_event_id=_identifier(row["parent_event_id"], "parent event ID"),
            attempt_ordinal=int(row["attempt_ordinal"]),
            display_suffix=_identifier(row["display_suffix"], "attempt display suffix"),
        )

    def _append_stage(
        self,
        identity: WriterAttemptIdentity,
        *,
        stage: str,
        payload: Mapping[str, Any],
        terminal_status: str | None = None,
    ) -> dict[str, Any]:
        if stage not in _SINGLETON_STAGES:
            raise WriterAttemptJournalError("writer attempt stage is invalid")
        with self._lock:
            receipts = self.read_receipts()
            existing = [
                row for row in receipts
                if row["writer_request_id"] == identity.writer_request_id
                and row["stage"] == stage
            ]
            stages = {
                row["stage"] for row in receipts
                if row["writer_request_id"] == identity.writer_request_id
            }
            if "commit" in stages:
                raise WriterAttemptJournalError("committed writer attempt is immutable")
            if "terminal" in stages and stage != "commit":
                raise WriterAttemptJournalError("terminal writer attempt is immutable")
            if existing:
                raise WriterAttemptJournalError(
                    f"writer attempt stage {stage} is already archived")
            artifact_ref = self._artifact_ref(identity.writer_request_id)
            artifact_record = {
                "schema_version": WRITER_ATTEMPT_SCHEMA_VERSION,
                "stage": stage,
                **identity.to_record(),
                "payload": dict(payload),
            }
            artifact_fingerprint = model_data_fingerprint(artifact_record)
            artifact_record["artifact_fingerprint"] = artifact_fingerprint
            receipt = {
                "schema_version": WRITER_ATTEMPT_SCHEMA_VERSION,
                "kind": "writer_attempt_stage_archived",
                **identity.to_record(),
                "stage": stage,
                "terminal_status": terminal_status,
                "artifact_ref": artifact_ref,
                "artifact_fingerprint": artifact_fingerprint,
            }
            # The artifact is written before its index entry.  If an earlier
            # process stopped between those appends, an exact retry indexes the
            # existing immutable artifact instead of duplicating or overwriting it.
            artifact_path = self._artifact_path(identity.writer_request_id)
            orphan_rows = [
                row for row in self._read_jsonl(artifact_path)
                if row.get("stage") == stage
            ]
            if orphan_rows:
                if len(orphan_rows) != 1 or orphan_rows[0] != artifact_record:
                    raise WriterAttemptJournalError(
                        "writer attempt orphan artifact conflicts with retry")
            else:
                self._append_jsonl(artifact_path, artifact_record)
            self._append_jsonl(self.index_path, receipt)
        return receipt

    def allocate_request(self, *, parent_event_id: str) -> WriterAttemptIdentity:
        parent = _identifier(parent_event_id, "parent event ID")
        with self._lock:
            ordinals = [
                int(row["attempt_ordinal"])
                for row in self.read_receipts()
                if row["parent_event_id"] == parent and row["stage"] == "allocated"
            ]
            ordinal = max(ordinals, default=0) + 1
            suffix = _suffix(ordinal)
            identity = WriterAttemptIdentity(
                writer_request_id=f"{parent}:writer-{suffix}",
                parent_event_id=parent,
                attempt_ordinal=ordinal,
                display_suffix=suffix,
            )
            self._append_stage(identity, stage="allocated", payload={})
        return identity

    def record_packet(self, writer_request_id: str, *, packet: WriterPacket,
                      source_evidence: Mapping[str, Any] | None = None,
                      provider_dispatch_tracked: bool = False,
                      prepared_prompt: Mapping[str, Any] | None = None,
                      prepared_assembly: Mapping[str, Any] | None = None) -> dict[str, Any]:
        identity = self._identity(writer_request_id)
        verify_writer_packet(packet)
        if packet.writer_request_id != identity.writer_request_id:
            raise WriterAttemptJournalError(
                "writer packet request ID differs from allocated attempt")
        if packet.event_id != identity.parent_event_id:
            raise WriterAttemptJournalError(
                "writer packet parent event differs from allocated attempt")
        payload = {"packet": packet.to_record()}
        if not isinstance(provider_dispatch_tracked, bool):
            raise WriterAttemptJournalError("provider dispatch tracking must be an explicit boolean")
        if provider_dispatch_tracked:
            payload["provider_dispatch_tracked"] = True
        if prepared_prompt is not None:
            if (not provider_dispatch_tracked or not isinstance(prepared_prompt, Mapping)
                    or set(prepared_prompt) != {"system", "user", "prompt_fingerprint"}
                    or not isinstance(prepared_prompt["system"], str)
                    or not isinstance(prepared_prompt["user"], str)):
                raise WriterAttemptJournalError("frozen packet prompt requires tracked exact prompt data")
            if hashlib.sha256((prepared_prompt["system"] + "\0" + prepared_prompt["user"]).encode("utf-8")).hexdigest() != prepared_prompt["prompt_fingerprint"]:
                raise WriterAttemptJournalError("frozen packet prompt fingerprint is invalid")
            payload["prepared_prompt"] = dict(prepared_prompt)
        if prepared_assembly is not None:
            if not provider_dispatch_tracked or not isinstance(prepared_assembly, Mapping):
                raise WriterAttemptJournalError("assembly metadata requires tracked record data")
            payload["prepared_assembly"] = thaw_model_data(freeze_model_payload(prepared_assembly))
        if source_evidence is not None:
            if not isinstance(source_evidence, Mapping):
                raise WriterAttemptJournalError("packet source evidence must be opaque record data")
            payload["source_evidence"] = thaw_model_data(freeze_model_payload(source_evidence))
        return self._append_stage(identity, stage="packet", payload=payload)

    def record_provider_dispatch(self, writer_request_id: str) -> dict[str, Any]:
        """Durable dispatch intent, not proof that a remote call completed."""
        identity = self._identity(writer_request_id)
        self._require_stage(identity, "prompt")
        artifact = self.read_attempt(writer_request_id)
        if artifact.get("provider_dispatch_tracked") is not True:
            raise WriterAttemptJournalError("provider dispatch contract was not enabled")
        if any(name in artifact for name in ("provider", "terminal", "commit")):
            raise WriterAttemptJournalError("completed provider attempt cannot dispatch again")
        return self._append_stage(identity, stage="provider_dispatch", payload={
            "provider_dispatch": {
                "version": "writer-provider-dispatch-v1",
                "prompt_fingerprint": artifact["prompt"]["prompt_fingerprint"],
                "packet_fingerprint": artifact["packet"]["packet_fingerprint"],
            }})

    def record_prompt(
        self,
        writer_request_id: str,
        *,
        system: str,
        user: str,
        prompt_fingerprint: str,
    ) -> dict[str, Any]:
        identity = self._identity(writer_request_id)
        self._require_stage(identity, "packet")
        prompt = {
            "system": str(system),
            "user": str(user),
            "prompt_fingerprint": _identifier(
                prompt_fingerprint, "prompt fingerprint"),
        }
        return self._append_stage(
            identity, stage="prompt", payload={"prompt": prompt})

    def record_terminal(
        self,
        writer_request_id: str,
        *,
        status: str,
        provider_result: Mapping[str, Any] | None = None,
        candidate: str | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        identity = self._identity(writer_request_id)
        normalized_status = _identifier(status, "writer terminal status")
        if normalized_status not in _TERMINAL_STATUSES:
            raise WriterAttemptJournalError("writer terminal status is invalid")
        if normalized_status == "candidate_ready":
            self._require_stage(identity, "packet")
        terminal = {
            "status": normalized_status,
            "provider_result": _mapping(provider_result, "provider result"),
            "candidate": None if candidate is None else str(candidate),
            "error": _mapping(error, "writer error"),
        }
        return self._append_stage(
            identity,
            stage="terminal",
            payload={"terminal": terminal},
            terminal_status=normalized_status,
        )

    def record_provider_result(
        self,
        writer_request_id: str,
        *,
        provider_result: Mapping[str, Any] | None = None,
        candidate: str | None = None,
    ) -> dict[str, Any]:
        """Archive the provider return before parsing or commit is attempted."""
        identity = self._identity(writer_request_id)
        self._require_stage(identity, "prompt")
        provider = {
            "provider_result": _mapping(provider_result, "provider result"),
            "candidate": None if candidate is None else str(candidate),
        }
        return self._append_stage(
            identity, stage="provider", payload={"provider": provider})

    def link_committed_action(
        self,
        writer_request_id: str,
        *,
        action_id: str,
        action_receipt_ref: str,
        action_receipt_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        identity = self._identity(writer_request_id)
        terminal = self.read_attempt(identity.writer_request_id).get("terminal")
        if not terminal or terminal.get("status") != "candidate_ready":
            status = (terminal or {}).get("status") or "missing"
            raise WriterAttemptJournalError(
                f"only candidate_ready attempts can commit; attempt is {status}")
        commit = {
            "action_id": _identifier(action_id, "committed action ID"),
            "action_receipt_ref": _identifier(
                action_receipt_ref, "action receipt ref"),
        }
        if action_receipt_fingerprint is not None:
            commit["action_receipt_fingerprint"] = _identifier(
                action_receipt_fingerprint, "action receipt fingerprint")
        return self._append_stage(
            identity, stage="commit", payload={"commit": commit})

    def _require_stage(self, identity: WriterAttemptIdentity, stage: str) -> None:
        if not any(
            row["writer_request_id"] == identity.writer_request_id
            and row["stage"] == stage
            for row in self.read_receipts()
        ):
            raise WriterAttemptJournalError(
                f"writer attempt requires archived {stage} stage")

    def read_attempt(self, writer_request_id: str) -> dict[str, Any]:
        identity = self._identity(writer_request_id)
        path = self._artifact_path(identity.writer_request_id)
        with self._lock:
            rows = self._read_jsonl(path)
        result: dict[str, Any] = identity.to_record()
        seen = set()
        for row in rows:
            stage = row.get("stage")
            if stage in seen or stage not in _SINGLETON_STAGES:
                raise WriterAttemptJournalError(
                    "writer attempt artifact contains duplicate or invalid stages")
            seen.add(stage)
            expected = dict(row)
            fingerprint = expected.pop("artifact_fingerprint", None)
            if fingerprint != model_data_fingerprint(expected):
                raise WriterAttemptJournalError(
                    "writer attempt artifact fingerprint is invalid")
            if row.get("writer_request_id") != identity.writer_request_id:
                raise WriterAttemptJournalError(
                    "writer attempt artifact identity is invalid")
            payload = row.get("payload")
            if not isinstance(payload, Mapping):
                raise WriterAttemptJournalError(
                    "writer attempt artifact payload is invalid")
            if stage == "packet":
                result["packet"] = payload.get("packet")
                if "source_evidence" in payload:
                    result["source_evidence"] = payload["source_evidence"]
                if "provider_dispatch_tracked" in payload:
                    result["provider_dispatch_tracked"] = payload["provider_dispatch_tracked"]
                if "prepared_prompt" in payload:
                    result["prepared_prompt"] = payload["prepared_prompt"]
                if "prepared_assembly" in payload:
                    result["prepared_assembly"] = payload["prepared_assembly"]
            elif stage == "provider_dispatch":
                result["provider_dispatch"] = payload.get("provider_dispatch")
            elif stage == "prompt":
                result["prompt"] = payload.get("prompt")
            elif stage == "terminal":
                result["terminal"] = payload.get("terminal")
            elif stage == "provider":
                result["provider"] = payload.get("provider")
            elif stage == "commit":
                result["commit"] = payload.get("commit")
        return result


__all__ = (
    "WRITER_ATTEMPT_SCHEMA_VERSION",
    "WriterAttemptIdentity",
    "WriterAttemptJournal",
    "WriterAttemptJournalError",
)
