"""Optional user-preference fitting and reverse-calibration extension.

The manifest freezes architectural ownership only. Legacy product adapters
remain under ``ops`` until they are migrated in targeted, receipt-preserving
slices; importing this module does not activate or import those adapters.
"""
from interaction_extensions import InteractionExtensionManifest


EXTENSION_MANIFEST = InteractionExtensionManifest(
    extension_id="tide.preference-calibration",
    version="1.0.0-alpha04",
    capabilities=(
        "preference_binding",
        "explicit_feedback_attribution",
        "feedback_intent_interpretation",
        "b0_version_archive",
        "reverse_calibration",
        "calibration_replay",
    ),
    required_framework_contracts=(
        "versioned_parameters",
        "committed_event",
        "receipt_envelope",
    ),
    required=False,
)


__all__ = ("EXTENSION_MANIFEST",)
