"""显式 B₀ 的 append-only 版本档案。

这里只处理用户已经明确提交的五维偏好，不读取 D(t)、F12 候选、文本、沉默或模型结果。
``profile.prior`` 保留为当前版本的兼容镜像；权威历史在 ``b0_versions``，每一次显式
保存都会追加新版本并更新当前会话绑定。
"""
from __future__ import annotations

import hashlib
import json
from typing import Mapping

from model_packages.cx_six_seven_v1.preference_parameters import (
    neutral_preference_weights,
    resolve_effective_constants,
    validate_preference_weights,
)


VERSION_SCHEMA = 1


def _weights(value: Mapping[str, object]) -> dict[str, float]:
    return validate_preference_weights(value)


def append_explicit_b0_version(profile: Mapping[str, object], weights: Mapping[str, object], *,
                               enabled: bool, source: str, at_ms: int) -> dict[str, object]:
    """返回带新 B₀ 版本和当前会话绑定的 profile 副本。

    ``source`` 只允许显式用户入口；这样调用方无法借由普通互动或自动候选伪装成一次
    B₀ 更新。此函数不写盘，Runner 负责将一次完整 profile 原子提交。
    """
    if source not in {"initial_explicit_text", "explicit_text", "explicit_weights",
                      "initial_unspecified", "legacy_declared_import", "legacy_unknown_closed"}:
        raise ValueError("B₀ 版本只能来自显式用户入口")
    if not isinstance(at_ms, int) or at_ms < 0:
        raise ValueError("B₀ 版本必须带非负绝对时间")
    prior = _weights(weights)
    copied = dict(profile or {})
    existing = [dict(item) for item in (copied.get("b0_versions") or [])]
    parent = copied.get("session_b0_version") or copied.get("active_b0_version")
    ordinal = len(existing) + 1
    identity = {"schema_version": VERSION_SCHEMA, "ordinal": ordinal, "weights": prior,
                "enabled": bool(enabled), "source": source, "parent_version": parent,
                "created_at_ms": at_ms}
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":")).encode("utf-8")).hexdigest()[:12]
    version = f"b0-v{VERSION_SCHEMA}-{ordinal:04d}-{digest}"
    record = dict(identity, version=version)
    existing.append(record)
    copied["b0_versions"] = existing
    copied["active_b0_version"] = version
    copied["session_b0_version"] = version
    copied["prior"] = prior
    return copied


def migrate_legacy_b0_binding(profile: Mapping[str, object], *, at_ms: int) -> tuple[dict[str, object], bool]:
    """把缺版本的旧 profile 一次性转为可审计 B₀ 锚。

    只有旧档已经记录 ``preference_declared=True`` 且 ``preference_status='applied'`` 时，
    才把旧 ``prior`` 作为历史显式声明导入。其余裸 prior 不能证明用户填写过偏好，必须创建
    关闭的均匀锚。这个 schema migration 不读取互动、沉默、模型结果或 F12。
    """
    copied = dict(profile or {})
    if current_b0(copied) is not None:
        return copied, False
    declared = copied.get("preference_declared") is True
    applied = str(copied.get("preference_status") or "") == "applied"
    if declared and applied:
        return append_explicit_b0_version(
            copied, copied.get("prior") or {}, enabled=True,
            source="legacy_declared_import", at_ms=at_ms,
        ), True
    neutral = neutral_preference_weights()
    return append_explicit_b0_version(
        copied, neutral, enabled=False, source="legacy_unknown_closed", at_ms=at_ms,
    ), True


def current_b0(profile: Mapping[str, object]) -> Mapping[str, object] | None:
    """只读取当前绑定版本；缺少版本档案的 legacy profile 不作隐式升级。"""
    active = str((profile or {}).get("session_b0_version") or "")
    for record in (profile or {}).get("b0_versions") or []:
        if isinstance(record, Mapping) and record.get("version") == active:
            return dict(record)
    return None


def resolve_session_effective_constants(c_base: Mapping[str, object],
                                        profile: Mapping[str, object], *,
                                        correction: Mapping[str, object] | None = None) -> dict[str, object]:
    """从当前会话的不可变 B₀ 引用纯读取 `C_effective` receipt。

    这是 replay/调用方的显式桥，不读 ``profile.prior``，更不写 profile、state 或调度。缺少
    版本档案的旧 profile 必须显式迁移，不能悄悄把裸 prior 当作已获授权的 B₀。
    """
    binding = current_b0(profile)
    if binding is None:
        raise ValueError("当前会话没有可解析的 B₀ 版本；旧档必须显式迁移")
    receipt = resolve_effective_constants(
        c_base,
        b0_version=str(binding["version"]),
        b0=binding["weights"],
        b0_enabled=bool(binding["enabled"]),
        correction=correction,
    )
    return dict(receipt, b0_binding_source=binding["source"],
                b0_binding_created_at_ms=binding["created_at_ms"])
