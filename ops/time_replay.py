"""隔离的合成时间回放。

它把相对时间轴喂给既有 Runner，以验证间隔、积载、召回门和延迟行为；不改写任何
真实角色档案，也不替代真实时间。生成器由调用方注入，因此离线桩测试和真实模型测试
使用同一条时间轴，而不把 API 选择耦合进回放器。
"""
from __future__ import annotations

import os
import hashlib
import json

from ops.storage import UserStore, validate_store_id
from ops.telemetry import project_state
from ops.fixture_provenance import SYNTHETIC_FIXTURE_SOURCE


SCHEMA_VERSION = 1


def _as_int(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name} 必须是整数")
    try:
        converted = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是整数") from exc
    return converted


def _unit_interval(value, name):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是数值") from exc
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"{name} 必须在 [0, 1]")
    return number


def _seed_tokens(value, name):
    if not isinstance(value, list):
        raise ValueError(f"{name} 必须是数组")
    tokens = []
    for index, raw in enumerate(value, start=1):
        token = dict(raw or {})
        if not isinstance(token.get("text"), str) or not token["text"].strip():
            raise ValueError(f"{name}[{index}].text 必须是非空字符串")
        if "strength" in token:
            token["strength"] = _unit_interval(token["strength"],
                                                  f"{name}[{index}].strength")
        tokens.append(token)
    return tokens


