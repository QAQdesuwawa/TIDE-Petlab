"""互动树 P4 的记忆状态印痕、策略与会话身份合同。

记忆选择、冷却、相关性和卡包仍属于 ``turn_recall``。本模块只回答一件更窄的事：
一条*已真正唤起*的历史消息，是否有可回放的 ``Delta_X_event`` 可供未来的唤起函数读取。
它故意不接受候选分、recall ID、召回次数、词元或 writer 卡包，避免这些记忆侧元数据
绕过 ``Delta_X_memory → X`` 边界进入调度。
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Iterable, Mapping

from core.effective_constants import CONSTANT_ORDER
from core.interaction_geometry import ROOT_ORDER, root_snapshot


STATE_IMPRINT_VERSION = "interaction-state-imprint-v1"
RECALL_IMPACT_VERSION = "recall-state-impact-v1-shadow"
RECALL_STATE_POLICY_VERSION = "recall-state-policy-v1"
RECALL_STATE_POLICY_BINDING_SCHEMA = 1
LEGACY_RECALL_POLICY_FAMILY_VERSION = "recall-policy-family-v1"
RECALL_POLICY_FAMILY_VERSION = "recall-policy-family-v2"
RECALL_POLICY_FAMILY_ANCHOR_SCHEMA = 1
P4_MEMORY_BASIS_VERSION = "p4-memory-basis-v1"
P4_DYNAMIC_READOUT_VERSION = "recall-state-readout-v2"
P4_TAU_REFERENCE_MS = 24 * 60 * 60 * 1000


def _effective_constant_snapshot(constants: Mapping[str, object]) -> dict[str, float]:
    """读取完整、已绑定的 ``C_effective``，拒绝隐式补零或截断。"""
    if not isinstance(constants, Mapping) or set(constants) != set(CONSTANT_ORDER):
        raise ValueError("P4 memory basis 必须读取完整七常量 C_effective")
    parsed = {}
    for axis in CONSTANT_ORDER:
        try:
            value = float(constants[axis])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"C_effective.{axis} 必须是数值") from exc
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError(f"C_effective.{axis} 必须在 0 到 1 之间")
        parsed[axis] = value
    return parsed


def p4_memory_basis(effective_constants: Mapping[str, object]) -> dict[str, object]:
    """从会话 ``C_effective`` 得到 P4 专属的三组正交根投影。

    该函数只实现作者已确认的映射，不能承担时间衰减、轨迹增益、RAG 选择或六轴写入：

    * ``a1/a2`` → 交换外推；
    * ``b1/b2/b3`` → 收缩保留；
    * ``g1/g2`` → 关系失稳。

    每行先按正交单位基投影，再除以该组范数，让 readout 保持 0..1 的同量纲。这样它等价于
    组内等权均值，但 receipt 同时保留基向量，审计时不会把“等权”伪装成无来源的标量。
    它不读取 M.A.N.E. 驱动力、P3 Z、状态 X、文本、记忆候选或 UI。
    """
    c = _effective_constant_snapshot(effective_constants)
    root_two = math.sqrt(2.0)
    root_three = math.sqrt(3.0)
    projection = {
        "exchange": {"a1": 1.0 / root_two, "a2": 1.0 / root_two},
        "retention": {"b1": 1.0 / root_three, "b2": 1.0 / root_three,
                      "b3": 1.0 / root_three},
        "instability": {"g1": 1.0 / root_two, "g2": 1.0 / root_two},
    }
    norm = {"exchange": root_two, "retention": root_three, "instability": root_two}
    raw = {
        name: sum(c[axis] * coefficient for axis, coefficient in row.items())
        for name, row in projection.items()
    }
    values = {name: raw[name] / norm[name] for name in raw}
    return {
        "version": P4_MEMORY_BASIS_VERSION,
        "constant_order": list(CONSTANT_ORDER),
        "projection": projection,
        "raw_unit_projection": raw,
        "B_memory": values,
        "derived_only": True,
    }


def _root_magnitude(vector: Mapping[str, object]) -> float:
    """六轴向量的单位化长度；只用于幅度，绝不生成新的六轴方向。"""
    values = root_snapshot(vector)
    return min(1.0, math.sqrt(sum(value * value for value in values.values())) / math.sqrt(6.0))


def derive_memory_temporal_readout(*, record: Mapping[str, object], now_ms: int,
                                   effective_constants: Mapping[str, object]) -> dict[str, object]:
    """为一个已提交 memory event 导出唯一的真实墙钟保留读数。

    这条 readout 是 F10 候选排序与 P4 状态影响共用的**同一事实**：

    ``tau_i = tau_ref * (1 + B_memory.retention * ||Delta_X_history|| / sqrt(6))``
    ``age_retention_i = 1 / (1 + age_ms / tau_i)``

    没有状态印痕的 legacy event 仍可作为离散事实被选择或表达，但其历史幅度按 0
    处理、`state_eligible=False`，不会伪造 P4 状态资格。这里不看 tick、消息条数、
    writer、UI 或 recall 次数；也不选择、排序或写任何状态。
    """
    if not isinstance(record, Mapping):
        raise ValueError("memory temporal readout 必须读取事件记录")
    try:
        now = int(now_ms)
        record_ts = int(record["ts"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("memory event 缺少有效 ts") from exc
    age_ms = now - record_ts
    if age_ms < 0:
        raise ValueError("memory event 不能来自未来")
    basis = p4_memory_basis(effective_constants)
    imprint = recalled_state_imprint(record)
    historical_magnitude = (_root_magnitude(imprint["Delta_X_event"])
                            if imprint is not None else 0.0)
    tau_ms = P4_TAU_REFERENCE_MS * (
        1.0 + float(basis["B_memory"]["retention"]) * historical_magnitude)
    age_retention = 1.0 / (1.0 + age_ms / tau_ms)
    return {
        "version": P4_DYNAMIC_READOUT_VERSION,
        "record_ts": record_ts,
        "age_ms": age_ms,
        "historical_magnitude": historical_magnitude,
        "tau_ms": tau_ms,
        "age_retention": age_retention,
        "basis": basis,
        "state_eligible": imprint is not None,
        "derived_only": True,
    }


def derive_dynamic_recall_state_measures(*, selected_records: Iterable[tuple[object, Mapping[str, object]]],
                                         now_ms: int,
                                         effective_constants: Mapping[str, object],
                                         current_event_delta: Mapping[str, object],
                                         state_after_event: Mapping[str, object],
                                         state_rest: Mapping[str, object],
                                         selection_receipts: Mapping[str, Mapping[str, object]] | None,
                                         trajectory_history: Mapping[str, Mapping[str, object]] | None):
    """Alpha 0.3 family-v2 的纯 P4 readout。

    三个连续读数严格分工：真实年龄只由 ``retention`` 调制，已提交 recall 轨迹只由
    ``instability`` 调制，容量只由 selected RAG relevance、当前根量幅度与 ``exchange``
    调制。它不选择/排序记忆，不读取表现层/P3/M.A.N.E. 结果，也不写状态。
    """
    try:
        now = int(now_ms)
    except (TypeError, ValueError) as exc:
        raise ValueError("now_ms 必须是整数时间戳") from exc
    basis = p4_memory_basis(effective_constants)
    b_memory = dict(basis["B_memory"])
    current = root_snapshot(current_event_delta)
    after = root_snapshot(state_after_event)
    rest = root_snapshot(state_rest)
    current_state_offset = {axis: after[axis] - rest[axis] for axis in ROOT_ORDER}
    u_event = _root_magnitude(current)
    u_state = _root_magnitude(current_state_offset)
    u_now = 1.0 - (1.0 - u_event) * (1.0 - u_state)
    selections = selection_receipts if isinstance(selection_receipts, Mapping) else {}
    history = trajectory_history if isinstance(trajectory_history, Mapping) else {}
    measures, seen = {}, set()
    for source, record in tuple(selected_records or ()):
        ref = recall_reference_id(source, record)
        if ref in seen:
            continue
        seen.add(ref)
        temporal = derive_memory_temporal_readout(
            record=record, now_ms=now, effective_constants=effective_constants)
        record_ts = int(temporal["record_ts"])
        age_ms = int(temporal["age_ms"])
        selection = selections.get(ref)
        if not isinstance(selection, Mapping) or "relevance" not in selection:
            # `prepare_recall_state_batch` 仍会以同一原因拒绝该 batch；这里绝不补 RAG 值。
            continue
        imprint = recalled_state_imprint(record)
        if imprint is None:
            # legacy record 不拥有状态影响资格；保持给下游的显式 legacy reason。
            continue
        historical_delta = imprint["Delta_X_event"]
        historical_magnitude = float(temporal["historical_magnitude"])
        row = history.get(ref) or {}
        if not isinstance(row, Mapping):
            raise ValueError("trajectory history 必须按 memory_id 提供映射")
        try:
            prior_actual_recalls = int(row.get("prior_actual_recalls", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("prior_actual_recalls 必须是非负整数") from exc
        if prior_actual_recalls < 0:
            raise ValueError("prior_actual_recalls 必须是非负整数")
        tau_ms = float(temporal["tau_ms"])
        age_retention = float(temporal["age_retention"])
        trajectory_gain = 1.0 + b_memory["instability"] * (
            prior_actual_recalls / (1.0 + prior_actual_recalls))
        relevance = _unit(selection["relevance"], label="selection_relevance")
        impact_capacity = relevance * (
            1.0 - (1.0 - b_memory["exchange"]) * (1.0 - u_now))
        measures[ref] = {
            "version": P4_DYNAMIC_READOUT_VERSION,
            "record_ts": record_ts,
            "age_ms": age_ms,
            "historical_magnitude": historical_magnitude,
            "prior_actual_recalls": prior_actual_recalls,
            "age_retention": age_retention,
            "trajectory_gain": trajectory_gain,
            "impact_capacity": impact_capacity,
            "selection_relevance": relevance,
            "derived_only": True,
        }
    return {
        "version": P4_DYNAMIC_READOUT_VERSION,
        "tau_reference_ms": P4_TAU_REFERENCE_MS,
        "basis": basis,
        "current_event_magnitude": u_event,
        "current_state_offset_magnitude": u_state,
        "current_context_magnitude": u_now,
        "state_rest": rest,
        "state_measures": measures,
        "fusion_mode": "global-vector-tanh-v1",
        "derived_only": True,
    }


def default_recall_policy_family() -> dict[str, str]:
    """返回 Alpha 0.3 新会话的策略族身份。

    这不是静态 policy 数值表：它冻结 family-v2 动态 readout 的输入合同。实际
    ``Delta_X_memory`` 仍只在每次 actual-selected recall 后，从根状态、真实年龄、
    RAG selection 和 recall 轨迹前向导出；不得由运行期猜一组角色或场景数字。
    """
    return {
        "version": RECALL_POLICY_FAMILY_VERSION,
        "readout_contract": "selected-recall-root-vector-v2",
        "selection_authority": "rag-selected-receipt-only",
    }


def normalize_recall_policy_family(family: Mapping[str, object]) -> dict[str, str]:
    """规范化策略族身份；不读取角色、用户、UI 或运行期状态。"""
    if not isinstance(family, Mapping):
        raise ValueError("P4 policy family 必须是版本化映射")
    expected = {"version", "readout_contract", "selection_authority"}
    actual = set(family)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        detail = []
        if missing:
            detail.append("缺少 " + ", ".join(missing))
        if extra:
            detail.append("包含未登记字段 " + ", ".join(extra))
        raise ValueError("P4 policy family 字段不完整：" + "；".join(detail))
    normalized = {key: str(family.get(key) or "").strip() for key in expected}
    valid_contracts = {
        LEGACY_RECALL_POLICY_FAMILY_VERSION: "selected-recall-root-vector-v1",
        RECALL_POLICY_FAMILY_VERSION: "selected-recall-root-vector-v2",
    }
    expected_contract = valid_contracts.get(normalized["version"])
    if expected_contract is None:
        raise ValueError("P4 policy family 版本无效")
    if normalized["readout_contract"] != expected_contract:
        raise ValueError("P4 policy family readout contract 无效")
    if normalized["selection_authority"] != "rag-selected-receipt-only":
        raise ValueError("P4 policy family selection authority 无效")
    return {key: normalized[key] for key in sorted(expected)}


def append_recall_policy_family_anchor(profile: Mapping[str, object],
                                       family: Mapping[str, object], *,
                                       effective_constants_version: str,
                                       b0_version: str, at_ms: int,
                                       source: str) -> dict[str, object]:
    """为新会话追加 Alpha 0.3 的策略族 anchor。"""
    if source != "initial_new_session":
        raise ValueError("P4 policy family anchor 只能来自新会话创建事务")
    if not isinstance(at_ms, int) or at_ms < 0:
        raise ValueError("P4 policy family anchor 必须带非负绝对时间")
    effective_ref = str(effective_constants_version or "").strip()
    b0_ref = str(b0_version or "").strip()
    if not effective_ref or not b0_ref:
        raise ValueError("P4 policy family anchor 必须绑定 C_effective 与 B₀ 版本")
    normalized = normalize_recall_policy_family(family)
    copied = dict(profile or {})
    existing = [dict(item) for item in (copied.get("recall_policy_family_anchors") or [])]
    parent = copied.get("session_recall_policy_family_anchor_version")
    ordinal = len(existing) + 1
    identity = {
        "schema_version": RECALL_POLICY_FAMILY_ANCHOR_SCHEMA,
        "ordinal": ordinal,
        "family": normalized,
        "effective_constants_version": effective_ref,
        "b0_version": b0_ref,
        "source": source,
        "parent_version": parent,
        "created_at_ms": at_ms,
    }
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":")).encode("utf-8")).hexdigest()[:12]
    version = "recall-policy-family-anchor-v%d-%04d-%s" % (
        RECALL_POLICY_FAMILY_ANCHOR_SCHEMA, ordinal, digest)
    existing.append(dict(identity, version=version))
    copied["recall_policy_family_anchors"] = existing
    copied["session_recall_policy_family_anchor_version"] = version
    return copied


def current_recall_policy_family_anchor(profile: Mapping[str, object]) -> Mapping[str, object] | None:
    """读取当前会话 anchor；缺失明确代表旧会话尚未进入 Alpha 0.3。"""
    active = str((profile or {}).get("session_recall_policy_family_anchor_version") or "")
    for record in (profile or {}).get("recall_policy_family_anchors") or []:
        if isinstance(record, Mapping) and record.get("version") == active:
            family = normalize_recall_policy_family(record.get("family") or {})
            effective_ref = str(record.get("effective_constants_version") or "").strip()
            b0_ref = str(record.get("b0_version") or "").strip()
            if not effective_ref or not b0_ref:
                raise ValueError("P4 policy family anchor 缺少常量版本")
            return {
                "version": str(record["version"]),
                "family": family,
                "effective_constants_version": effective_ref,
                "b0_version": b0_ref,
                "source": str(record.get("source") or ""),
                "created_at_ms": int(record["created_at_ms"]),
            }
    return None


def recall_reference_id(source: object, record: Mapping[str, object]) -> str:
    """返回一条离散、已实际唤起记忆的可回放引用 ID。

    新 committed event 必须沿用其既有精确身份；F10 selection receipt、动态
    readout、P4 batch 与后续 action 才能指向同一事实。仅历史无 ID 行保留
    ``source:record_ts`` 兼容键，且不从时间或正文推造现代 identity。
    """
    if not isinstance(record, Mapping):
        raise ValueError("recall record 必须是映射")
    event_id = str(record.get("event_id") or "").strip()
    if event_id:
        return event_id
    try:
        ts = int(record["ts"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("recall record 缺少有效 ts") from exc
    return "%s:%d" % (str(source), ts)


def recalled_state_imprint(record: Mapping[str, object]) -> dict[str, object] | None:
    """读取一条已提交历史事件的状态印痕；旧档没有就返回 ``None``。

    返回 ``None`` 不是“零影响”：它明确表示该条记录只能作表达兼容，绝不能被未来
    recall 状态桥伪造成 ``Delta_X_memory``。调用者还必须先完成语义相关性与真正唤起
    判定；本函数不把候选当唤起。
    """
    if not isinstance(record, Mapping):
        raise ValueError("memory record 必须是映射")
    imprint = record.get("state_imprint")
    if imprint is None:
        return None
    if not isinstance(imprint, Mapping):
        raise ValueError("state_imprint 必须是映射")
    required = ("X_before", "Delta_X_event", "X_after")
    missing = [key for key in required if key not in imprint]
    if missing:
        raise ValueError("state_imprint 缺少 " + ", ".join(missing))
    before = root_snapshot(imprint["X_before"])
    delta = root_snapshot(imprint["Delta_X_event"])
    after = root_snapshot(imprint["X_after"])
    for axis in ROOT_ORDER:
        if abs((before[axis] + delta[axis]) - after[axis]) > 1e-8:
            raise ValueError(f"state_imprint {axis} 不是同一次提交事件的变化")
    return {
        "version": str(imprint.get("version") or STATE_IMPRINT_VERSION),
        "X_before": before,
        "Delta_X_event": delta,
        "X_after": after,
        "derived_only": True,
    }


def _unit(value: object, *, label: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 必须是 0 到 1 的数值") from exc
    if not 0.0 <= numeric <= 1.0:
        raise ValueError(f"{label} 必须在 0 到 1 之间")
    return numeric


def _trajectory_gain(value: object) -> float:
    """读取 P4 的可回放轨迹系数。

    旧 v1 policy 把该项当作 0..1 的衰减系数；Alpha 0.3 family-v2 则把既成
    recall 轨迹定义为 1..2 的有限放大。因此这里不能复用 ``_unit`` 并把新族的
    合法读数静默截断为 1。两种版本共用一个明确、有界的 0..2 合同。
    """
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("trajectory_gain 必须是数值") from exc
    if not math.isfinite(numeric) or not 0.0 <= numeric <= 2.0:
        raise ValueError("trajectory_gain 必须在 0 到 2 之间")
    return numeric


def _positive(value: object, *, label: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 必须是正数") from exc
    if not math.isfinite(numeric) or numeric <= 0.0:
        raise ValueError(f"{label} 必须是正数")
    return numeric


def normalize_recall_state_policy(policy: Mapping[str, object]) -> dict[str, object]:
    """严格规范化 P4 政策，拒绝未留痕的额外自由度。

    这一步只校验并复制政策；既不读会话状态、记忆、模型、UI，也不决定一次 recall
    是否发生。之后的 profile 版本绑定和每轮 measurement 都必须使用这份同构快照，
    不能分别再解释一遍参数。
    """
    if not isinstance(policy, Mapping):
        raise ValueError("P4 state policy 必须是版本化映射")
    expected = {"version", "age_half_life_ms", "trajectory_floor",
                "trajectory_ceiling", "trajectory_rate", "axis_caps"}
    actual = set(policy)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        detail = []
        if missing:
            detail.append("缺少 " + ", ".join(missing))
        if extra:
            detail.append("包含未登记字段 " + ", ".join(extra))
        raise ValueError("P4 state policy 字段不完整：" + "；".join(detail))
    if str(policy.get("version") or "") != RECALL_STATE_POLICY_VERSION:
        raise ValueError("P4 state policy 版本无效")
    half_life_ms = _positive(policy.get("age_half_life_ms"), label="age_half_life_ms")
    floor = _unit(policy.get("trajectory_floor"), label="trajectory_floor")
    ceiling = _unit(policy.get("trajectory_ceiling"), label="trajectory_ceiling")
    if ceiling < floor:
        raise ValueError("trajectory_ceiling 不能小于 trajectory_floor")
    rate = _positive(policy.get("trajectory_rate"), label="trajectory_rate")
    caps = root_snapshot(policy.get("axis_caps"))
    if any(caps[axis] <= 0.0 for axis in ROOT_ORDER):
        raise ValueError("axis_caps 必须为六轴正数")
    return {
        "version": RECALL_STATE_POLICY_VERSION,
        "age_half_life_ms": half_life_ms,
        "trajectory_floor": floor,
        "trajectory_ceiling": ceiling,
        "trajectory_rate": rate,
        "axis_caps": caps,
    }


def append_memory_state_policy_version(profile: Mapping[str, object],
                                       policy: Mapping[str, object], *,
                                       at_ms: int, source: str) -> dict[str, object]:
    """把显式 P4 policy 绑定为会话只读版本；不修改既有版本。"""
    if source != "initial_explicit_runner":
        raise ValueError("P4 policy 版本只能来自显式会话创建入口")
    if not isinstance(at_ms, int) or at_ms < 0:
        raise ValueError("P4 policy 版本必须带非负绝对时间")
    normalized = normalize_recall_state_policy(policy)
    copied = dict(profile or {})
    existing = [dict(item) for item in (copied.get("memory_state_policy_versions") or [])]
    parent = copied.get("session_memory_state_policy_version")
    ordinal = len(existing) + 1
    identity = {
        "schema_version": RECALL_STATE_POLICY_BINDING_SCHEMA,
        "ordinal": ordinal,
        "policy": normalized,
        "source": source,
        "parent_version": parent,
        "created_at_ms": at_ms,
    }
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":")).encode("utf-8")).hexdigest()[:12]
    version = "memory-state-policy-v%d-%04d-%s" % (
        RECALL_STATE_POLICY_BINDING_SCHEMA, ordinal, digest)
    existing.append(dict(identity, version=version))
    copied["memory_state_policy_versions"] = existing
    copied["session_memory_state_policy_version"] = version
    return copied


def current_memory_state_policy(profile: Mapping[str, object]) -> Mapping[str, object] | None:
    """读取当前会话绑定的 P4 policy；没有档案时明确返回 ``None``。"""
    active = str((profile or {}).get("session_memory_state_policy_version") or "")
    for record in (profile or {}).get("memory_state_policy_versions") or []:
        if isinstance(record, Mapping) and record.get("version") == active:
            normalized = normalize_recall_state_policy(record.get("policy") or {})
            return {"version": str(record["version"]), "policy": normalized,
                    "source": str(record.get("source") or ""),
                    "created_at_ms": int(record["created_at_ms"])}
    return None


def derive_recall_state_measures(*, selected_records: Iterable[tuple[object, Mapping[str, object]]],
                                 now_ms: int, policy: Mapping[str, object],
                                 trajectory_history: Mapping[str, Mapping[str, object]] | None):
    """从**显式版本化政策**导出 P4 的年龄与轨迹测量 receipt。

    这不是候选选择器，也不读取词元、Agent 1、writer 或 UI。`trajectory_history`
    必须来自 append-only 的已提交 recall 账本；这里仅消费每条离散记忆此前真正被
    唤起的次数，使用连续、有界的增益而非“第 N 次以后禁用/必发”规则。

    政策没有默认值，必须显式给出：

    ``age_half_life_ms``（真实墙钟半衰期）、`trajectory_floor`、
    `trajectory_ceiling`、`trajectory_rate` 与六轴 `axis_caps`。这使参数版本和
    每次测量输入都能进入 receipt，Runner 在未定标前无法静默启用 P4。
    """
    normalized_policy = normalize_recall_state_policy(policy)
    half_life_ms = normalized_policy["age_half_life_ms"]
    floor = normalized_policy["trajectory_floor"]
    ceiling = normalized_policy["trajectory_ceiling"]
    rate = normalized_policy["trajectory_rate"]
    caps = normalized_policy["axis_caps"]

    try:
        now = int(now_ms)
    except (TypeError, ValueError) as exc:
        raise ValueError("now_ms 必须是整数时间戳") from exc
    history = trajectory_history if isinstance(trajectory_history, Mapping) else {}
    measures, seen = {}, set()
    for source, record in tuple(selected_records or ()):
        ref = recall_reference_id(source, record)
        if ref in seen:
            continue
        seen.add(ref)
        try:
            record_ts = int(record["ts"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("selected recall record 缺少有效 ts") from exc
        age_ms = now - record_ts
        if age_ms < 0:
            raise ValueError("selected recall record 不能来自未来")
        row = history.get(ref) or {}
        if not isinstance(row, Mapping):
            raise ValueError("trajectory history 必须按 memory_id 提供映射")
        try:
            prior_actual_recalls = int(row.get("prior_actual_recalls", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("prior_actual_recalls 必须是非负整数") from exc
        if prior_actual_recalls < 0:
            raise ValueError("prior_actual_recalls 必须是非负整数")
        age_retention = math.exp(-math.log(2.0) * age_ms / half_life_ms)
        # 轨迹只在已经真实想起的 append-only 历史上连续强化，并向 ceiling 收敛；
        # 它不是固定冷却、次数门或对当前 selected set 的重新排序。
        trajectory_gain = floor + (ceiling - floor) * (
            1.0 - math.exp(-rate * prior_actual_recalls))
        measures[ref] = {
            "version": RECALL_STATE_POLICY_VERSION,
            "record_ts": record_ts,
            "age_ms": age_ms,
            "age_retention": age_retention,
            "prior_actual_recalls": prior_actual_recalls,
            "trajectory_gain": trajectory_gain,
            "policy_version": RECALL_STATE_POLICY_VERSION,
            "derived_only": True,
        }
    return {
        "version": RECALL_STATE_POLICY_VERSION,
        "now_ms": now,
        "policy": normalized_policy,
        "state_measures": measures,
        "axis_caps": caps,
        "derived_only": True,
    }


def _cosine(left: Mapping[str, object], right: Mapping[str, object]) -> float:
    """六轴事件变化的向量共振；零向量没有方向，返回中性 1。"""
    a = root_snapshot(left)
    b = root_snapshot(right)
    dot = sum(a[axis] * b[axis] for axis in ROOT_ORDER)
    norm_a = math.sqrt(sum(a[axis] ** 2 for axis in ROOT_ORDER))
    norm_b = math.sqrt(sum(b[axis] ** 2 for axis in ROOT_ORDER))
    if norm_a <= 1e-12 or norm_b <= 1e-12:
        return 1.0
    # 负方向不把历史影响反号；它只说明本轮不构成“共振放大”。印痕本身仍保留符号，
    # 所以多条已唤起记忆在后续融合时可以自然抵消。
    return max(0.0, min(1.0, dot / (norm_a * norm_b)))


def recalled_impact(*, record: Mapping[str, object], actual_recalled: bool,
                    current_event_delta: Mapping[str, object], relevance: float,
                    age_retention: float, trajectory_gain: float = 1.0,
                    impact_capacity: float | None = None,
                    memory_id: str | None = None) -> dict[str, object] | None:
    """P4-1 shadow：将一条**已真实唤起**的历史印痕变成 ``dX_i``。

    选择/语义相关性仍在上游。这里的 ``relevance``、``age_retention`` 和
    ``trajectory_gain`` 必须由可回放的 recall receipt 显式给出，而不是从 token、卡包、
    recall 次数或角色常量猜出。公式遵循已裁定的单向骨架：

    ``lever = relevance × age_retention × resonance × trajectory_gain``
    ``dX_i = Delta_X_event(history) × lever``

    该影子函数不产生表达卡、不修改记忆轨迹、不写状态；`actual_recalled=False` 或旧档
    一律没有状态影响。具体时间半衰期、轨迹更新和融合容量尚未定标，因此必须由调用者
    传入，不在这里藏默认心理参数。
    """
    if not actual_recalled:
        return None
    imprint = recalled_state_imprint(record)
    if imprint is None:
        return None
    relevance_value = _unit(relevance, label="relevance")
    age_value = _unit(age_retention, label="age_retention")
    trajectory_value = _trajectory_gain(trajectory_gain)
    current = root_snapshot(current_event_delta)
    history_delta = imprint["Delta_X_event"]
    resonance = _cosine(history_delta, current)
    # v1 静态 fixture 延续 relevance 作为容量；family-v2 则已用 selected receipt、当前根状态
    # 与 exchange 根投影得到独立容量，不能在这里第二次相乘 relevance。
    capacity_value = (relevance_value if impact_capacity is None
                      else _unit(impact_capacity, label="impact_capacity"))
    leverage = capacity_value * age_value * resonance * trajectory_value
    return {
        "version": RECALL_IMPACT_VERSION,
        "memory_id": str(memory_id) if memory_id is not None else None,
        "imprint_version": imprint["version"],
        "relevance": relevance_value,
        "impact_capacity": capacity_value,
        "age_retention": age_value,
        "trajectory_gain": trajectory_value,
        "resonance": resonance,
        "leverage": leverage,
        "historical_delta": history_delta,
        "impact_delta": {axis: history_delta[axis] * leverage for axis in ROOT_ORDER},
        "expression_independent": True,
        "derived_only": True,
    }


def fuse_recalled_impacts(impacts: Iterable[Mapping[str, object]], *,
                          axis_caps: Mapping[str, object] | None = None,
                          fusion_mode: str = "axis-tanh-v1") -> dict[str, object]:
    """把同一事件中已唤起的 ``dX_i`` 一次性融合为有界 ``Delta_X_memory``。

    每条离散记忆仍在 ``items`` 中保留独立 receipt；这里只合成它们对当下状态的数值
    影响。按轴先求和后再做 ``tanh`` 有界投影，因此同向自然增强、反向自然抵消，且不依赖
    检索顺序。容量不能在此模块擅自设为某个心理学阈值，必须由未来版本化状态合同显式提供。
    """
    if fusion_mode not in {"axis-tanh-v1", "global-vector-tanh-v1"}:
        raise ValueError("未知的 recall fusion_mode")
    caps = None
    if fusion_mode == "axis-tanh-v1":
        caps = root_snapshot(axis_caps)
        if any(caps[axis] <= 0.0 for axis in ROOT_ORDER):
            raise ValueError("axis_caps 必须为六轴正数")
    items = []
    raw = {axis: 0.0 for axis in ROOT_ORDER}
    for impact in impacts:
        if not isinstance(impact, Mapping):
            raise ValueError("recall impact 必须是映射")
        delta = impact.get("impact_delta")
        if not isinstance(delta, Mapping):
            raise ValueError("recall impact 缺少 impact_delta")
        normalized = root_snapshot(delta)
        items.append({
            "memory_id": impact.get("memory_id"),
            "impact_delta": normalized,
        })
        for axis in ROOT_ORDER:
            raw[axis] += normalized[axis]
    if fusion_mode == "axis-tanh-v1":
        fused = {axis: caps[axis] * math.tanh(raw[axis] / caps[axis])
                 for axis in ROOT_ORDER}
    else:
        magnitude = math.sqrt(sum(raw[axis] * raw[axis] for axis in ROOT_ORDER))
        scale = 0.0 if magnitude <= 1e-12 else math.tanh(magnitude) / magnitude
        fused = {axis: raw[axis] * scale for axis in ROOT_ORDER}
    return {
        "version": RECALL_IMPACT_VERSION,
        "items": items,
        "raw_sum": raw,
        "Delta_X_memory": fused,
        "axis_caps": caps,
        "fusion_mode": fusion_mode,
        "single_state_commit_required": True,
        "derived_only": True,
    }


def prepare_recall_state_batch(*, selected_records: Iterable[tuple[object, Mapping[str, object]]],
                               current_event_delta: Mapping[str, object],
                               selection_receipts: Mapping[str, Mapping[str, object]] | None,
                               state_measures: Mapping[str, Mapping[str, object]] | None,
                               axis_caps: Mapping[str, object] | None,
                               fusion_mode: str = "axis-tanh-v1") -> dict[str, object]:
    """把已经**实际唤起**的离散记录编排为一次状态影响候选批次。

    此处绝不接收候选列表、词元、表达卡或 writer 结果；调用方传入的
    ``selected_records`` 已是 recall resolver 的真实选择。每条有状态印痕还必须有同源
    ``selection_receipts[ID].relevance``：它只能来自 resolver 已选 bundle 的 RAG 向量／资格
    结果，不能由 Agent 1、writer 或本模块补算。时间保留和轨迹权重则单列在
    ``state_measures``，使“是否想起”与“想起后影响多强”不共用一个泛化测量入口。
    缺失任一 receipt/测量时整批不可提交而不是猜测默认值或只提交一部分。旧档没有印痕时
    仅记为兼容原因，不拥有状态影响权威。
    """
    current = root_snapshot(current_event_delta)
    selections = selection_receipts if isinstance(selection_receipts, Mapping) else {}
    measures = state_measures if isinstance(state_measures, Mapping) else {}
    selected, legacy, missing, impacts, seen = [], [], [], [], set()
    for source, record in tuple(selected_records or ()):
        ref = recall_reference_id(source, record)
        if ref in seen:
            continue
        seen.add(ref)
        selected.append(ref)
        if recalled_state_imprint(record) is None:
            legacy.append({"memory_id": ref, "reason": "no_state_imprint"})
            continue
        selection = selections.get(ref)
        if not isinstance(selection, Mapping) or "relevance" not in selection:
            missing.append({"memory_id": ref, "reason": "missing_selection_receipt"})
            continue
        measure = measures.get(ref)
        if not isinstance(measure, Mapping):
            missing.append({"memory_id": ref, "reason": "missing_state_measure"})
            continue
        required = ("age_retention", "trajectory_gain")
        absent = [key for key in required if key not in measure]
        if absent:
            missing.append({"memory_id": ref, "reason": "missing_measure_fields",
                            "fields": absent})
            continue
        impacts.append(recalled_impact(
            record=record, actual_recalled=True, current_event_delta=current,
            relevance=selection["relevance"], age_retention=measure["age_retention"],
            trajectory_gain=measure["trajectory_gain"],
            impact_capacity=measure.get("impact_capacity"), memory_id=ref))
    if missing:
        return {
            "version": RECALL_IMPACT_VERSION,
            "selected_memory_ids": selected,
            "legacy_records": legacy,
            "missing": missing,
            "ready_to_commit": False,
            "Delta_X_memory": None,
            "derived_only": True,
        }
    if not impacts:
        return {
            "version": RECALL_IMPACT_VERSION,
            "selected_memory_ids": selected,
            "legacy_records": legacy,
            "missing": [],
            "ready_to_commit": False,
            "reason": "no_selected_state_imprint",
            "Delta_X_memory": None,
            "derived_only": True,
        }
    if fusion_mode == "axis-tanh-v1" and axis_caps is None:
        return {
            "version": RECALL_IMPACT_VERSION,
            "selected_memory_ids": selected,
            "legacy_records": legacy,
            "missing": [{"reason": "missing_axis_caps"}],
            "ready_to_commit": False,
            "Delta_X_memory": None,
            "derived_only": True,
        }
    fused = fuse_recalled_impacts(impacts, axis_caps=axis_caps, fusion_mode=fusion_mode)
    return {
        "version": RECALL_IMPACT_VERSION,
        "selected_memory_ids": selected,
        "legacy_records": legacy,
        "missing": [],
        "ready_to_commit": True,
        "impacts": impacts,
        "fusion": fused,
        "Delta_X_memory": fused["Delta_X_memory"],
        "derived_only": True,
    }
