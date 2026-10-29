"""Keep frozen bindings; new formal actors use the original information-holder definition."""
import json
from pathlib import Path
from model_packages.desktop_pet_v1.relation_package import MultiPetPackage
from model_packages.desktop_pet_v1.relation_disclosure import DisclosureAwareMultiPetPackage
from model_packages.desktop_pet_v1.information_holder import HolderMultiPetPackage,PACKAGE_VERSION
from model_packages.cx_six_seven_v1.reference_parameters import reference_full_parameter_package


def select(*,session_path,owner_ref,human_ref):
 path=Path(session_path);cls=HolderMultiPetPackage
 if path.exists():
  from .storage import _disk_decode
  # Select by the immutable base binding; formal restore verifies all evidence.
  checkpoint=_disk_decode(json.loads(path.read_text('utf8')))
  version=checkpoint['profile']['runtime_session_binding']['binding']['model_binding']['package_version']
  if version=='0.1.0-directed-state':cls=MultiPetPackage
  elif version=='0.2.0-public-disclosure':cls=DisclosureAwareMultiPetPackage
  elif version!=PACKAGE_VERSION:raise ValueError('unsupported frozen multi package version')
 return cls(parameter_package=reference_full_parameter_package(),owner_ref=owner_ref,human_ref=human_ref)
