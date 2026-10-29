"""自由 NPC × 模拟用户会话的测试编排核心。

这里只编排可见文本的交替，不定义 NPC 状态、用户心理或评估分数：NPC 仍完全由 ``Runner``
处理，模拟用户仍完全由测试 adapter 处理。隐藏模拟条件只作为 ``user_reactor.react`` 的参数，
永不传入 Runner 或写入本模块的公开结果。
"""
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional


MAX_NPC_TURNS = 20
MAX_TEST_GAP_MS = 86_400_000


@dataclass(frozen=True)
class FreeDialogueConfig:
    """一次无人介入会话的可复查上限；``max_npc_turns`` 以 NPC 输出数计。"""

    max_npc_turns: int = 10
    min_npc_turns_before_stop: int = 1
    start_ms: int = 10_000
    # 测试专用的显式消息抵达时间；None 才使用模拟用户返回的 delay_ms。
    # 这不是产品侧的“打字时间”推断，不能进入 Runner、人格或 prompt。
    inter_message_gaps_ms: Optional[tuple] = None

    def __post_init__(self):
        if not 1 <= int(self.max_npc_turns) <= MAX_NPC_TURNS:
            raise ValueError("max_npc_turns 必须在 1 到 %d 之间" % MAX_NPC_TURNS)
        if not 1 <= int(self.min_npc_turns_before_stop) <= int(self.max_npc_turns):
            raise ValueError("min_npc_turns_before_stop 必须在 1 到 max_npc_turns 之间")
        if int(self.start_ms) < 0:
            raise ValueError("start_ms 不能为负数")
        schedule = self.inter_message_gaps_ms
        if schedule is None:
            return
        if not isinstance(schedule, tuple) or len(schedule) != int(self.max_npc_turns) - 1:
            raise ValueError("inter_message_gaps_ms 必须是长度为 max_npc_turns - 1 的元组")
        for gap in schedule:
            if isinstance(gap, bool) or int(gap) != gap or not 0 <= int(gap) <= MAX_TEST_GAP_MS:
                raise ValueError("inter_message_gaps_ms 的每项必须是 0 到 86400000 的整数")


def _visible(role: str, text: str) -> Dict[str, str]:
    return {"role": role, "text": str(text or "")}


def _wall_clock_receipt(npc_result):
    """把本轮时间总线收据投影到测试 manifest，不把状态/词元当作测试事实改写。"""
    report = dict((npc_result or {}).get("report") or {})
    events = [dict(event) for event in report.get("events") or []
              if event.get("rule") in {"root_time_advanced", "rebound_done"}]
    return {"dt_ms": int(report.get("dt_ms") or 0), "events": events}


def _checkpoint(callback, *, status, termination, transcript, rounds, **_ignored):
    if callback is not None:
        callback({
            "status": status,
            "termination": termination,
            "transcript": [dict(item) for item in transcript],
            "rounds": [dict(item) for item in rounds],
        })


