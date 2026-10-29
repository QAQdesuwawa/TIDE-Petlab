"""自由长线对话的盲评包与只读汇总。

这一模块只消费已完成的 ``free-dialogue-v1`` manifest。公开包仅提供完整对话和评审维度；
语义投影开关、来源路径及分支标识只存在于私有映射。评审结果始终是报告，不回写产品参数。
"""
import copy
import hashlib
import json
import random


DIMENSIONS = (
    "current_input_fit",
    "memory_naturalness",
    "topic_shift_respect",
    "continuity",
)
LABEL_VALUES = {"pass", "fail", "unclear"}
PACKET_VERSION = 1


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _public_transcript(manifest):
    transcript = manifest.get("transcript")
    if not isinstance(transcript, list) or not transcript:
        raise ValueError("free dialogue manifest 缺少 transcript")
    public = []
    for item in transcript:
        if not isinstance(item, dict) or item.get("role") not in {"user", "npc"}:
            raise ValueError("transcript 角色无效")
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("transcript 文本无效")
        public.append({"role": item["role"], "text": text})
    return public


def _unit(manifest, source_label):
    if not isinstance(manifest, dict) or manifest.get("suite") != "free-dialogue-v1":
        raise ValueError("不支持的 free dialogue manifest")
    if manifest.get("status") != "completed":
        raise ValueError("只允许导出已完成的 free dialogue manifest")
    preflight = manifest.get("preflight")
    context = manifest.get("context")
    if not isinstance(preflight, dict) or not isinstance(context, dict):
        raise ValueError("free dialogue manifest 缺少上下文")
    semantic = preflight.get("semantic_projection")
    if not isinstance(semantic, dict) or not isinstance(semantic.get("enabled"), bool):
        raise ValueError("free dialogue manifest 缺少语义投影声明")
    branch_id = context.get("branch_id")
    if not isinstance(branch_id, str) or not branch_id:
        raise ValueError("free dialogue manifest 缺少 branch_id")
    return {
        "transcript": _public_transcript(manifest),
        "_private": {
            "branch_id": branch_id,
            "semantic_projection_enabled": semantic["enabled"],
            "source_label": str(source_label),
        },
    }


def build_blind_artifacts(manifests, *, shuffle_seed=0):
    """构造公开对话包、私有映射和标签模板，不修改输入 manifest。"""
    if not isinstance(manifests, list) or not manifests:
        raise ValueError("至少需要一份 manifest")
    units = []
    for row in manifests:
        if not isinstance(row, dict) or "manifest" not in row:
            raise ValueError("manifest 行无效")
        units.append(_unit(row["manifest"], row.get("source_label", "")))
    random.Random(int(shuffle_seed)).shuffle(units)

    public, private, labels = [], {}, []
    for index, unit in enumerate(units, start=1):
        blind_id = "blind-%03d" % index
        public.append({
            "blind_id": blind_id,
            "transcript": copy.deepcopy(unit["transcript"]),
            "dimensions": list(DIMENSIONS),
        })
        private[blind_id] = unit["_private"]
        labels.append({
            "blind_id": blind_id,
            "dimensions": {dimension: None for dimension in DIMENSIONS},
            "comment": "",
        })
    assessment_id = "free-dialogue-" + hashlib.sha256(
        _canonical(public).encode("utf-8")).hexdigest()[:12]
    return {
        "packet": {
            "packet_version": PACKET_VERSION,
            "assessment_id": assessment_id,
            "blind_to_policy": True,
            "units": public,
        },
        "private_mapping": {
            "packet_version": PACKET_VERSION,
            "assessment_id": assessment_id,
            "units": private,
        },
        "label_template": {
            "packet_version": PACKET_VERSION,
            "assessment_id": assessment_id,
            "reviewer": "",
            "blind_to_policy": True,
            "labels": labels,
        },
    }


def _validated_labels(packet, mapping, labels):
    if packet.get("packet_version") != PACKET_VERSION or mapping.get("packet_version") != PACKET_VERSION:
        raise ValueError("packet 或 private mapping 不支持")
    if packet.get("assessment_id") != mapping.get("assessment_id"):
        raise ValueError("packet 与 private mapping assessment_id 不匹配")
    if labels.get("assessment_id") != packet.get("assessment_id"):
        raise ValueError("labels assessment_id 不匹配")
    if labels.get("blind_to_policy") is not True or not str(labels.get("reviewer") or "").strip():
        raise ValueError("labels 必须声明盲评并提供 reviewer")
    packet_ids = [row.get("blind_id") for row in packet.get("units") or [] if isinstance(row, dict)]
    if not packet_ids or len(packet_ids) != len(set(packet_ids)) or set(packet_ids) != set(mapping.get("units") or {}):
        raise ValueError("packet 与 private mapping 的 blind_id 无效")
    accepted, seen = [], set()
    for row in labels.get("labels") or []:
        blind_id = row.get("blind_id") if isinstance(row, dict) else None
        dimensions = row.get("dimensions") if isinstance(row, dict) else None
        if blind_id not in mapping["units"] or blind_id in seen:
            raise ValueError("label blind_id 无效或重复")
        if not isinstance(dimensions, dict) or set(dimensions) != set(DIMENSIONS):
            raise ValueError("label dimensions 必须完整")
        if any(value not in LABEL_VALUES for value in dimensions.values()):
            raise ValueError("label 值必须为 pass/fail/unclear")
        seen.add(blind_id)
        accepted.append((blind_id, dimensions))
    return accepted


def aggregate_blind_labels(packet, private_mapping, labels):
    """按分支与投影开关只读汇总，永远不给出自动胜负或参数建议。"""
    accepted = _validated_labels(packet, private_mapping, labels)
    groups = {}
    for blind_id, dimensions in accepted:
        private = private_mapping["units"][blind_id]
        branch = private["branch_id"]
        strategy = "semantic_on" if private["semantic_projection_enabled"] else "semantic_off"
        bucket = groups.setdefault(branch, {"branch_id": branch, "strategies": {}})
        stats = bucket["strategies"].setdefault(strategy, {
            "n": 0,
            "dimensions": {dimension: {value: 0 for value in sorted(LABEL_VALUES)}
                           for dimension in DIMENSIONS},
        })
        stats["n"] += 1
        for dimension, value in dimensions.items():
            stats["dimensions"][dimension][value] += 1
    return {
        "report_version": PACKET_VERSION,
        "assessment_id": packet["assessment_id"],
        "reviewer": labels["reviewer"],
        "status": "report_only",
        "decision": "no-decision",
        "reason": "单批自由对话盲评仅形成条件化统计；不得自动判胜或回写参数。",
        "groups": list(groups.values()),
        "labelled_units": len(accepted),
    }
