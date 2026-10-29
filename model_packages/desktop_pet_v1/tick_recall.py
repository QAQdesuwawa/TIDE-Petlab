"""Pet wire adapter over the unchanged shared-tick recall mathematics."""
from framework.model_api import InteractionModelManifest
from model_packages.cx_six_seven_v1.autonomous_recall_package import CxSixSevenAutonomousRecallPackage
from . import memory_batch


class DesktopPetTickRecallPackage(CxSixSevenAutonomousRecallPackage):
    def __init__(self,*,parameter_package):
        super().__init__(parameter_package=parameter_package)
        old=self.manifest
        self.manifest=InteractionModelManifest.create(
            **{name:getattr(old,name) for name in ('api_version','capability_profile','capabilities',
               'constants_schema','parameters_schema','state_schema','measurement_schema',
               'shared_projection_schema','progress_delta_schema','parameter_package','operator_versions')},
            package_id='tide.desktop-pet-tick-memory',package_version='2.5.1-pet-indexed-source')

    def recall_history(self,*,payload,context):
        if memory_batch.enabled(context):
            payload={**payload,'historical_live':memory_batch.recall_inputs(
                payload['historical_live'],context,payload['candidate_event_ids'])}
        from .recall_model import select
        grounded_recall=select(context.metadata.get('pet_memory_recall',{}).get('version'))
        result=grounded_recall.recall(self,payload=payload,context=context) if grounded_recall.enabled(context) else super().recall_history(payload=payload,context=context)
        return memory_batch.compact_recall(result) if memory_batch.enabled(context) else result
