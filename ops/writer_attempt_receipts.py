"""Product bridge from a committed F16 receipt to a Writer attempt audit.

This bridge does not create, persist, or infer an action.  The caller must pass
the exact receipt that has already crossed the product commit boundary and its
ledger reference.  Only the body-free fingerprint is copied into the attempt
commit stage.
"""
from __future__ import annotations

from collections.abc import Mapping

from framework.model_api import model_data_fingerprint
from product_adapters.cx_six_seven_v1.writer_attempts import WriterAttemptJournal


_BODY_KEYS = frozenset({
    "body", "candidate", "content", "message", "prompt", "reply",
    "system", "text", "user",
})


def _identifier(value, field):
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} must be a non-empty identifier")
    return result


def _contains_body_key(value):
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).strip().lower() in _BODY_KEYS:
                return True
            if _contains_body_key(nested):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_body_key(item) for item in value)
    return False


def link_writer_attempt_to_action_receipt(
    journal,
    *,
    writer_request_id,
    action_receipt,
    action_receipt_ref,
):
    """Link candidate-ready audit evidence to one exact committed F16 receipt."""
    if not isinstance(journal, WriterAttemptJournal):
        raise ValueError("writer action link requires WriterAttemptJournal")
    if not isinstance(action_receipt, Mapping):
        raise ValueError("action receipt must be a mapping")
    receipt = dict(action_receipt)
    if _contains_body_key(receipt):
        raise ValueError("action receipt link cannot contain body fields")
    action_id = _identifier(receipt.get("action_id"), "action receipt action ID")
    _identifier(receipt.get("source"), "action receipt source")
    _identifier(receipt.get("params_sha"), "action receipt parameter fingerprint")
    ts = receipt.get("ts")
    if isinstance(ts, bool) or not isinstance(ts, int) or ts < 0:
        raise ValueError("action receipt ts must be a non-negative integer")
    receipt_ref = _identifier(action_receipt_ref, "action receipt ref")
    receipt_fingerprint = model_data_fingerprint(receipt)
    journal_receipt = journal.link_committed_action(
        _identifier(writer_request_id, "writer request ID"),
        action_id=action_id,
        action_receipt_ref=receipt_ref,
        action_receipt_fingerprint=receipt_fingerprint,
    )
    return {
        "writer_request_id": str(writer_request_id),
        "action_id": action_id,
        "action_receipt_ref": receipt_ref,
        "action_receipt_fingerprint": receipt_fingerprint,
        "journal_receipt_fingerprint": journal_receipt["artifact_fingerprint"],
    }


__all__ = ("link_writer_attempt_to_action_receipt",)
