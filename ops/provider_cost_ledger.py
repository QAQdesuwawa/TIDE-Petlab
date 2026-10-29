"""Shared operational cost receipts; no model, state or Commit authority."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from framework.model_api import model_data_fingerprint, thaw_model_data


COST_RECEIPT_VERSION = "tide-provider-cost-receipt-v1"


def _record(value: object, field: str) -> dict[str, Any]:
    plain = thaw_model_data(value)
    if not isinstance(plain, dict):
        raise ValueError(f"{field} must be a mapping")
    return plain


def _tokens(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def build_exact_provider_cost_receipt(
    *, calls: Sequence[Mapping[str, Any]], pricing: Mapping[str, Any],
    pricing_fingerprint: str, rate_periods: Sequence[str],
) -> dict[str, Any]:
    """Price each observed call; never infer missing cache data or rate periods."""
    price = _record(pricing, "pricing")
    if model_data_fingerprint(price) != pricing_fingerprint:
        raise ValueError("pricing fingerprint differs from frozen rate card")
    if price.get("currency") != "CNY" or price.get("token_unit") != 1_000_000:
        raise ValueError("pricing currency or token unit is unsupported")
    if len(calls) != len(rate_periods):
        raise ValueError("one frozen rate period is required per provider call")
    rates = _record(price.get("rates"), "pricing rates")
    aliases = {str(item).lower() for item in price.get("aliases", [])}
    served_aliases = {
        str(item).strip().lower()
        for item in price.get("served_model_aliases", [])
        if str(item).strip()
    }
    rows = []
    total = 0.0
    for index, (raw_call, period) in enumerate(zip(calls, rate_periods)):
        call = _record(raw_call, f"call {index}")
        model = str(call.get("model") or "").rsplit("/", 1)[-1].lower()
        if model not in aliases:
            raise ValueError("provider call model is absent from frozen rate card")
        served_model = str(call.get("served_model") or "").strip()
        if not served_model:
            raise ValueError("provider call served model is missing")
        expected_served = str(price.get("served_model_version") or "").strip()
        accepted_served = served_aliases | ({expected_served.lower()} if expected_served else set())
        if accepted_served and served_model.lower() not in accepted_served:
            raise ValueError("provider served model differs from frozen rate card")
        period_rates = _record(rates.get(period), f"rate period {period}")
        hit = _tokens(call.get("prompt_cache_hit_tokens"), "cache-hit tokens")
        miss = _tokens(call.get("prompt_cache_miss_tokens"), "cache-miss tokens")
        output = _tokens(call.get("completion_tokens"), "completion tokens")
        prompt = _tokens(call.get("prompt_tokens"), "prompt tokens")
        if hit + miss != prompt:
            raise ValueError("cache split differs from prompt token total")
        completed = _tokens(call.get("completed_at_unix_ms"), "completion time")
        amount = (
            hit * float(period_rates["cache_hit_input"])
            + miss * float(period_rates["cache_miss_input"])
            + output * float(period_rates["output"])
        ) / int(price["token_unit"])
        material = {
            "ordinal": index,
            "operation_kind": call.get("operation_kind"),
            "model": call.get("model"),
            "served_model": served_model,
            "completed_at_unix_ms": completed,
            "rate_period": period,
            "prompt_cache_hit_tokens": hit,
            "prompt_cache_miss_tokens": miss,
            "completion_tokens": output,
            "cost_cny": round(amount, 10),
        }
        rows.append({**material, "fingerprint": model_data_fingerprint(material)})
        total += amount
    material = {
        "schema_version": COST_RECEIPT_VERSION,
        "status": "exact_from_provider_cache_split_and_frozen_rate_periods",
        "pricing_fingerprint": pricing_fingerprint,
        "currency": "CNY",
        "calls": rows,
        "total_cost_cny": round(total, 10),
    }
    return {**material, "fingerprint": model_data_fingerprint(material)}


__all__ = ("COST_RECEIPT_VERSION", "build_exact_provider_cost_receipt")
