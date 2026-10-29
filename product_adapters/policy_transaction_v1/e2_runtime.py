"""Explicit experimental structure-contract composition; no provider or storage."""
from __future__ import annotations
from dataclasses import dataclass
from collections.abc import Callable, Mapping
from framework.model_api import freeze_model_payload
from .rag_content import PolicyTransactionRagRuntimeBindingV2


@dataclass(frozen=True)
class PolicyTransactionE2RuntimeBinding(PolicyTransactionRagRuntimeBindingV2):
    candidate_response_projection: Callable | None = None
    response_projection_fingerprint: str | None = None
    historical_seed: object | None = None
    candidate_content_contract: Mapping | None = None

    def __post_init__(self):
        super().__post_init__()
        if self.candidate_content_contract is not None:
            if not isinstance(self.candidate_content_contract, Mapping):
                raise ValueError("candidate content contract must be an explicit mapping")
            object.__setattr__(self, "candidate_content_contract", freeze_model_payload(self.candidate_content_contract))
        if self.historical_seed is not None:
            from tools.e2_history_fork import E2HistoricalSeed
            if not isinstance(self.historical_seed, E2HistoricalSeed):
                raise ValueError("E2 historical seed must be an explicit verified source")
        if not callable(self.candidate_response_projection):
            raise ValueError("E2 binding requires explicit candidate structure projection")
        signature = self.response_projection_fingerprint
        if not isinstance(signature, str) or len(signature) != 64 or any(c not in "0123456789abcdef" for c in signature):
            raise ValueError("E2 structure projection requires a frozen identity")
