"""Explicit production composition of the pet, reference parameters and time."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass,replace
import time

from framework.model_api import freeze_model_payload,model_data_fingerprint,thaw_model_data
from framework.runtime_variant import RuntimeVariantDefinition
from model_packages.desktop_pet_v1 import DesktopPetPackage
from model_packages.cx_six_seven_v1.reference_parameters import reference_full_parameter_package
from ops.runtime_package_composition import compose_runtime_packages
from ops.runtime_session_bootstrap import bootstrap_runtime_session
from ops.runtime_expression_state import restore_runtime_expression_state
from product_adapters.cx_six_seven_v1.realizer_packet import WRITER_LANE_ORDER
from temporal_packages.wall_clock_ms_v1 import WallClockMillisecondsV1
from .session import SinglePetApplication
from .storage import SessionFile,RUNTIME_IDENTITY

DEFINITION_KEY="desktop_pet_definition_v1"


@dataclass(frozen=True,kw_only=True)
class PetDefinition:
    session_id:str
    owner_ref:str
    user_ref:str
    scope_ref:str
    name:str
    background:str
    base_constants:Mapping
    base_constants_version:str
    b0_binding:Mapping
    initial_state:Mapping|None=None

    def __post_init__(self):
        for name in ("session_id","owner_ref","user_ref","scope_ref","name","base_constants_version"):
            value=getattr(self,name)
            if not isinstance(value,str) or not value.strip() or value!=value.strip():
                raise ValueError("pet definition requires exact "+name)
        if self.owner_ref==self.user_ref:raise ValueError("pet and user identities must differ")
        if not isinstance(self.background,str):raise ValueError("pet background must be text")
        for name in ("base_constants","b0_binding"):
            value=getattr(self,name)
            if not isinstance(value,Mapping):raise ValueError("pet definition requires "+name)
            object.__setattr__(self,name,freeze_model_payload(value))
        if self.initial_state is not None:
            if not isinstance(self.initial_state,Mapping):raise ValueError("initial state must be a mapping")
            object.__setattr__(self,"initial_state",freeze_model_payload(self.initial_state))

    def to_record(self):
        record={"version":DEFINITION_KEY}
        for name in self.__dataclass_fields__:
            value=getattr(self,name)
            record[name]=thaw_model_data(value) if isinstance(value,Mapping) else value
        return record

    @property
    def fingerprint(self):return model_data_fingerprint(self.to_record())

    @classmethod
    def from_record(cls,record):
        if not isinstance(record,Mapping) or record.get("version")!=DEFINITION_KEY:
            raise ValueError("pet definition version is invalid")
        if set(record)!={"version",*cls.__dataclass_fields__}:
            raise ValueError("pet definition fields are invalid")
        return cls(**{key:record[key] for key in cls.__dataclass_fields__})


def _compose_pet_packages(session_id,*,model_package=None):
    package=DesktopPetPackage(parameter_package=reference_full_parameter_package()) if model_package is None else model_package
    variant=RuntimeVariantDefinition.create(implementation_id=RUNTIME_IDENTITY,
        kernel_implementation_id="tide.runtime-kernel.v1",
        mechanism_implementations={"persistent_state_authority":"runtime-state-commit-authority-v1",
            "commit_gate":"canonical-action-commit-gate-v1","pet_event_ingress":"desktop-pet-event-v1"},
        realizer_profile_id="desktop-pet-readonly-expression-v1",
        resource_policy={lane:"enabled" for lane in WRITER_LANE_ORDER})
    composition=compose_runtime_packages(model_package=package,temporal_package=WallClockMillisecondsV1(),
        session_id=session_id,variant_definition=variant)
    return composition


def background_creation_context(*,session_id,coordinate):
    """Bind a creation request without bootstrapping state or persisting history."""
    from framework.runtime_kernel import RuntimeSessionBinding,RuntimeProgressAuthority
    composition=_compose_pet_packages(session_id)
    binding=RuntimeSessionBinding.create(model_runtime=composition.model_runtime,
        temporal_runtime=composition.temporal_runtime,runtime_variant=composition.variant_binding)
    progress=RuntimeProgressAuthority(session_binding=binding,temporal_runtime=composition.temporal_runtime)
    return binding,progress.initialize(coordinate)


def definition_from_background(seed,measured):
    """Adopt measured creation values only in a caller-selected new definition."""
    if not isinstance(seed,PetDefinition) or measured.get("version")!="desktop-pet-background-creation-v1":raise ValueError("explicit seed and measured background required")
    receipt=measured["receipt"]
    expected={k:v for k,v in receipt.items() if k!="fingerprint"}
    if model_data_fingerprint(expected)!=receipt["fingerprint"]:raise ValueError("background receipt differs")
    profile=receipt["parsed_profile"]
    if any(profile[k]!=measured[k] for k in ("persona_card","weights","base_constants","measured_model")):
        raise ValueError("measured profile differs from its receipt")
    if measured["base_constants_version"]!="pet-background:"+receipt["fingerprint"]:raise ValueError("background constant version differs")
    return replace(seed,background=measured["persona_card"],base_constants=measured["base_constants"],
        base_constants_version=measured["base_constants_version"])


def open_application(*,definition,session_path,action_verifier,initial_coordinate=None,model_package=None):
    if not isinstance(definition,PetDefinition):raise TypeError("explicit pet definition is required")
    composition=_compose_pet_packages(definition.session_id,model_package=model_package)
    store=SessionFile(session_path)
    kwargs={"store":store,"model_runtime":composition.model_runtime,"temporal_runtime":composition.temporal_runtime,
        "runtime_variant":composition.variant_binding,"owner_ref":definition.owner_ref,"user_ref":definition.user_ref,
        "action_verifier":action_verifier,"memory_query_budget_ms":500}
    try:
        if store.path.exists():
            app=SinglePetApplication.restore(**kwargs)
            selected=app.runtime.session.profile.get(DEFINITION_KEY)
            if selected is None or model_data_fingerprint(selected)!=definition.fingerprint:
                raise ValueError("restored pet definition differs from selected configuration")
        else:
            session=bootstrap_runtime_session(profile={DEFINITION_KEY:definition.to_record()},
                model_runtime=composition.model_runtime,temporal_runtime=composition.temporal_runtime,
                runtime_variant=composition.variant_binding,
                initial_coordinate=(time.time_ns()//1_000_000 if initial_coordinate is None else initial_coordinate),
                parameter_payload={"base_constants":definition.base_constants,
                    "base_constants_version":definition.base_constants_version,"b0_binding":definition.b0_binding,
                    "correction":None},state_payload={"initial_state":definition.initial_state})
            app=SinglePetApplication.create(session=session,**kwargs)
        restore_runtime_expression_state(app.runtime.session.profile,session_binding=app.runtime.session.session_binding)
        app.definition=definition
        return app
    except Exception:
        store.close()
        raise


def validate_definition(definition):
    """Check a selected configuration using the original pure initializers."""
    from framework.model_api.invocation_v2 import ModelInvocationContextV2
    if not isinstance(definition,PetDefinition):raise TypeError("explicit pet definition is required")
    package=DesktopPetPackage(parameter_package=reference_full_parameter_package())
    context=ModelInvocationContextV2(session_id=definition.session_id,scope_ref=definition.scope_ref,
        subject_ref=definition.owner_ref,event_ref=None,action_ref=None,
        temporal_position=WallClockMillisecondsV1().normalize_position(coordinate=0),runtime_ordinal=0)
    package.initialize_parameters(payload={"base_constants":definition.base_constants,
        "base_constants_version":definition.base_constants_version,"b0_binding":definition.b0_binding,
        "correction":None},context=context)
    package.initialize_state(payload={"initial_state":definition.initial_state},context=context)
