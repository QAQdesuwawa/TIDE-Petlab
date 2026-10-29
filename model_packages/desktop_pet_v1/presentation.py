"""Package-owned references for typed sources and already-selected outputs."""
from collections.abc import Mapping
import json
from pathlib import Path
from framework.model_api import model_data_fingerprint
from .events import Content, EVENT_SCHEMA, COMPOSITE_EVENT_SCHEMA

LEGACY_VERSION="desktop-pet-presentation-subtypes-v1"
VERSION="desktop-pet-presentation-subtypes-v2"
SUMMARY_VERSION="desktop-pet-presentation-subtypes-v3-text"
VISIBLE_VERSION="desktop-pet-presentation-subtypes-v4-visible"
RESOURCE_PATH=Path(__file__).with_name("resources")/"presentation-v2.json"


def build_guidance(*,source_contents,selected_output_kinds,resource_path=None):
    resource=json.loads(Path(resource_path or RESOURCE_PATH).read_text(encoding="utf-8"))
    if (not isinstance(resource,dict) or set(resource)!={"version","common","subtypes"}
            or resource["version"] not in {LEGACY_VERSION,VERSION,SUMMARY_VERSION,VISIBLE_VERSION} or not isinstance(resource["common"],str) or not resource["common"].strip()
            or not isinstance(resource["subtypes"],dict)
            or set(resource["subtypes"])!=set((COMPOSITE_EVENT_SCHEMA if resource["version"]==VISIBLE_VERSION else EVENT_SCHEMA)["content_types"])):
        raise ValueError("presentation subtype registry differs from event definition")
    definitions=resource["subtypes"]
    for kind,row in definitions.items():
        if (not isinstance(row,dict) or set(row)!={"source_reference","output_reference"}
                or not isinstance(row["source_reference"],str) or not row["source_reference"].strip()
                or (kind=="interaction" and row["output_reference"] is not None)
                or (kind!="interaction" and (not isinstance(row["output_reference"],str) or not row["output_reference"].strip()))):
            raise ValueError("presentation subtype reference is incomplete")
        if not RESOURCE_PATH.parent.joinpath("semantic",kind+".md").is_file():
            raise ValueError("presentation subtype lacks semantic measurement reference")
    if not isinstance(source_contents,(tuple,list)) or any(not isinstance(item,Content) for item in source_contents):
        raise ValueError("presentation sources require typed contents")
    if (not isinstance(selected_output_kinds,(tuple,list)) or not selected_output_kinds
            or any(not isinstance(kind,str) or kind not in definitions or definitions[kind]["output_reference"] is None
                   for kind in selected_output_kinds) or len(set(selected_output_kinds))!=len(selected_output_kinds)):
        raise ValueError("selected output subtypes are invalid")
    sources=list(dict.fromkeys(item.kind for item in source_contents))
    if any(kind not in definitions for kind in sources):raise ValueError("unregistered source subtype")
    value={"version":resource["version"],"source_kinds":sources,"selected_output_kinds":list(selected_output_kinds),
           "common":resource["common"],"source_references":{kind:definitions[kind]["source_reference"] for kind in sources},
           "output_references":{kind:definitions[kind]["output_reference"] for kind in selected_output_kinds}}
    value["fingerprint"]=model_data_fingerprint(value)
    return value


def render_guidance(value):
    if not isinstance(value,Mapping):raise ValueError("presentation reference requires a record")
    from framework.model_api import thaw_model_data
    record=thaw_model_data(value)
    fingerprint=record.pop("fingerprint",None)
    if record.get("version") not in {LEGACY_VERSION,VERSION,SUMMARY_VERSION,VISIBLE_VERSION} or model_data_fingerprint(record)!=fingerprint:
        raise ValueError("presentation reference integrity differs")
    parts=["【本轮子类表达参照】",record["common"]]
    for lane in ("source_references","output_references"):
        parts.extend(record[lane].values())
    return "\n".join(parts)