def run_free_dialogue(*, runner, user_reactor, initial_user_text: str,
                      public_condition: Optional[Dict[str, object]],
                      hidden_oracle: Dict[str, object],
                      config: FreeDialogueConfig = FreeDialogueConfig(),
                      checkpoint: Optional[Callable[[Dict[str, object]], None]] = None):
    """运行到 NPC 回合上限、用户主动停止或显式失败。

    ``initial_user_text`` 是唯一人工输入。最后一个 NPC 回合不再请求用户回复，因此最终
    transcript 以 NPC 输出收束；N 个 NPC 回合最多使用 ``2N-1`` 次生成调用。
    """
    if runner is None or not hasattr(runner, "on_user_message"):
        raise TypeError("runner 必须提供 on_user_message()")
    if user_reactor is None or not hasattr(user_reactor, "react"):
        raise TypeError("user_reactor 必须提供 react()")
    opening = str(initial_user_text or "").strip()
    if not opening:
        raise ValueError("initial_user_text 不能为空")
    if not isinstance(hidden_oracle, dict):
        raise TypeError("hidden_oracle 必须是对象")

    transcript: List[Dict[str, object]] = [_visible("user", opening)]
    rounds: List[Dict[str, object]] = []
    now_ms = int(config.start_ms)
    current_text = opening
    current_delay = 0

    for number in range(1, int(config.max_npc_turns) + 1):
        try:
            npc_result = runner.on_user_message(current_text, now_ms=now_ms,
                                                delay_ms=current_delay)
        except Exception as exc:
            result = {
                "status": "failed", "termination": "npc_generation_failure",
                "failure": {"error_type": type(exc).__name__, "message": str(exc)[:240]},
                "transcript": transcript, "rounds": rounds, "npc_turns": number - 1,
            }
            _checkpoint(checkpoint, **result)
            return result
        npc_text = str(npc_result.get("reply") or "").strip()
        if not npc_text:
            result = {
                "status": "failed", "termination": "empty_npc_reply",
                "transcript": transcript, "rounds": rounds,
                "npc_turns": number - 1,
            }
            _checkpoint(checkpoint, **result)
            return result
        transcript.append(_visible("npc", npc_text))
        row = {
            "npc_turn": number,
            "npc_turn_id": npc_result.get("turn_id"),
            "npc_action_id": npc_result.get("action_id"),
            "npc_text": npc_text,
            "wall_clock": _wall_clock_receipt(npc_result),
        }
        rounds.append(row)

        # 让最终输出停在 NPC 侧，既符合“给一次输入、读取最终输出”，也不产生一条
        # 无法由 Runner 归因的尾部用户输入。
        if number == int(config.max_npc_turns):
            result = {
                "status": "completed", "termination": "npc_turn_budget_reached",
                "transcript": transcript, "rounds": rounds, "npc_turns": number,
            }
            _checkpoint(checkpoint, **result)
            return result

        react_kwargs = {
            "npc_text": npc_text, "visible_history": transcript,
            "public_condition": dict(public_condition or {}), "hidden_oracle": hidden_oracle,
        }
        # 最低回合只约束测试模拟器，不改 NPC/Runner；模拟器仍必须自行生成下一条自然文本。
        if number < int(config.min_npc_turns_before_stop):
            react_kwargs["allow_stop"] = False
        user_result = user_reactor.react(**react_kwargs)
        simulator = user_result.as_manifest_record()
        row["simulator"] = simulator
        if user_result.status != "ok":
            result = {
                "status": "failed", "termination": "simulated_user_failure",
                "failure": {"error_type": user_result.error_type},
                "transcript": transcript, "rounds": rounds, "npc_turns": number,
            }
            _checkpoint(checkpoint, **result)
            return result
        if user_result.signal == "stop":
            if number < int(config.min_npc_turns_before_stop):
                result = {
                    "status": "failed", "termination": "simulated_user_minimum_turn_violation",
                    "failure": {"error_type": "simulated_user_stop_before_minimum"},
                    "transcript": transcript, "rounds": rounds, "npc_turns": number,
                }
                _checkpoint(checkpoint, **result)
                return result
            result = {
                "status": "completed", "termination": "simulated_user_stop",
                "transcript": transcript, "rounds": rounds, "npc_turns": number,
            }
            _checkpoint(checkpoint, **result)
            return result

        current_text = str(user_result.text or "").strip()
        if not current_text:
            result = {
                "status": "failed", "termination": "simulated_user_empty_text",
                "transcript": transcript, "rounds": rounds, "npc_turns": number,
            }
            _checkpoint(checkpoint, **result)
            return result
        model_delay = int(user_result.delay_ms or 0)
        schedule = config.inter_message_gaps_ms
        if schedule is None:
            current_delay, timing_source = model_delay, "simulated_user"
        else:
            current_delay, timing_source = int(schedule[number - 1]), "scheduled_test_clock"
        # 模型报出的 delay 和实际喂给 Runner 的抵达时间都要留档，不能把测试时钟
        # 伪装成用户模型的行为，也不能让它作为产品输入回流。
        simulator.update({"model_delay_ms": model_delay,
                          "effective_delay_ms": current_delay,
                          "timing_source": timing_source})
        now_ms += current_delay
        transcript.append(_visible("user", current_text))
        _checkpoint(checkpoint, status="running", termination=None,
                    transcript=transcript, rounds=rounds)

    raise AssertionError("max_npc_turns 循环必须在循环内返回")
