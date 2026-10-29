"""P1.6 小样本表达盲评：匿名展示回复，条件映射与判读严格分离。"""
import hashlib
import json
import random


# 人格卡在盲评中必须隐藏，故不能让评审者判断“符合哪张卡”；只评可见的声线是否自洽。
DIMENSIONS = ("naturalness", "current_input_fit", "voice_coherence", "memory_expression")
ALLOWED_LABELS = {"pass", "fail", "unclear"}
SYNTHETIC_INPUT = "今天又路过那家咖啡店了。"


def _assessment_id(manifest):
    material = {
        "suite_id": manifest.get("suite_id"), "seed": manifest.get("seed"),
        "cells": [{"case_id": item.get("case_id"), "reply": item.get("reply")}
                  for item in manifest.get("cells", [])],
    }
    return "p16-" + hashlib.sha256(
        json.dumps(material, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def build_blind_artifacts(manifest, shuffle_seed=0):
    if not isinstance(manifest, dict) or manifest.get("suite_id") != "prompt-expression-p16":
        raise ValueError("只接受已完成的 P1.6 manifest")
    if manifest.get("status") != "completed" or len(manifest.get("cells") or []) != 5:
        raise ValueError("P1.6 manifest 必须是完整的五 cell 批次")
    assessment_id = _assessment_id(manifest)
    cells = list(manifest["cells"])
    if any(not str(cell.get("reply") or "").strip() for cell in cells):
        raise ValueError("P1.6 manifest 缺少回复")
    rng = random.Random(int(shuffle_seed))
    rng.shuffle(cells)
    units, mapping, labels = [], {}, []
    for index, cell in enumerate(cells, 1):
        blind_id = "blind-{:03d}".format(index)
        units.append({"blind_id": blind_id, "reply": str(cell["reply"])})
        mapping[blind_id] = {"case_id": str(cell["case_id"]),
                             "prompt_sha": str(cell.get("prompt_sha") or "")}
        labels.append({"blind_id": blind_id,
                       "dimensions": {dimension: "unclear" for dimension in DIMENSIONS},
                       "note": ""})
    return {
        "packet": {
            "schema_version": 1, "assessment_id": assessment_id,
            "blind_to_conditions": True, "synthetic_user_input": SYNTHETIC_INPUT,
            "rubric": list(DIMENSIONS), "units": units,
            "instruction": "只评回复本身；不要推断或填写隐藏条件。",
        },
        "private_mapping": {"schema_version": 1, "assessment_id": assessment_id,
                            "units": mapping},
        "label_template": {"schema_version": 1, "assessment_id": assessment_id,
                           "blind_to_conditions": True, "reviewer": "",
                           "labels": labels},
    }


def aggregate_labels(packet, private_mapping, labels):
    assessment_id = packet.get("assessment_id")
    if not assessment_id or private_mapping.get("assessment_id") != assessment_id:
        raise ValueError("packet 与 private mapping 不属于同一评审")
    if labels.get("assessment_id") != assessment_id or labels.get("blind_to_conditions") is not True:
        raise ValueError("标签必须声明在未知条件下完成")
    known = {unit["blind_id"] for unit in packet.get("units", [])}
    rows = labels.get("labels") or []
    if {row.get("blind_id") for row in rows} != known:
        raise ValueError("标签集必须与盲评单元一一对应")
    resolved = []
    for row in rows:
        dimensions = row.get("dimensions") or {}
        if set(dimensions) != set(DIMENSIONS) or not set(dimensions.values()) <= ALLOWED_LABELS:
            raise ValueError("标签维度或取值无效")
        resolved.append({"blind_id": row["blind_id"],
                         "case_id": private_mapping["units"][row["blind_id"]]["case_id"],
                         "dimensions": dimensions, "note": str(row.get("note") or "")})
    return {"schema_version": 1, "assessment_id": assessment_id, "status": "report_only",
            "decision": "no-decision", "reviewer": labels.get("reviewer") or "",
            "rows": resolved,
            "limitation": "每个隐藏条件只有一条回复；报告不构成统计或参数更新依据。"}