def validate_memory_seeds(value):
    """规范化仅供隔离回放使用的合成记忆种子。

    种子是测试夹具，不是历史对话的替代品：会写入各自的记忆/台账索引并标明来源，
    但不会推进墙钟、生成 NPC 动作或进入生产角色目录。
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("memory_seeds 必须是数组")
    seeds, ids = [], set()
    for index, raw in enumerate(value, start=1):
        row = dict(raw or {})
        seed_id = validate_store_id(row.get("id"))
        if seed_id in ids:
            raise ValueError("memory_seeds.id 必须唯一")
        ids.add(seed_id)
        offset_ms = _as_int(row.get("offset_ms"), f"memory_seeds[{index}].offset_ms")
        if offset_ms >= 0:
            raise ValueError("memory_seeds 的 offset_ms 必须为负")
        salience = _unit_interval(row.get("salience", 0.0),
                                  f"memory_seeds[{index}].salience")
        messages = []
        for message_index, raw_message in enumerate(row.get("messages") or [], start=1):
            message = dict(raw_message or {})
            role = str(message.get("role") or "").strip()
            text = message.get("text")
            if role not in {"user", "npc"}:
                raise ValueError(f"memory_seeds[{index}].messages[{message_index}].role 无效")
            if not isinstance(text, str) or not text.strip():
                raise ValueError(f"memory_seeds[{index}].messages[{message_index}].text 无效")
            messages.append({"role": role, "text": text})
        archive_tokens = _seed_tokens(row.get("archive_tokens", []),
                                      f"memory_seeds[{index}].archive_tokens")
        ledger_facts = row.get("ledger_facts") or []
        if not isinstance(ledger_facts, list) or not all(
                isinstance(fact, str) and fact.strip() for fact in ledger_facts):
            raise ValueError(f"memory_seeds[{index}].ledger_facts 必须是非空字符串数组")
        topic = row.get("topic", "")
        if not isinstance(topic, str):
            raise ValueError(f"memory_seeds[{index}].topic 必须是字符串")
        drift = row.get("drift", {})
        if not isinstance(drift, dict):
            raise ValueError(f"memory_seeds[{index}].drift 必须是对象")
        if not (messages or archive_tokens or ledger_facts):
            raise ValueError(f"memory_seeds[{index}] 至少需要一种记忆证据")
        seeds.append({"id": seed_id, "offset_ms": offset_ms, "salience": salience,
                      "messages": messages, "archive_tokens": archive_tokens,
                      "ledger_facts": list(ledger_facts), "topic": topic,
                      "drift": dict(drift)})
    return seeds


def apply_memory_seeds(runner, seeds, start_ms, *, source=SYNTHETIC_FIXTURE_SOURCE):
    """通过 Memory/Ledger 的公开写入接口建立带来源戳的测试记忆。"""
    source = str(source or SYNTHETIC_FIXTURE_SOURCE)
    rows = []
    for seed in seeds:
        ts = int(start_ms) + int(seed["offset_ms"])
        # 冻结测试记忆也必须保留一条可回放的 exact event identity。它只标识
        # fixture 已声明的共同经历，不能由 ts/text 在读取期反推。
        event_id = "synthetic-seed:%s:%d" % (str(seed["id"]), ts)
        for message in seed["messages"]:
            runner.memory.remember_message(
                ts, message["role"], message["text"], S=seed["salience"],
                tokens=seed["archive_tokens"], source=source,
                params_sha=getattr(runner, "params_sha", None),
                cause_id="seed:" + seed["id"], seed_id=seed["id"], event_id=event_id)
        if seed["archive_tokens"]:
            runner.memory.archive_tokens(
                ts, seed["archive_tokens"], S=seed["salience"], drift=seed["drift"],
                source=source, seed_id=seed["id"], event_id=event_id)
        if seed["ledger_facts"]:
            runner.ledger.merge(
                seed["ledger_facts"], ts, S=seed["salience"],
                tokens=seed["archive_tokens"], topic=seed["topic"],
                source=source, seed_id=seed["id"], event_id=event_id)
        runner.store.append_event({
            "schema_version": SCHEMA_VERSION, "kind": "synthetic_memory_seeded", "ts": ts,
            "detail": {"seed_id": seed["id"], "source": source,
                       "message_count": len(seed["messages"]),
                       "archive_token_count": len(seed["archive_tokens"]),
                       "ledger_fact_count": len(seed["ledger_facts"])},
        })
        rows.append({"id": seed["id"], "now_ms": ts, "event_id": event_id,
                     "message_count": len(seed["messages"]),
                     "archive_token_count": len(seed["archive_tokens"]),
                     "ledger_fact_count": len(seed["ledger_facts"])})
    return rows


def validate_scenario(scenario):
    """验证版本化相对时间轴，返回不含调用方可变对象的规范副本。"""
    source = dict(scenario or {})
    if source.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"只支持 schema_version={SCHEMA_VERSION} 的时间回放场景")
    scenario_id = validate_store_id(source.get("scenario_id"))
    start_ms = _as_int(source.get("start_ms"), "start_ms")
    history = source.get("history", [])
    if not isinstance(history, list):
        raise ValueError("history 必须是列表")
    normalized_history, previous_history = [], None
    for index, raw in enumerate(history, start=1):
        item = dict(raw or {})
        offset_ms = _as_int(item.get("offset_ms"), f"history[{index}].offset_ms")
        if offset_ms >= 0 or (previous_history is not None and offset_ms < previous_history):
            raise ValueError("history 的 offset_ms 必须为负且单调不减")
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"history[{index}].text 必须是非空字符串")
        normalized_history.append({"offset_ms": offset_ms, "text": text})
        previous_history = offset_ms

    steps = source.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("steps 必须是非空列表")
    normalized, previous = [], -1
    for index, raw in enumerate(steps, start=1):
        item = dict(raw or {})
        offset_ms = _as_int(item.get("offset_ms"), f"steps[{index}].offset_ms")
        if offset_ms < 0 or offset_ms < previous:
            raise ValueError("steps 的 offset_ms 必须非负且单调不减")
        kind = str(item.get("kind") or "user_message")
        if kind == "user_message":
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError(f"steps[{index}].text 必须是非空字符串")
            normalized.append({"offset_ms": offset_ms, "kind": kind, "text": text})
        elif kind == "catch_up":
            normalized.append({"offset_ms": offset_ms, "kind": kind})
        else:
            raise ValueError("steps.kind 只能是 user_message 或 catch_up")
        previous = offset_ms
    expected = source.get("expected_observations", {})
    if not isinstance(expected, dict):
        raise ValueError("expected_observations 必须是对象")
    return {"schema_version": SCHEMA_VERSION, "scenario_id": scenario_id,
            "start_ms": start_ms, "history": normalized_history, "steps": normalized,
            "memory_seeds": validate_memory_seeds(source.get("memory_seeds")),
            "expected_observations": dict(expected)}


def _in_range(value, rule):
    if not isinstance(rule, dict):
        return False, "范围规则必须是对象"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False, "观测值不是数值"
    if "min" in rule and number < float(rule["min"]):
        return False, f"{number} < min {rule['min']}"
    if "max" in rule and number > float(rule["max"]):
        return False, f"{number} > max {rule['max']}"
    return True, ""


def evaluate_observations(manifest, expected):
    """纯评估回放机制边界；不解释回复质量，也不调整任何参数。"""
    failures = []
    rules = dict(expected or {})
    allowed = {"turn_count", "history_turn_count", "catch_up_count", "memory_seed_count",
               "final_state", "turns"}
    unknown = set(rules) - allowed
    if unknown:
        failures.append("未知观测规则: " + ", ".join(sorted(unknown)))
    if "turn_count" in rules and int(rules["turn_count"]) != int(manifest["turn_count"]):
        failures.append(f"turn_count={manifest['turn_count']}，预期 {rules['turn_count']}")
    for field in ("history_turn_count", "catch_up_count", "memory_seed_count"):
        if field in rules and int(rules[field]) != int(manifest.get(field, 0)):
            failures.append(f"{field}={manifest.get(field, 0)}，预期 {rules[field]}")
    final_state = (manifest.get("turns") or [{}])[-1].get("state") or {}
    for field, rule in (rules.get("final_state") or {}).items():
        ok, reason = _in_range(final_state.get(field), rule)
        if not ok:
            failures.append(f"final_state.{field}: {reason}")
    by_index = {int(turn["index"]): turn for turn in manifest.get("turns") or []}
    for raw in rules.get("turns") or []:
        rule = dict(raw or {})
        index = rule.pop("index", None)
        turn = by_index.get(index)
        if turn is None:
            failures.append(f"turn {index} 不存在")
            continue
        recall = turn.get("recall") or {}
        for field in ("candidate_count", "actual_reference_count", "suppressed_count"):
            if field not in rule:
                continue
            ok, reason = _in_range(recall.get(field), rule[field])
            if not ok:
                failures.append(f"turn {index}.recall.{field}: {reason}")
    return {"passed": not failures, "failures": failures}


def replay_signature(manifest):
    """对不含原文的回放摘要签名，供 T0 判断同输入/时钟是否逐值一致。"""
    stable = {"schema_version": manifest.get("schema_version"),
              "start_ms": manifest.get("start_ms"),
              "history_turn_count": manifest.get("history_turn_count"),
              "catch_up_count": manifest.get("catch_up_count"),
              "memory_seed_count": manifest.get("memory_seed_count"),
              "turn_count": manifest.get("turn_count"), "operations": manifest.get("operations"),
              "turns": manifest.get("turns")}
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def run_time_replay(*, scenario, output_base_dir, make_runner, setup_card=None):
    """在独立目录顺序回放场景并返回不含用户原文的摘要。

    ``make_runner(store)`` 由入口注入已配置的 Runner；若提供 ``setup_card`` 则会在
    回放档案内初始化该角色。目录固定为 ``<output_base_dir>/<scenario_id>``，已有目录
    会拒绝覆写，防止把一次实验误当成可重复修改的真实会话。
    """
    spec = validate_scenario(scenario)
    root = os.path.abspath(output_base_dir)
    replay_dir = os.path.abspath(os.path.join(root, spec["scenario_id"]))
    if os.path.commonpath([root, replay_dir]) != root:
        raise ValueError("回放目录必须位于 output_base_dir 内")
    if os.path.exists(replay_dir):
        raise FileExistsError(f"回放目录已存在，拒绝覆写: {replay_dir}")
    os.makedirs(root, exist_ok=True)
    store = UserStore(spec["scenario_id"], base_dir=root)
    runner = make_runner(store)
    if setup_card is not None:
        runner.setup_from_card(dict(setup_card))

    turns, operations = [], []
    for seed in apply_memory_seeds(runner, spec["memory_seeds"], spec["start_ms"]):
        operations.append({"phase": "seed", "kind": "memory_seed", **seed})
    for index, step in enumerate(spec["history"], start=1):
        now_ms = spec["start_ms"] + step["offset_ms"]
        result = runner.on_user_message(step["text"], now_ms=now_ms)
        telemetry = result.get("telemetry") or {}
        operations.append({"phase": "history", "index": index, "kind": "user_message",
                           "now_ms": now_ms, "turn_id": result.get("turn_id"),
                           "state": telemetry.get("state") or {}})

    for index, step in enumerate(spec["steps"], start=1):
        now_ms = spec["start_ms"] + step["offset_ms"]
        if step["kind"] == "catch_up":
            result = runner.catch_up(now_ms=now_ms, parallel=False)
            operations.append({"phase": "steps", "index": index, "kind": "catch_up",
                               "now_ms": now_ms, "advanced": bool(result.get("advanced")),
                               "sent": bool(result.get("sent")),
                               "message_count": len(result.get("messages") or []),
                               "reason_rule": str((result.get("reason") or {}).get("rule") or ""),
                               "state": project_state(runner.state)})
            continue
        result = runner.on_user_message(step["text"], now_ms=now_ms)
        telemetry = result.get("telemetry") or {}
        turns.append({
            "index": index,
            "now_ms": now_ms,
            "turn_id": result.get("turn_id"),
            "action_id": result.get("action_id"),
            "recall": telemetry.get("recall") or {},
            "state": telemetry.get("state") or {},
            "conversation": telemetry.get("conversation") or {},
            "uncertainties": list(telemetry.get("uncertainties") or []),
        })
        operations.append({"phase": "steps", "index": index, "kind": "user_message",
                           "now_ms": now_ms, "turn_id": result.get("turn_id"),
                           "state": telemetry.get("state") or {}})

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": "time_replay_manifest",
        "scenario_id": spec["scenario_id"],
        "start_ms": spec["start_ms"],
        "memory_seed_count": len(spec["memory_seeds"]),
        "history_turn_count": len(spec["history"]),
        "catch_up_count": sum(1 for step in spec["steps"] if step["kind"] == "catch_up"),
        "turn_count": len(turns),
        "operations": operations,
        "turns": turns,
        "isolation": "new_store_only",
    }
    manifest["assertions"] = evaluate_observations(
        manifest, spec["expected_observations"])
    manifest["replay_signature"] = replay_signature(manifest)
    store.write_json(os.path.join(store.user_dir, "replay_manifest.json"), manifest)
    return {"store_dir": store.user_dir, "manifest": manifest}
