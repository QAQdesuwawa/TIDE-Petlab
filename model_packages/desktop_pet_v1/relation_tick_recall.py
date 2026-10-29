"""Receipt-bound root-view adapter, using the original tick recall formula."""
from framework.model_api import InteractionModelManifest,thaw_model_data
from .tick_recall import DesktopPetTickRecallPackage
from .relation_package import reference_tick_payload,VERSION
class MultiPetTickRecallPackage(DesktopPetTickRecallPackage):
 def __init__(self,*,parameter_package):
  params=thaw_model_data(parameter_package);binding=params.pop('directed_relation_binding')
  # These are registered wrapper definitions, not parameters of CX Full.
  # Validate and restore them on this manifest rather than weakening Full's
  # exact field check or silently discarding an unknown extension.
  from .relation_disclosure import VERSION as DISCLOSURE
  from .information_holder import FLAG,VERSION as HOLDER
  extensions={}
  for key,expected in (('public_disclosure_scope_version',DISCLOSURE),(FLAG,HOLDER)):
   if key in params:
    value=params.pop(key)
    if value!=expected:raise ValueError('unknown directed tick parameter extension')
    extensions[key]=value
  super().__init__(parameter_package=params)
  old=self.manifest;operators=dict(old.operator_versions);operators['recall_history']=VERSION+'+'+operators['recall_history']
  fields={k:getattr(old,k) for k in ('api_version','capability_profile','capabilities','constants_schema','parameters_schema','state_schema','measurement_schema','shared_projection_schema','progress_delta_schema')}
  self.manifest=InteractionModelManifest.create(**fields,package_id='tide.desktop-pet-multi-tick-memory',package_version='0.1.0-directed' if not extensions else '0.2.0-registered-extensions',parameter_package={**params,**extensions,'directed_relation_binding':binding},operator_versions=operators)
 def recall_history(self,*,payload,context):
  normalized,source=reference_tick_payload(payload)
  result=super().recall_history(payload=normalized,context=context)
  return result if source is None else {**result,'directed_query_source':source}
