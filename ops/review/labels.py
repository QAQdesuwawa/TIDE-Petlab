"""独立盲评标签合同与 EvidenceRow 附着。

标签不解释文本、不定义正负，也不由生成器自评；它只验证来源、盲态声明和行动引用，再把
原始 dimension/value 附着给离线评审。
"""
import copy
import os


LABEL_SET_VERSION = 1
INDEPENDENT_DIMENSIONS = frozenset({"boundary_respect", "continuity", "recall_relevance"})
EVALUATOR_KINDS = frozenset({"human", "independent_model"})


def _normal_path(value):
    return os.path.abspath(str(value)) if value else None


def validate_label_set(label_set, assessment_id, target_vector):
    """返回合同错误；调用方必须拒绝不合格标签，而非静默丢弃。"""
    if not isinstance(label_set, dict):
        return ["LabelSet 必须是 dict"]
    errors = []
    if label_set.get("schema_version") != LABEL_SET_VERSION:
        errors.append("schema_version 非法")
    if label_set.get("assessment_id") != assessment_id:
        errors.append("assessment_id 不一致")
    if not str(label_set.get("label_set_id", "")).strip():
        errors.append("label_set_id 必须非空")
    evaluator = label_set.get("evaluator") or {}
    if evaluator.get("kind") not in EVALUATOR_KINDS:
        errors.append("evaluator.kind 必须是 human 或 independent_model")
    if not str(evaluator.get("id", "")).strip():
        errors.append("evaluator.id 必须非空")
    if evaluator.get("blind_to_policy") is not True:
        errors.append("evaluator 必须声明 blind_to_policy=true")
    labels = label_set.get("labels")
    if not isinstance(labels, list):
        errors.append("labels 必须是 list")
        return errors
    allowed = set(target_vector or []) & INDEPENDENT_DIMENSIONS
    for index, item in enumerate(labels):
        if not isinstance(item, dict):
            errors.append(f"labels[{index}] 必须是 dict")
            continue
        has_action = bool(str(item.get("action_id", "")).strip())
        has_blind = bool(str(item.get("blind_id", "")).strip())
        if has_action == has_blind:
            errors.append(f"labels[{index}] 必须且只能提供 action_id 或 blind_id")
        if item.get("dimension") not in allowed:
            errors.append(f"labels[{index}].dimension 不在 AssessmentSpec 独立目标中")
        if not str(item.get("value", "")).strip():
            errors.append(f"labels[{index}].value 必须非空")
    return errors


def attach_independent_labels(evidence_rows, label_sets, assessment_id, target_vector,
                              blind_mapping=None):
    """将一个或多个合格 LabelSet 附着到唯一 EvidenceRow，返回新 rows。

    同 action_id 出现在多个 store 时，标签必须给出 ``store_dir``；没有该字段且不能唯一
    匹配时拒绝，避免跨用户/策略串样本。
    """
    rows = copy.deepcopy([row for row in (evidence_rows or []) if isinstance(row, dict)])
    by_action = {}
    by_store_action = {}
    for index, row in enumerate(rows):
        action_id = str(row.get("action_id", ""))
        store_dir = _normal_path((row.get("source_refs") or {}).get("store_dir"))
        by_action.setdefault(action_id, []).append(index)
        if store_dir:
            by_store_action[(store_dir, action_id)] = index
        observations = row.setdefault("observations", {})
        observations["independent_labels"] = list(observations.get("independent_labels") or [])

    blind_index = {}
    if blind_mapping is not None:
        if blind_mapping.get("assessment_id") != assessment_id:
            raise ValueError("blind mapping assessment_id 不一致")
        for item in blind_mapping.get("items") or []:
            blind_id = str(item.get("blind_id", ""))
            if not blind_id or blind_id in blind_index:
                raise ValueError("blind mapping 含无效或重复 blind_id")
            blind_index[blind_id] = dict(item)

    for label_set in label_sets or []:
        errors = validate_label_set(label_set, assessment_id, target_vector)
        if errors:
            raise ValueError("LabelSet 非法: " + "；".join(errors))
        evaluator = copy.deepcopy(label_set["evaluator"])
        for label_index, label in enumerate(label_set["labels"]):
            if label.get("blind_id"):
                resolved = blind_index.get(str(label["blind_id"]))
                if resolved is None:
                    raise ValueError(f"LabelSet 引用未知 blind_id: {label['blind_id']}")
                action_id = str(resolved.get("action_id", ""))
                store_dir = _normal_path(resolved.get("store_dir"))
            else:
                action_id = str(label["action_id"])
                store_dir = _normal_path(label.get("store_dir"))
            if store_dir:
                row_index = by_store_action.get((store_dir, action_id))
                if row_index is None:
                    raise ValueError(f"LabelSet 引用未知 action/store: {action_id}")
            else:
                matches = by_action.get(action_id, [])
                if len(matches) != 1:
                    raise ValueError(f"LabelSet action_id 无法唯一匹配: {action_id}")
                row_index = matches[0]
            rows[row_index]["observations"]["independent_labels"].append({
                "dimension": str(label["dimension"]),
                "value": str(label["value"]),
                "label_set_id": str(label_set["label_set_id"]),
                "label_index": label_index,
                "evaluator": evaluator,
            })
    return rows
