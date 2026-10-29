"""模拟对话 A/B 的盲评包与只读条件化汇总。

本模块不 import Runner 或模型客户端。它只把 live run 的公开条件和 NPC 输出导出给评审，并使用
私有映射在标签返回后按条件/策略分桶；不计算单一奖励，不产生 Proposal，也不写参数。
"""
import copy
import hashlib
import json
import random
from pathlib import Path


DIMENSIONS = ("continuity", "recall_relevance", "boundary_respect",
              "interaction_burden", "style_fit")
LABEL_VALUES = {"pass", "fail", "unclear"}
PACKET_VERSION = 1


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _context_for(row):
    store_dir = row.get("store_dir")
    if not isinstance(store_dir, str) or not store_dir:
        raise ValueError("run row 缺少 store_dir")
    path = Path(store_dir) / "review_context.json"
    if not path.exists():
        raise ValueError("cell 缺少 review_context.json")
    context = _load_json(path)
    if not isinstance(context, dict) or context.get("schema_version") != 1:
        raise ValueError("review_context 不支持")
    return context


def _public_unit(row, action, context):
    scenario = context.get("public_scenario") or {}
    turn_id = action.get("turn_id")
    turns = {item.get("turn_id"): item for item in scenario.get("turns") or []
             if isinstance(item, dict)}
    turn = turns.get(turn_id)
    if not isinstance(turn, dict):
        # 此盲评协议只定义“公开用户回合 → NPC 回复”的成对样本。自治
        # continuation/replay 没有对应的公开当前输入，不能为凑样本伪造上下文，
        # 也不该因它们存在而让整个导出失败；应由带公开时间线的独立协议评审。
        return None
    condition = context.get("public_condition") or {}
    return {
        "condition": {
            "declared_b0": copy.deepcopy(condition.get("declared_weights")),
            "declared_boundaries": list(scenario.get("declared_boundaries") or []),
            "prior_context": [str(item.get("text") or "") for item in scenario.get("history") or []
                              if isinstance(item, dict)],
            "current_user_text": str(turn.get("text") or ""),
        },
        "npc_text": str(action.get("reply") or ""),
        "dimension_hints": list(DIMENSIONS),
        "_private": {
            "cell_id": row.get("cell_id"), "store_dir": row.get("store_dir"),
            "strategy": row.get("strategy"), "pair_key": row.get("pair_key"),
            "pair_seed": row.get("pair_seed"), "turn_id": turn_id,
        },
    }


def build_blind_artifacts(run_manifest, *, shuffle_seed=0):
    """构造公开 packet、私有 mapping 与空标签模板；策略字段绝不进入 packet。"""
    if not isinstance(run_manifest, dict) or not isinstance(run_manifest.get("rows"), list):
        raise ValueError("run manifest 缺少 rows")
    units = []
    for row in run_manifest["rows"]:
        if not isinstance(row, dict):
            continue
        for action in row.get("actions") or []:
            if isinstance(action, dict):
                unit = _public_unit(row, action, _context_for(row))
                if unit is not None:
                    units.append(unit)
    if not units:
        raise ValueError("run manifest 没有可盲评 action")
    random.Random(int(shuffle_seed)).shuffle(units)
    public, private, labels = [], {}, []
    for index, unit in enumerate(units, start=1):
        blind_id = "blind-%03d" % index
        public.append({"blind_id": blind_id, "condition": unit["condition"],
                       "npc_text": unit["npc_text"], "dimensions": list(DIMENSIONS)})
        private[blind_id] = unit["_private"]
        labels.append({"blind_id": blind_id,
                       "dimensions": {dimension: None for dimension in DIMENSIONS},
                       "comment": ""})
    assessment_id = "sim-dialogue-" + hashlib.sha256(
        _canonical(public).encode("utf-8")).hexdigest()[:12]
    return {
        "packet": {"packet_version": PACKET_VERSION, "assessment_id": assessment_id,
                   "blind_to_policy": True, "units": public},
        "private_mapping": {"packet_version": PACKET_VERSION, "assessment_id": assessment_id,
                            "units": private},
        "label_template": {"packet_version": PACKET_VERSION, "assessment_id": assessment_id,
                           "reviewer": "", "blind_to_policy": True, "labels": labels},
    }


