"""One derived pet package; the existing Full reference math is inherited."""
from __future__ import annotations

from collections.abc import Mapping

from framework.model_api import InteractionModelManifest
from framework.model_api.identity import context_fact_identity
from model_packages.cx_six_seven_v1.full_v2_package import CxSixSevenFullV2Package
from .events import EVENT_SCHEMA, PetEvent

PACKAGE_ID = "tide.desktop-pet"
PACKAGE_VERSION = "0.1.0-event-contract"


class DesktopPetPackage(CxSixSevenFullV2Package):
    def __init__(self, *, parameter_package):
        super().__init__(parameter_package=parameter_package)
        from model_packages.cx_six_seven_v1.memory_live_operators import CxSixSevenMemoryLiveAdapter
        from .memory_runtime import readout_format
        self._memory_live = CxSixSevenMemoryLiveAdapter(receipt_format_resolver=readout_format)
        base = self.manifest
        operators = dict(base.operator_versions)
        operators["measure_event"] = "desktop-pet-measure-event-v1+" + operators["measure_event"]
        operators["prepare_memory_inputs"] = "desktop-pet-disabled-b0-source-validation-v1+" + operators["prepare_memory_inputs"]
        self.reference_manifest = base
        self.manifest = InteractionModelManifest.create(
            package_id=PACKAGE_ID, package_version=PACKAGE_VERSION,
            api_version=base.api_version, capability_profile=base.capability_profile,
            capabilities=base.capabilities, constants_schema=base.constants_schema,
            parameters_schema=base.parameters_schema, state_schema=base.state_schema,
            measurement_schema={
                "event_contract": EVENT_SCHEMA, "reference_measurement": base.measurement_schema,
                "reference_package": {"package_id": base.package_id, "package_version": base.package_version,
                                      "schema_fingerprint": base.schema_fingerprint},
            }, shared_projection_schema=base.shared_projection_schema,
            progress_delta_schema=base.progress_delta_schema,
            parameter_package=base.parameter_package, operator_versions=operators,
        )

    def measure_event(self, *, payload, context):
        if not isinstance(payload, Mapping) or set(payload) != {"event", "measurement_payload"}:
            raise ValueError("desktop pet measure_event requires the unified event contract")
        event = PetEvent.from_record(payload["event"])
        identity = context_fact_identity(context)
        if (identity.event_ref != event.event_id or identity.subject_ref != event.owner_ref
                or identity.scope_ref != event.scope_ref
                or context.temporal_position.fingerprint != event.position.fingerprint):
            raise ValueError("event identity differs from the formal context")
        if (context.metadata.get("desktop_pet_event_fingerprint") != event.fingerprint
                or context.metadata.get("source_receipt_fingerprint") != event.source_binding_ref):
            raise ValueError("event source differs from product-verified context")
        measurement = payload["measurement_payload"]
        if not isinstance(measurement, Mapping) or measurement.get("event_kind") != event.event_kind:
            raise ValueError("event direction differs from measurement")
        record=measurement.get('committed_measurement',{})
        frame=record.get('reference_frame') or {}
        if isinstance(frame,Mapping) and frame.get('schema_version')==2:
            from .semantic import evidence_source
            text=evidence_source(event)
            for claim in frame.get('relation_claims',()):
                span=claim.get('evidence_span')
                if (not isinstance(span,(list,tuple)) or len(span)!=2
                        or any(type(x) is not int for x in span)
                        or not 0<=span[0]<span[1]<=len(text)
                        or text.count(text[span[0]:span[1]])!=1):
                    raise ValueError('local reference evidence is not unique in actual event')
        from .recall_model import select
        grounded_recall=select(measurement.get('pet_memory_recall',{}).get('version'))
        if grounded_recall.KEY in measurement:
            expected=grounded_recall.make_cue(event,measurement[grounded_recall.KEY])
            from framework.model_api import thaw_model_data
            if thaw_model_data(context.metadata.get(grounded_recall.KEY,{}).get('cue'))!=expected:
                raise ValueError('recall cue differs from actual event evidence')
        calibration=measurement.get('local_calibration_reference')
        plain={k:v for k,v in measurement.items() if k not in ('local_calibration_reference','pet_memory_recall')}
        result=super().measure_event(payload=plain,context=context)
        if calibration is not None:
            from framework.model_api import thaw_model_data,model_data_fingerprint
            if (calibration.get('version')!='desktop-pet-local-calibration-reference-v1'
                    or calibration.get('event_fingerprint')!=event.fingerprint
                    or calibration.get('fingerprint')!=model_data_fingerprint({k:v for k,v in calibration.items() if k!='fingerprint'})
                    or context.metadata.get('desktop_pet_local_calibration_source')!=calibration['fingerprint']):
                raise ValueError('local calibration lacks actual source authority')
            from .measurement_calibration import calibrate as v3,VERSION as V3
            from .measurement_constraint import calibrate as v4,VERSION as V4
            version=calibration.get('calibration_version',V3)
            if version not in (V3,V4):raise ValueError('unsupported sealed calibration version')
            calibrate={V3:v3,V4:v4}[version]
            raw=thaw_model_data(result['measurement'])
            receipt=calibrate(stimulus=raw['variable_deltas'],state=calibration['state'],
                constants=calibration['constants'],constants_version=calibration['constants_version'],
                response_parameters=calibration['response_parameters'],previous_stimulus=calibration['previous_stimulus'],previous_delta=calibration['previous_delta'])
            adjusted=self._coerce_adjusted_measurement({**raw,'variable_deltas':receipt['adjusted_stimulus']},event_kind=event.event_kind,turn_id=event.event_id).as_record()
            result={'measurement':adjusted,'local_calibration':{'version':receipt['version'],'reference_fingerprint':calibration['fingerprint'],
                'raw_measurement_fingerprint':model_data_fingerprint(raw),'adjusted_measurement_fingerprint':model_data_fingerprint(adjusted),'receipt':receipt}}
        return result

    @staticmethod
    def _coerce_adjusted_measurement(raw, *, event_kind, turn_id):
        from .semantic import coerce_located_projection
        return coerce_located_projection(raw,event_kind=event_kind,turn_id=turn_id)

    @staticmethod
    def _validate_committed_measurement(*,projection,local_event_id,event_kind):
        from framework.model_api import thaw_model_data
        frame=projection.get('reference_frame') or {}
        if not isinstance(frame,Mapping) or frame.get('schema_version')!=2:
            return CxSixSevenFullV2Package._validate_committed_measurement(
                projection=projection,local_event_id=local_event_id,event_kind=event_kind)
        from .semantic import coerce_located_projection
        record=thaw_model_data(projection)
        from model_packages.cx_six_seven_v1.reference_frame import coerce_reference_frame
        neutral_frame=coerce_reference_frame({'delivery_scope':'unresolved'},turn_id=local_event_id).as_record()
        CxSixSevenFullV2Package._validate_committed_measurement(
            projection={**record,'reference_frame':neutral_frame},local_event_id=local_event_id,event_kind=event_kind)
        result=coerce_located_projection(record,turn_id=local_event_id,event_kind=event_kind)
        if result is None or result.as_record()!=record:
            raise ValueError('local evidence measurement normalization differs')
        return record


    def prepare_memory_inputs(self, *, payload, context):
        if isinstance(payload, Mapping) and "memory_source_view" in payload:
            from .memory_index_query import prepare
            return prepare(self, payload=payload, context=context)
        from .memory_topology import enabled,compact
        from . import memory_runtime, memory_batch
        from interaction_extensions.memory_source_table import CONTEXT_KEY, VERSION, decode
        wire_payload = payload
        source_encoding = context.metadata.get(CONTEXT_KEY)
        if source_encoding is not None:
            if source_encoding != VERSION:
                raise ValueError("unsupported memory source encoding")
            payload = decode(payload)
        result=super().prepare_memory_inputs(payload=payload,context=context)
        if memory_batch.enabled(context):
            return memory_batch.pack(result,wire_payload,context,
                parameter_version=self._package_params("memory_forward_readout")["version"])
        if memory_runtime.enabled(context):
            return memory_runtime.compact(result,payload,context)
        return compact(result) if enabled(context) else result

    def recall_history(self, *, payload, context):
        from . import memory_batch
        if memory_batch.enabled(context):
            payload = {**payload, "historical_live": memory_batch.recall_inputs(
                payload["historical_live"], context, payload["candidate_event_ids"])}
        from .recall_model import select
        grounded_recall=select(context.metadata.get('pet_memory_recall',{}).get('version'))
        result = (grounded_recall.recall(self,payload=payload,context=context)
            if grounded_recall.enabled(context) else super().recall_history(payload=payload, context=context))
        return memory_batch.compact_recall(result) if memory_batch.enabled(context) else result

    def read_historical_influence(self, *, payload, context):
        from . import memory_batch
        if memory_batch.enabled(context):
            payload = {**payload, "historical_readouts": memory_batch.influence_inputs(
                payload["historical_readouts"], context, payload["recalled_event_ids"])}
            return super().read_historical_influence(payload=payload, context=context)
        from .memory_topology import verify
        if isinstance(payload,Mapping) and 'historical_readouts' in payload:
            from . import memory_runtime
            if memory_runtime.enabled(context):
                memory_runtime.verify(payload['historical_readouts'],context)
            else:
                if any('prepared_nodes' in row.get('receipt',{}) for row in payload['historical_readouts']):
                    raise ValueError('runtime memory evidence requires its explicit context version')
                verify(payload['historical_readouts'],context)
        return super().read_historical_influence(payload=payload,context=context)


__all__ = ("DesktopPetPackage", "PACKAGE_ID", "PACKAGE_VERSION")

