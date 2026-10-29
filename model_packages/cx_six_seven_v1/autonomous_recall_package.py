"""Opt-in Full package: a settled tick's shared vector is its recall basis.

The host must verify the actual shared stage receipt before invoking this
operator. Package checks bind the numerical material to that settlement; they
do not confer History-read, sending, or Commit authority.
"""
from framework.model_api import InteractionModelManifest, model_data_fingerprint
from framework.model_api.identity import context_fact_identity

from .full_v2_package import (
    CxSixSevenFullV2Package, _evaluation_time_ms, _legacy_memory_adapter_context,
)
from .v2_package import (
    _constant_binding, _exact_payload, _mapping,
    _verified_settlement, _verified_shared_projection,
)


AUTONOMOUS_RECALL_PACKAGE_VERSION = "2.5.0-alpha04-shared-tick-recall"
AUTONOMOUS_RECALL_OPERATOR_VERSION = "cx67-event-or-shared-tick-recall-v1"


class CxSixSevenAutonomousRecallPackage(CxSixSevenFullV2Package):
    """Same formulas, explicit additional autonomous-current input contract."""

    def __init__(self, *, parameter_package):
        super().__init__(parameter_package=parameter_package)
        old = self.manifest
        versions = dict(old.operator_versions)
        versions["recall_history"] = AUTONOMOUS_RECALL_OPERATOR_VERSION
        self.manifest = InteractionModelManifest.create(
            **{name: getattr(old, name) for name in (
                "package_id", "api_version", "capability_profile", "capabilities",
                "constants_schema", "parameters_schema", "state_schema",
                "measurement_schema", "shared_projection_schema",
                "progress_delta_schema", "parameter_package",
            )},
            package_version=AUTONOMOUS_RECALL_PACKAGE_VERSION,
            operator_versions=versions,
        )

    def recall_history(self, *, payload, context):
        if "shared_projection" not in payload:
            return super().recall_history(payload=payload, context=context)
        _exact_payload(payload, {
            "candidate_event_ids", "historical_live", "parameters",
            "settlement", "shared_projection",
        }, "recall_history")
        identity = context_fact_identity(context)
        if (context.metadata.get("committed_event_kind") != "tick"
                or identity.scope_ref is not None or identity.event_ref is None
                or identity.action_ref is not None):
            raise ValueError("shared recall requires an owner-local tick context")
        settlement = _verified_settlement(payload["settlement"])
        if tuple(settlement["applied_contributions"]) != ("progress",):
            raise ValueError("shared tick recall requires progress-only settlement")
        shared = _verified_shared_projection(payload["shared_projection"])
        binding = _constant_binding(payload["parameters"])
        if shared.get("source_settlement_fingerprint") != settlement["fingerprint"]:
            raise ValueError("shared recall settlement source differs")
        if model_data_fingerprint(shared.get("source_state_candidate")) != model_data_fingerprint(settlement["state_candidate"]):
            raise ValueError("shared recall current state differs from settlement")
        if shared.get("constant_binding_version") != binding["effective_constants_version"]:
            raise ValueError("shared recall constant binding differs")
        if shared.get("formula_version") != self._package_params("shared_projection")["version"]:
            raise ValueError("shared recall formula version differs")
        receipt = _mapping(shared.get("receipt"), "shared projection receipt")
        if model_data_fingerprint(receipt.get("effective_constants")) != model_data_fingerprint(binding["effective_constants"]):
            raise ValueError("shared recall effective constants differ")
        transition = _mapping(receipt.get("transition"), "shared transition")
        for name, state in (("before", settlement["state_before"]),
                            ("after", settlement["state_candidate"])):
            side = _mapping(transition.get(name), "shared transition " + name)
            if model_data_fingerprint(side.get("roots")) != model_data_fingerprint(state):
                raise ValueError("shared recall transition differs from settlement")
        if model_data_fingerprint(receipt.get("final_vector")) != model_data_fingerprint(shared["vector"]):
            raise ValueError("shared recall vector differs from projection receipt")
        # Remove the shared wrapper only. Preserve the exact numerical projection
        # and bind its current snapshot to the existing output fingerprint.
        projection = {name: shared[name] for name in (
            "vector", "formula_version", "constant_binding_version", "receipt",
        )}
        evaluation_ms = _evaluation_time_ms(context)
        return self._memory_recall.recall_history(payload={
            "candidate_event_ids": payload["candidate_event_ids"],
            "historical_live": payload["historical_live"],
            "current": {
                "event_id": identity.event_ref,
                "evaluation_time_ms": evaluation_ms,
                "source_receipt_ref": shared["fingerprint"],
                "projection": projection,
            },
            "params": self._package_params("actual_recall"),
        }, context=_legacy_memory_adapter_context(context, evaluation_time_ms=evaluation_ms))