def _validate_labels(packet, mapping, labels):
    if not isinstance(packet, dict) or packet.get("packet_version") != PACKET_VERSION:
        raise ValueError("packet 不支持")
    if not isinstance(mapping, dict) or mapping.get("packet_version") != PACKET_VERSION:
        raise ValueError("private mapping 不支持")
    if mapping.get("assessment_id") != packet.get("assessment_id"):
        raise ValueError("private mapping assessment_id 不匹配")
    if not isinstance(labels, dict) or labels.get("assessment_id") != packet.get("assessment_id"):
        raise ValueError("labels assessment_id 不匹配")
    if labels.get("blind_to_policy") is not True:
        raise ValueError("labels 必须明确 blind_to_policy=true")
    if not str(labels.get("reviewer") or "").strip():
        raise ValueError("labels 必须提供 reviewer")
    mapping_units = mapping.get("units")
    packet_rows = packet.get("units")
    if not isinstance(mapping_units, dict) or not isinstance(packet_rows, list):
        raise ValueError("packet 或 private mapping 缺少 units")
    packet_ids = [unit.get("blind_id") for unit in packet_rows if isinstance(unit, dict)]
    if len(packet_ids) != len(packet_rows) or len(set(packet_ids)) != len(packet_ids):
        raise ValueError("packet blind_id 无效或重复")
    if set(packet_ids) != set(mapping_units):
        raise ValueError("packet 与 private mapping 的 blind_id 不一致")
    rows = labels.get("labels")
    if not isinstance(rows, list):
        raise ValueError("labels 必须包含 labels 数组")
    seen, accepted = set(), []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("label 必须是对象")
        blind_id = row.get("blind_id")
        if not isinstance(blind_id, str) or blind_id not in mapping_units or blind_id in seen:
            raise ValueError("label blind_id 无效或重复")
        dims = row.get("dimensions")
        if not isinstance(dims, dict) or set(dims) != set(DIMENSIONS):
            raise ValueError("label dimensions 必须完整")
        if any(value not in LABEL_VALUES for value in dims.values()):
            raise ValueError("label 维度值必须为 pass/fail/unclear")
        seen.add(blind_id)
        accepted.append((blind_id, dims))
    return accepted


def aggregate_blind_labels(packet, private_mapping, labels):
    """按公开条件和私有策略只读分桶；永远不输出策略胜负或参数建议。"""
    accepted = _validate_labels(packet, private_mapping, labels)
    packet_units = {unit["blind_id"]: unit for unit in packet.get("units") or []}
    groups = {}
    for blind_id, dims in accepted:
        public = packet_units[blind_id]
        private = private_mapping["units"][blind_id]
        condition = public["condition"]
        group_key = _canonical({
            "scenario_id": private.get("pair_key", "").split(":", 1)[-1],
            "declared_b0": condition.get("declared_b0"),
            "declared_boundaries": condition.get("declared_boundaries"),
            "current_user_text": condition.get("current_user_text"),
        })
        bucket = groups.setdefault(group_key, {
            "condition": copy.deepcopy(condition), "strategies": {},
        })
        stats = bucket["strategies"].setdefault(str(private.get("strategy")), {
            "n": 0, "dimensions": {dimension: {"pass": 0, "fail": 0, "unclear": 0}
                                      for dimension in DIMENSIONS},
        })
        stats["n"] += 1
        for dimension, value in dims.items():
            stats["dimensions"][dimension][value] += 1
    return {
        "report_version": PACKET_VERSION,
        "assessment_id": packet.get("assessment_id"),
        "reviewer": labels.get("reviewer"),
        "status": "report_only",
        "decision": "no-decision",
        "reason": "模拟对话盲评仅形成条件化统计；不得自动判胜或参数回流。",
        "groups": list(groups.values()),
        "labelled_units": len(accepted),
    }
