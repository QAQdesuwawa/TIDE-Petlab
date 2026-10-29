"""Holder-labelled information at the package boundary; frozen CX math unchanged.

The reference parser uses discloser labels. Translate just its representation
on entry/exit; state, G, calibration, projection and eligibility stay holder-
labelled throughout this new binding. Old bindings never use this adapter.
"""
import json
from collections.abc import Mapping
from dataclasses import replace
from framework.model_api import InteractionModelManifest, thaw_model_data
from model_packages.cx_six_seven_v1.semantic_projection import SemanticProjection, VARIABLE_KEYS
from .semantic import coerce_located_projection
from .relation_disclosure import DisclosureAwareMultiPetPackage
from . import DesktopPetPackage

VERSION = 'desktop-pet-information-holder-v1'
SUFFIX = ':information-holder-v1'
PACKAGE_VERSION = '0.3.0-information-holder'
FLAG = 'information_holder_version'
_RENAME = {'L_self': 'L_opponent', 'L_opponent': 'L_self'}


def enabled(runtime):
    return runtime.model_runtime.manifest.parameter_package.get(FLAG) == VERSION


def _axes(values):
    return {_RENAME.get(key, key): value for key, value in values.items()}


def exchange_parser_labels(raw):
    """Involution of schema labels only, never another actor's root state."""
    if isinstance(raw, SemanticProjection):
        raw = raw.as_record()
    if not isinstance(raw, Mapping):
        return raw
    record = thaw_model_data(raw)
    if isinstance(record.get('variable_deltas'), dict):
        record['variable_deltas'] = _axes(record['variable_deltas'])
    presence = record.get('axis_presence')
    if isinstance(presence, dict):
        for key in ('provided_axes', 'missing_axes', 'forced_zero_axes', 'normalised_nonzero_axes'):
            values = presence.get(key)
            if isinstance(values, list):
                renamed = [_RENAME.get(value, value) for value in values]
                # The original presence schema has a canonical axis order.
                presence[key] = sorted(renamed, key=lambda axis: VARIABLE_KEYS.index(axis) if axis in VARIABLE_KEYS else len(VARIABLE_KEYS))
    return record


def _holder_result(result):
    if result is None:
        return None
    record = exchange_parser_labels(result)
    return replace(result, variable_deltas=record['variable_deltas'], axis_presence=record['axis_presence'])


def coerce_holder_projection(raw, *, event_kind, turn_id):
    return _holder_result(coerce_located_projection(exchange_parser_labels(raw), event_kind=event_kind, turn_id=turn_id))


class HolderSemanticCodec:
    """Compile the known contract fragments, leaving actual source text intact."""
    def __init__(self, base):
        self.base = base
        self.request_version = base.request_version + SUFFIX

    def __getattr__(self, name):
        return getattr(self.base, name)

    def build_request(self, *, event, recall_hint):
        system, user = self.base.build_request(event=event, recall_hint=recall_hint)
        fragments = {
            '- L_self：当前角色已经对外表达的立场、脆弱或信息。':
                '- L_self：当前角色持有的对端信息；对端实际披露并被当前角色获知时，量测本次新增获得的立场、脆弱或信息。',
            '- L_opponent：对端已经表达且当前角色可获知的立场、脆弱或信息。':
                '- L_opponent：关系对端持有的当前角色信息；当前角色实际对外披露时，量测本次让该对端新增获得的立场、脆弱或信息。',
            'peer_input 的受影响者是当前接收角色，L_self 为0；对端明确交出的信息可进入 L_opponent。npc_output 的受影响者是当前表达角色，L_opponent 为0；实际披露可进入 L_self。':
                'peer_input 的受影响者是当前接收角色，L_opponent 为0；对端明确交出且当前角色获知的信息进入 L_self。npc_output 的受影响者是当前表达角色，L_self 为0；实际向关系对端披露的信息进入 L_opponent。',
        }
        for old, new in fragments.items():
            if system.count(old) != 1:
                raise ValueError('holder semantic base contract differs')
            system = system.replace(old, new, 1)
        system = system.replace('L_self记录此次新增交出的信息', 'L_opponent记录此次实际交给关系对端的新信息')
        from .semantic import RESOURCE_ROOT
        thought = RESOURCE_ROOT.joinpath('thought.md').read_text('utf8')
        if thought in system:
            system = system.replace(thought, '## 内部自述量测\n这是当前角色已正式形成的私有经历；压力、关系需求、正向牵引和摩擦按本次作用量测。私有思考尚未让任何一方新增获得对方的信息，两项披露增量为0。归属、来源和证据保持当前角色的实际视角。')
        return system, user

    def parse_event_response(self, raw, *, event):
        from model_packages.cx_six_seven_v1.semantic_measurement import _strip_fences
        try:
            data = json.loads(_strip_fences(raw))
        except (TypeError, ValueError):
            return None
        result = self.base.parse_event_response(json.dumps(exchange_parser_labels(data), ensure_ascii=False), event=event)
        return _holder_result(result)


class HolderMultiPetPackage(DisclosureAwareMultiPetPackage):
    own_disclosure_axis = 'L_opponent'

    def __init__(self, *, parameter_package, owner_ref, human_ref):
        super().__init__(parameter_package=parameter_package, owner_ref=owner_ref, human_ref=human_ref)
        base = self.manifest
        fields = {key: getattr(base, key) for key in ('api_version', 'capability_profile', 'capabilities', 'constants_schema', 'parameters_schema', 'state_schema', 'measurement_schema', 'shared_projection_schema', 'progress_delta_schema')}
        versions = dict(base.operator_versions)
        for stage in ('measure_event', 'derive_event_contribution', 'settle_state'):
            versions[stage] = VERSION + '+' + versions[stage]
        self.manifest = InteractionModelManifest.create(package_id=base.package_id, package_version=PACKAGE_VERSION,
            **fields, parameter_package={**thaw_model_data(base.parameter_package), FLAG: VERSION}, operator_versions=versions)

    @staticmethod
    def _coerce_adjusted_measurement(raw, *, event_kind, turn_id):
        return coerce_holder_projection(raw, event_kind=event_kind, turn_id=turn_id)

    @staticmethod
    def _validate_committed_measurement(*, projection, local_event_id, event_kind):
        record = thaw_model_data(projection)
        normalized = coerce_holder_projection(record, event_kind=event_kind, turn_id=local_event_id)
        if normalized is None or normalized.as_record() != record:
            raise ValueError('holder measurement normalization differs')
        DesktopPetPackage._validate_committed_measurement(projection=exchange_parser_labels(record), local_event_id=local_event_id, event_kind=event_kind)
        return record

    def measure_event(self, *, payload, context):
        # New formal binding accepts source-bound measurements, as the product
        # already supplies. Never let the frozen raw branch use its old mask.
        measurement = payload.get('measurement_payload', {}) if isinstance(payload, Mapping) else {}
        if 'committed_measurement' not in measurement:
            raise ValueError('holder package requires source-bound committed measurement')
        return super().measure_event(payload=payload, context=context)
