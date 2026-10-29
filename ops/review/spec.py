"""AssessmentSpec 校验与字段路径读取。"""
import copy


ASSESSMENT_SPEC_VERSION = 1
SUPPORTED_TARGETS = frozenset({
    "explicit_fit", "boundary_respect", "continuity", "recall_relevance", "pacing_cost",
})


def validate_spec(spec):
    """返回错误列表；协议错误由调用方拒绝，避免看完数据后临时补默认项。"""
    if not isinstance(spec, dict):
        return ["AssessmentSpec 必须是 dict"]
    errors = []
    if spec.get("schema_version") != ASSESSMENT_SPEC_VERSION:
        errors.append("schema_version 非法")
    if not isinstance(spec.get("assessment_id"), str) or not spec["assessment_id"].strip():
        errors.append("assessment_id 必须是非空字符串")
    target_vector = spec.get("target_vector")
    if not isinstance(target_vector, list) or not target_vector:
        errors.append("target_vector 必须是非空 list")
    else:
        unknown = set(target_vector) - SUPPORTED_TARGETS
        if unknown:
            errors.append("不支持的 target_vector: " + ", ".join(sorted(unknown)))
    if not isinstance(spec.get("condition_keys", []), list):
        errors.append("condition_keys 必须是 list")
    if "source_params_sha" in spec and not isinstance(spec["source_params_sha"], list):
        errors.append("source_params_sha 必须是 list")
    if not isinstance(spec.get("eligibility", {}), dict):
        errors.append("eligibility 必须是 dict")
    elif ("allowed_params_sha" in spec.get("eligibility", {})
          and not isinstance(spec["eligibility"]["allowed_params_sha"], list)):
        errors.append("eligibility.allowed_params_sha 必须是 list")
    if not isinstance(spec.get("decision_policy", {}), dict):
        errors.append("decision_policy 必须是 dict")
    return errors


def require_valid_spec(spec):
    errors = validate_spec(spec)
    if errors:
        raise ValueError("AssessmentSpec 非法: " + "；".join(errors))
    return copy.deepcopy(spec)


def field_value(row, dotted_path):
    """只读取 dict 路径；字段缺失必须显式返回 unknown。"""
    value = row
    for segment in str(dotted_path).split("."):
        if not isinstance(value, dict) or segment not in value:
            return "__unknown__"
        value = value[segment]
    if value is None:
        return "__unknown__"
    if isinstance(value, (dict, list)):
        # 条件值需要稳定且不会被对象 identity 影响。
        import json
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return value
