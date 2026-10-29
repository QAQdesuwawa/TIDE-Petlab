"""Alpha 0.4 M4 observation-only F09 vector address lane.

The caller owns the committed query and the closed exact-ID candidate set.  This
module only evaluates vector address evidence.  It does not choose content,
consume numeric witnesses, mutate memory/state, or provide a product threshold.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from types import MappingProxyType

from ops.embeddings import make_rag_embedder
from ops.memory_contracts import AddressReceipt


MEMORY_ADDRESS_SHADOW_VERSION = "memory-address-shadow-v1"


def _identifier(value, field):
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} cannot be empty")
    return normalized


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value):
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


class _VectorFailure(ValueError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _vector(value, *, expected_dimension=None):
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _VectorFailure("invalid_vector")
    if not value:
        raise _VectorFailure("empty_vector")
    parsed = []
    for item in value:
        try:
            number = float(item)
        except (TypeError, ValueError) as exc:
            raise _VectorFailure("non_numeric") from exc
        if not math.isfinite(number):
            raise _VectorFailure("non_finite")
        parsed.append(number)
    if expected_dimension is not None and len(parsed) != expected_dimension:
        raise _VectorFailure("dimension_mismatch")
    norm = math.sqrt(sum(item * item for item in parsed))
    if norm <= 0.0:
        raise _VectorFailure("zero_norm")
    return tuple(parsed), norm


def _cosine(left, left_norm, right, right_norm):
    value = sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)
    return max(-1.0, min(1.0, value))


def _failed_receipts(event_ids, *, provider, model, policy_version):
    return tuple(AddressReceipt(
        event_id=event_id,
        status="failed",
        similarity=None,
        provider=provider,
        model=model,
        policy_version=policy_version,
    ) for event_id in event_ids)


@dataclass(frozen=True)
class AddressShadowResult:
    receipts: tuple[AddressReceipt, ...]
    batch_status: str
    operator_version: str
    receipt: Mapping[str, object]

    def __post_init__(self):
        normalized = tuple(self.receipts)
        if not normalized or any(not isinstance(item, AddressReceipt) for item in normalized):
            raise ValueError("receipts must contain AddressReceipt values")
        ids = [item.event_id for item in normalized]
        if len(set(ids)) != len(ids):
            raise ValueError("address receipt event IDs must be unique")
        if self.batch_status not in {"shadow", "unavailable", "failed", "partial_failed"}:
            raise ValueError("batch_status is unsupported")
        object.__setattr__(self, "receipts", normalized)
        object.__setattr__(self, "operator_version", _identifier(
            self.operator_version, "operator_version"))
        if not isinstance(self.receipt, Mapping):
            raise ValueError("receipt must be a mapping")
        object.__setattr__(self, "receipt", _freeze(self.receipt))

    def to_record(self):
        return {
            "schema_version": 1,
            "kind": "memory_address_shadow",
            "batch_status": self.batch_status,
            "operator_version": self.operator_version,
            "receipts": [item.to_record() for item in self.receipts],
            "receipt": _thaw(self.receipt),
        }


def derive_shadow_address_receipts(
    *,
    event_ids: Sequence[str],
    event_texts: Mapping[str, str],
    query_text: str,
    embedder,
    provider: str,
    model: str,
    policy_version: str,
) -> AddressShadowResult:
    """Publish per-ID vector similarities for one explicit closed candidate set."""
    ids = tuple(_identifier(item, "event_id") for item in event_ids)
    if not ids:
        raise ValueError("event_ids must be non-empty")
    if len(set(ids)) != len(ids):
        raise ValueError("event_ids must be unique")
    if not isinstance(event_texts, Mapping):
        raise ValueError("event_texts must be a mapping")
    text_keys = {str(key) for key in event_texts}
    if text_keys != set(ids):
        raise ValueError("event_texts keys must exactly match closed event_ids")
    texts = {}
    for event_id in ids:
        value = event_texts[event_id]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"event_texts[{event_id}] must be non-empty text")
        texts[event_id] = value
    if not isinstance(query_text, str) or not query_text.strip():
        raise ValueError("query_text must be non-empty committed text")

    policy = _identifier(policy_version, "policy_version")
    ordered_ids = tuple(sorted(ids))
    provider_id = str(provider or "").strip()
    model_id = str(model or "").strip()
    base_receipt = {
        "operator_version": MEMORY_ADDRESS_SHADOW_VERSION,
        "candidate_policy": "explicit_closed_exact_ids",
        "qualification_policy": "unfrozen_shadow_only",
        "provider": provider_id,
        "model": model_id,
        "policy_version": policy,
        "candidate_count": len(ordered_ids),
    }

    if embedder is None:
        receipts = tuple(AddressReceipt(
            event_id=event_id,
            status="unavailable",
            similarity=None,
            provider=provider_id,
            model=model_id,
            policy_version=policy,
        ) for event_id in ordered_ids)
        return AddressShadowResult(
            receipts=receipts,
            batch_status="unavailable",
            operator_version=MEMORY_ADDRESS_SHADOW_VERSION,
            receipt={
                **base_receipt,
                "embedding_call_count": 0,
                "successful_count": 0,
                "failed_count": 0,
                "failure_reasons": {"provider": "unavailable"},
            },
        )

    if not callable(embedder):
        raise ValueError("embedder must implement the callable vector protocol")
    if getattr(embedder, "fallback_relevance_guard", False):
        raise ValueError("fallback embedding providers cannot serve formal F09")
    provider_id = _identifier(provider_id, "provider")
    model_id = _identifier(model_id, "model")
    base_receipt["provider"] = provider_id
    base_receipt["model"] = model_id

    call_count = 1
    try:
        query_raw = embedder(query_text)
    except Exception as exc:  # provider boundary: failure stays in F09
        reason = f"provider_error:{type(exc).__name__}"
        return AddressShadowResult(
            receipts=_failed_receipts(
                ordered_ids, provider=provider_id, model=model_id,
                policy_version=policy),
            batch_status="failed",
            operator_version=MEMORY_ADDRESS_SHADOW_VERSION,
            receipt={
                **base_receipt,
                "embedding_call_count": call_count,
                "successful_count": 0,
                "failed_count": len(ordered_ids),
                "failure_reasons": {"query": reason},
            },
        )
    try:
        query_vector, query_norm = _vector(query_raw)
    except _VectorFailure as exc:
        return AddressShadowResult(
            receipts=_failed_receipts(
                ordered_ids, provider=provider_id, model=model_id,
                policy_version=policy),
            batch_status="failed",
            operator_version=MEMORY_ADDRESS_SHADOW_VERSION,
            receipt={
                **base_receipt,
                "embedding_call_count": call_count,
                "successful_count": 0,
                "failed_count": len(ordered_ids),
                "failure_reasons": {"query": exc.reason},
            },
        )

    receipts = []
    failures = {}
    for event_id in ordered_ids:
        call_count += 1
        try:
            candidate_raw = embedder(texts[event_id])
            candidate_vector, candidate_norm = _vector(
                candidate_raw, expected_dimension=len(query_vector))
        except _VectorFailure as exc:
            failures[event_id] = exc.reason
            receipts.append(AddressReceipt(
                event_id=event_id, status="failed", similarity=None,
                provider=provider_id, model=model_id, policy_version=policy))
            continue
        except Exception as exc:  # provider boundary: one failed candidate stays local
            failures[event_id] = f"provider_error:{type(exc).__name__}"
            receipts.append(AddressReceipt(
                event_id=event_id, status="failed", similarity=None,
                provider=provider_id, model=model_id, policy_version=policy))
            continue
        receipts.append(AddressReceipt(
            event_id=event_id,
            status="shadow",
            similarity=_cosine(
                query_vector, query_norm, candidate_vector, candidate_norm),
            provider=provider_id,
            model=model_id,
            policy_version=policy,
        ))

    successful_count = len(receipts) - len(failures)
    if not failures:
        batch_status = "shadow"
    elif successful_count:
        batch_status = "partial_failed"
    else:
        batch_status = "failed"
    return AddressShadowResult(
        receipts=tuple(receipts),
        batch_status=batch_status,
        operator_version=MEMORY_ADDRESS_SHADOW_VERSION,
        receipt={
            **base_receipt,
            "query_dimension": len(query_vector),
            "embedding_call_count": call_count,
            "successful_count": successful_count,
            "failed_count": len(failures),
            "failure_reasons": failures,
        },
    )


def derive_configured_shadow_address_receipts(
    *,
    event_ids: Sequence[str],
    event_texts: Mapping[str, str],
    query_text: str,
    provider: str,
    model: str,
    policy_version: str,
    provider_factory=make_rag_embedder,
) -> AddressShadowResult:
    """Resolve the official real-provider/None boundary, then run the pure lane.

    Provider and model identities stay explicit: this adapter never guesses them
    from a class name, endpoint, chat configuration, or fallback implementation.
    """
    if not callable(provider_factory):
        raise ValueError("provider_factory must be callable")
    embedder = provider_factory()
    return derive_shadow_address_receipts(
        event_ids=event_ids,
        event_texts=event_texts,
        query_text=query_text,
        embedder=embedder,
        provider=provider,
        model=model,
        policy_version=policy_version,
    )