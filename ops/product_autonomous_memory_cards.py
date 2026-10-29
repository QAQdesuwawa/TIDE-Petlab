"""Explicit reference-product Memory composition for a committed tick grant."""
from framework.model_api import thaw_model_data
from ops.runtime_autonomous_memory_selection import execute_runtime_autonomous_memory_selection


class ProductAutonomousMemoryCardsProvider:
    def __init__(self, *, model_runtime, content_provider):
        if not callable(getattr(content_provider, "from_autonomous_selection", None)):
            raise TypeError("autonomous memory needs the explicit content adapter")
        self.model_runtime = model_runtime
        self.content_provider = content_provider

    def __call__(self, grant):
        selected = execute_runtime_autonomous_memory_selection(
            observation=grant, model_runtime=self.model_runtime)
        occupation, cards = self.content_provider.from_autonomous_selection(selected,
            session_binding_fingerprint=grant.session.session_binding.fingerprint)
        # Operational evidence is archived beside packet, never rendered as
        # fields, and never promoted to a committed interaction fact.
        evidence = {
            "version": "product-autonomous-memory-source-v1",
            "selection_receipt": thaw_model_data(selected.receipt),
            "preparation_stage_receipt": selected.inputs.preparation_execution.receipt.to_record(),
            "preparation_output": thaw_model_data(selected.inputs.preparation_execution.payload),
            "recall_stage_receipt": selected.recall_execution.receipt.to_record(),
            "recall_output": thaw_model_data(selected.recall_execution.payload),
            "selected_history_read": selected.history_read.to_record(),
        }
        return occupation, cards, evidence
