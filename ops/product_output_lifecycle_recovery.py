"""Body-free recovery identity for canonical output lifecycle work."""
from __future__ import annotations

from collections.abc import Mapping
import json
import os
import threading
from typing import Any

from framework.model_api import model_data_fingerprint, thaw_model_data
from interaction_extensions.multi_participant.sessions import (
    ProductConversationSessionRegistry,
)
from ops.runtime_product_action_commit import (
    RuntimeCommittedProductAction,
    restore_committed_product_action,
)


PRODUCT_OUTPUT_LIFECYCLE_RECOVERY_VERSION = (
    "product-output-lifecycle-recovery-v1")


def _verified(row: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise ValueError("output lifecycle recovery row must be record data")
    material = dict(row)
    fingerprint = material.pop("fingerprint", None)
    if model_data_fingerprint(material) != fingerprint:
        raise ValueError("output lifecycle recovery fingerprint is invalid")
    if material.get("version") != PRODUCT_OUTPUT_LIFECYCLE_RECOVERY_VERSION:
        raise ValueError("output lifecycle recovery version is invalid")
    return material


class ProductOutputLifecycleRecoveryJournal:
    """Append-only pending/completed stages; it never stores source text."""

    def __init__(self, *, registry, conversation_id, session_binding):
        if not isinstance(registry, ProductConversationSessionRegistry):
            raise TypeError("output recovery requires conversation registry")
        self.registry = registry
        self.conversation_id = str(conversation_id or "").strip()
        self.session_binding = session_binding
        self.path = registry.output_lifecycle_journal_path(
            self.conversation_id)
        self._durable_writes = registry._durable_writes
        self._lock = threading.RLock()

    def read_rows(self) -> tuple[dict[str, Any], ...]:
        if not os.path.isfile(self.path):
            return ()
        try:
            with open(self.path, "r", encoding="utf-8-sig") as handle:
                rows = tuple(
                    json.loads(line) for line in handle if line.strip())
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("output lifecycle recovery journal cannot be read") from exc
        states = {}
        for row in rows:
            material = _verified(row)
            stage = material.get("stage")
            event_id = material.get("event_id")
            binding_fingerprint = material.get(
                "session_binding_fingerprint")
            if (
                material.get("conversation_id") != self.conversation_id
                or not binding_fingerprint
                or stage not in {"pending", "completed"}
                or not event_id
            ):
                raise ValueError("output lifecycle recovery identity is invalid")
            state_key = (binding_fingerprint, event_id)
            prior = states.get(state_key)
            if stage == "pending":
                if prior is not None:
                    raise ValueError("output lifecycle pending stage is duplicated")
                if set(material) != {
                    "version", "stage", "conversation_id", "event_id",
                    "action_id", "participant_id", "source_runtime_ordinal",
                    "session_binding_fingerprint", "action_plan",
                    "action_receipt", "source_receipt_fingerprint", "authority",
                }:
                    raise ValueError("output lifecycle pending fields are invalid")
                states[state_key] = row
            else:
                if prior is None or prior.get("stage") != "pending":
                    raise ValueError("output lifecycle completion lacks pending stage")
                if set(material) != {
                    "version", "stage", "conversation_id", "event_id",
                    "action_id", "participant_id", "source_runtime_ordinal",
                    "session_binding_fingerprint", "pending_fingerprint",
                    "lifecycle_receipt_fingerprint", "route_receipt_fingerprint",
                    "authority",
                }:
                    raise ValueError("output lifecycle completed fields are invalid")
                if material.get("pending_fingerprint") != prior.get("fingerprint"):
                    raise ValueError("output lifecycle completion differs from pending")
                states[state_key] = row
        return rows

    def _append(self, row: Mapping[str, Any]) -> dict[str, Any]:
        record = dict(row)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        try:
            with open(self.path, "a", encoding="utf-8", newline="") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                if self._durable_writes:
                    os.fsync(handle.fileno())
        except OSError as exc:
            raise ValueError("output lifecycle recovery journal write failed") from exc
        return record

    def begin(self, action: RuntimeCommittedProductAction) -> Mapping[str, Any]:
        event = action.event
        plan = action.action_plan
        material = {
            "version": PRODUCT_OUTPUT_LIFECYCLE_RECOVERY_VERSION,
            "stage": "pending",
            "conversation_id": event.conversation_id,
            "event_id": event.event_id,
            "action_id": plan.action_id,
            "participant_id": event.sender_participant_id,
            "source_runtime_ordinal": plan.source_runtime_ordinal,
            "session_binding_fingerprint": self.session_binding.fingerprint,
            "action_plan": dict(vars(plan)),
            "action_receipt": thaw_model_data(action.receipt),
            "source_receipt_fingerprint": model_data_fingerprint(
                action.source_receipt),
            "authority": {
                "writer": False, "source_write": False,
                "dynamic_tick": False, "send": False,
            },
        }
        candidate = dict(
            material, fingerprint=model_data_fingerprint(material))
        with self._lock:
            rows = self.read_rows()
            matches = tuple(
                row for row in rows
                if row.get("event_id") == event.event_id
                and row.get("session_binding_fingerprint")
                == self.session_binding.fingerprint)
            if matches:
                if matches[0] != candidate:
                    raise ValueError("output lifecycle pending identity conflicts")
                return matches[0]
            return self._append(candidate)

    def complete(self, action, *, pending, lifecycle, route):
        material = {
            "version": PRODUCT_OUTPUT_LIFECYCLE_RECOVERY_VERSION,
            "stage": "completed",
            "conversation_id": action.event.conversation_id,
            "event_id": action.event.event_id,
            "action_id": action.action_plan.action_id,
            "participant_id": action.event.sender_participant_id,
            "source_runtime_ordinal": action.action_plan.source_runtime_ordinal,
            "session_binding_fingerprint": self.session_binding.fingerprint,
            "pending_fingerprint": pending["fingerprint"],
            "lifecycle_receipt_fingerprint": model_data_fingerprint(
                lifecycle.receipt),
            "route_receipt_fingerprint": model_data_fingerprint(route.receipt),
            "authority": {
                "writer": False, "source_write": False,
                "dynamic_tick": False, "send": False,
            },
        }
        candidate = dict(
            material, fingerprint=model_data_fingerprint(material))
        with self._lock:
            rows = self.read_rows()
            matches = tuple(
                row for row in rows
                if row.get("event_id") == action.event.event_id
                and row.get("session_binding_fingerprint")
                == self.session_binding.fingerprint
                and row.get("stage") == "completed"
            )
            if matches:
                if len(matches) != 1 or matches[0] != candidate:
                    raise ValueError("output lifecycle completion conflicts")
                return matches[0]
            return self._append(candidate)

    def pending_actions(self) -> tuple[RuntimeCommittedProductAction, ...]:
        rows = self.read_rows()
        completed = {
            row["event_id"] for row in rows
            if row.get("stage") == "completed"
            and row.get("session_binding_fingerprint")
            == self.session_binding.fingerprint}
        pending = tuple(
            row for row in rows
            if row.get("stage") == "pending"
            and row.get("session_binding_fingerprint")
            == self.session_binding.fingerprint
            and row["event_id"] not in completed
        )
        restored = []
        for row in pending:
            action = restore_committed_product_action(
                action_plan_record=row["action_plan"],
                action_receipt=row["action_receipt"],
                conversation_registry=self.registry,
                session_binding=self.session_binding,
            )
            if (
                row["conversation_id"] != action.event.conversation_id
                or row["event_id"] != action.event.event_id
                or row["action_id"] != action.action_plan.action_id
                or row["participant_id"] != action.event.sender_participant_id
                or row["source_runtime_ordinal"]
                != action.action_plan.source_runtime_ordinal
                or row["source_receipt_fingerprint"]
                != model_data_fingerprint(action.source_receipt)
            ):
                raise ValueError(
                    "output lifecycle pending row differs from committed action")
            restored.append(action)
        return tuple(restored)


__all__ = (
    "PRODUCT_OUTPUT_LIFECYCLE_RECOVERY_VERSION",
    "ProductOutputLifecycleRecoveryJournal",
)
