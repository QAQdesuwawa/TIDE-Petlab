"""六七参考模型表达资源的正式发放计划。

本模块只消费已经发布的词元描述符与已提交余量。它既不读取七常量、六轴状态、
墙钟、P3/F15/F16，也不接受旧 F07 的 ``seen_counts`` 或 ``potential``。因此，
同一份描述符在同一余量锚下必然得到同一份计划；真正的余量变更只能由调用方在
F16 已提交之后，使用实际交付的 token key 生成 receipt 并持久化。
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping


MAPPING_DESCRIPTOR_VERSION = "expression-mapping-descriptor-v1"
EMISSION_PLAN_VERSION = "expression-emission-plan-v1"
EMISSION_COMMIT_VERSION = "expression-emission-commit-v1"
EMISSION_QUANTIZATION_VERSION = "expression-emission-quantization-v2"
# 表现额度的统一离散刻度。它不是 P3/F15 门线，也不是角色或用户状态；所有
# source×axis 包均用同一值。未来若引入人工审核的表现参数 policy，只能在调用
# 本纯计划前显式发布同版值，不能在此处读取 B0、C_effective 或在线行为。
DEFAULT_EMISSION_UNIT = 0.75
_EPSILON = 1e-12


def _finite_number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _descriptor_identity(candidate: Mapping[str, object]) -> tuple[str, str, str] | None:
    token_key = str(candidate.get("token_key") or "").strip()
    source = str(candidate.get("source") or "").strip()
    axis = str(candidate.get("axis") or "").strip()
    if not token_key or not source or not axis:
        return None
    return token_key, source, axis


def _validated_descriptor(candidate: object) -> dict[str, object] | None:
    """复制并严格校验正式 descriptor；缺字段时空退而不猜测。"""
    if not isinstance(candidate, Mapping):
        return None
    if str(candidate.get("version") or "") != MAPPING_DESCRIPTOR_VERSION:
        return None
    identity = _descriptor_identity(candidate)
    domain = candidate.get("domain")
    receipt = candidate.get("input_receipt")
    score = _finite_number(candidate.get("score"))
    neutral = _finite_number(candidate.get("neutral"))
    if (identity is None or not isinstance(domain, Mapping) or not isinstance(receipt, Mapping)
            or score is None or neutral is None):
        return None
    low = _finite_number(domain.get("low"))
    high = _finite_number(domain.get("high"))
    if low is None or high is None or not low < high:
        return None
    if not low <= neutral <= high or not low <= score <= high:
        return None
    # 中性点可位于端点：单向的“变化幅度”词元以 ``0`` 为中性点，只会走正向
    # 分支；若实际落到另一侧，则分母为零而按无效 descriptor 空退。
    if neutral < low or neutral > high:
        return None
    token_key, source, axis = identity
    return {
        "version": MAPPING_DESCRIPTOR_VERSION,
        "token_key": token_key,
        "source": source,
        "axis": axis,
        "score": score,
        "domain": {"low": low, "high": high},
        "neutral": neutral,
        "input_receipt": dict(receipt),
    }


def normalize_mapping_offset(candidate: Mapping[str, object]) -> float | None:
    """将单张词元的 *自身* 分值相对自身中性点映射到 ``[-1, 1]``。

    这不是第二套状态/变化量计算：``score``、定义域和 ``neutral`` 已由合法词元
    映射器发布。该发放器只做可逆的坐标归一化，避免重读根量或重新解释来源。
    """
    validated = _validated_descriptor(candidate)
    if validated is None:
        return None
    score = float(validated["score"])
    neutral = float(validated["neutral"])
    domain = validated["domain"]
    assert isinstance(domain, Mapping)
    low = float(domain["low"])
    high = float(domain["high"])
    if math.isclose(score, neutral, rel_tol=0.0, abs_tol=_EPSILON):
        return 0.0
    if score > neutral:
        if high - neutral <= _EPSILON:
            return None
        return round((score - neutral) / (high - neutral), 12)
    if neutral - low <= _EPSILON:
        return None
    return round((score - neutral) / (neutral - low), 12)


def _package_key(source: str, axis: str) -> str:
    return f"{source}::{axis}"


def _read_remainder(remainders: Mapping[str, object] | None, package_key: str) -> float:
    if not isinstance(remainders, Mapping):
        return 0.0
    value = _finite_number(remainders.get(package_key))
    # 已提交余量可以大于一个单元（例如当轮候选不足），但永不允许负数或非有限值。
    return value if value is not None and value >= 0.0 else 0.0


def _round(value: float) -> float:
    return round(value, 12)


def compile_expression_emission_plan(
        candidates: Iterable[object], *, committed_remainders: Mapping[str, object] | None = None,
        emission_unit: float = DEFAULT_EMISSION_UNIT) -> dict[str, object]:
    """从本轮 descriptor 编排纯发放计划。

    每个 ``source × axis`` 包独立累计本轮绝对偏移与上一轮已提交余量；余量只做
    连续配额到离散 slot 的量化，绝不凭空发词元。每个 action 内 ``token_key`` 全局
    唯一，冲突 key 会让后一个包尝试自己的下一张候选，而不是重复注入同一材料。
    """
    unit = _finite_number(emission_unit)
    if unit is None or unit <= 0.0:
        raise ValueError("emission_unit must be a finite positive number")

    rejected: list[dict[str, str]] = []
    grouped: dict[tuple[str, str], list[dict[str, object]]] = {}
    for raw in candidates:
        validated = _validated_descriptor(raw)
        if validated is None:
            token_key = ""
            if isinstance(raw, Mapping):
                token_key = str(raw.get("token_key") or "").strip()
            rejected.append({
                "token_key": token_key or "<unknown>",
                "reason": "invalid_candidate_descriptor",
            })
            continue
        offset = normalize_mapping_offset(validated)
        assert offset is not None
        validated["current_offset"] = offset
        package = (str(validated["source"]), str(validated["axis"]))
        grouped.setdefault(package, []).append(validated)

    selected_token_keys: list[str] = []
    selected_keys: set[str] = set()
    packages: list[dict[str, object]] = []
    candidate_receipts: list[dict[str, object]] = []
    for source, axis in sorted(grouped):
        rows = grouped[(source, axis)]
        rows.sort(key=lambda row: (-abs(float(row["current_offset"])), str(row["token_key"])))
        key = _package_key(source, axis)
        prior_remainder = _read_remainder(committed_remainders, key)
        offset_total = sum(abs(float(row["current_offset"])) for row in rows)
        continuous_quota = prior_remainder + offset_total
        requested_slots = int(math.floor((continuous_quota + _EPSILON) / unit))
        emitted: list[str] = []
        deferred: list[dict[str, str]] = []

        for row in rows:
            token_key = str(row["token_key"])
            if len(emitted) >= requested_slots:
                deferred.append({"token_key": token_key, "reason": "outside_slot_budget"})
                continue
            if token_key in selected_keys:
                deferred.append({"token_key": token_key, "reason": "duplicate_token_key"})
                continue
            selected_keys.add(token_key)
            selected_token_keys.append(token_key)
            emitted.append(token_key)

        if requested_slots == 0:
            deferred = [
                {"token_key": str(row["token_key"]), "reason": "below_quantum"}
                for row in rows
            ]
        for row in rows:
            candidate_receipts.append({
                "version": MAPPING_DESCRIPTOR_VERSION,
                "token_key": str(row["token_key"]),
                "source": source,
                "axis": axis,
                "current_offset": _round(float(row["current_offset"])),
                "input_receipt": dict(row["input_receipt"]),
            })
        packages.append({
            "package_key": key,
            "source": source,
            "axis": axis,
            "prior_remainder": _round(prior_remainder),
            "current_offset_total": _round(offset_total),
            "continuous_quota": _round(continuous_quota),
            "requested_slots": requested_slots,
            "emitted_token_keys": emitted,
            "deferred": deferred,
        })

    return {
        "version": EMISSION_PLAN_VERSION,
        "mapping_descriptor_version": MAPPING_DESCRIPTOR_VERSION,
        "emission_quantization_version": EMISSION_QUANTIZATION_VERSION,
        "emission_unit": unit,
        "candidates": candidate_receipts,
        "packages": packages,
        "selected_token_keys": selected_token_keys,
        "rejected": rejected,
    }


def commit_expression_emission_plan(
        plan: Mapping[str, object], *, f16_committed: bool,
        delivered_token_keys: Iterable[object],
        committed_remainders: Mapping[str, object] | None = None) -> dict[str, object]:
    """从一次 *已提交* F16 的真实 delivery 生成余量更新 receipt。

    该函数是纯计算；调用方若未持有 F16 receipt 则只能获得空更新。模型草稿、typing、
    UI 预览或候选编排调用本函数时永远不能获得可持久化的余量。``plan`` 只描述
    本 action 实际出现的 ``source × axis`` 包，因此 F16 提交时必须带入上一次已
    提交余量：本轮没有候选的包不是零变化，更不能被静默清空。
    """
    if str(plan.get("version") or "") != EMISSION_PLAN_VERSION:
        raise ValueError("invalid expression emission plan")
    raw_delivered = {str(key).strip() for key in delivered_token_keys if str(key).strip()}
    delivered: list[str] = []
    updates: dict[str, float] = {}
    packages = plan.get("packages")
    if not isinstance(packages, list):
        raise ValueError("invalid expression emission packages")
    if not f16_committed:
        return {
            "version": EMISSION_COMMIT_VERSION,
            "committed": False,
            "reason": "missing_f16_commit",
            "delivered_token_keys": [],
            "remainder_updates": {},
        }

    # 这里只继承已经提交、可解析的 package 余量；候选 plan 不读取正文、根状态或
    # 任何历史 writer 材料。后续本轮实际出现的包会在下方逐个覆盖自己的值。
    if isinstance(committed_remainders, Mapping):
        for raw_key, raw_value in committed_remainders.items():
            key = str(raw_key).strip()
            value = _finite_number(raw_value)
            if key and "::" in key and value is not None and value >= 0.0:
                updates[key] = _round(value)

    unit = _finite_number(plan.get("emission_unit"))
    if unit is None or unit <= 0.0:
        raise ValueError("invalid expression emission unit")
    for package in packages:
        if not isinstance(package, Mapping):
            raise ValueError("invalid expression emission package")
        key = str(package.get("package_key") or "").strip()
        quota = _finite_number(package.get("continuous_quota"))
        selected = package.get("emitted_token_keys")
        if not key or quota is None or quota < 0.0 or not isinstance(selected, list):
            raise ValueError("invalid expression emission package")
        actual = [str(token).strip() for token in selected
                  if str(token).strip() in raw_delivered]
        delivered.extend(actual)
        updates[key] = _round(max(0.0, quota - len(actual) * unit))

    return {
        "version": EMISSION_COMMIT_VERSION,
        "committed": True,
        "delivered_token_keys": delivered,
        "remainder_updates": updates,
    }
