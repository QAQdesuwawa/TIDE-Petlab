"""Optional Actor command adapter for preference and feedback workflows.

The adapter owns product command names and their Runner calls.  The Actor only
serialises injected commands and applies the adapter's objective projections;
without this adapter the commands do not exist.
"""

from __future__ import annotations


PREFERENCE_COMMAND_KINDS = frozenset((
    "set_preference",
    "set_preference_weights",
    "mark_preference_declared",
    "record_feedback",
    "record_cadence_feedback",
    "refine_preference",
))


class PreferenceCalibrationActorCommands:
    kinds = PREFERENCE_COMMAND_KINDS

    @staticmethod
    def execute(runner, kind, payload):
        if kind == "set_preference":
            return bool(runner.apply_preference(payload.get("text", "")))
        if kind == "set_preference_weights":
            runner.apply_preference_weights(payload.get("weights") or {})
            return True
        if kind == "mark_preference_declared":
            return bool(runner.mark_preference_declared())
        if kind == "record_feedback":
            return runner.record_explicit_feedback(
                payload.get("feedback", ""), now_ms=payload.get("now_ms"))
        if kind == "record_cadence_feedback":
            return runner.record_cadence_feedback(
                payload.get("feedback", ""),
                now_ms=payload.get("now_ms"),
                presentation=payload.get("presentation"),
            )
        if kind == "refine_preference":
            return bool(runner.refine_preference())
        raise ValueError(f"未知偏好扩展命令: {kind}")

    @staticmethod
    def consume_result(*, kind, result, emit, log):
        if kind in {"set_preference", "set_preference_weights"}:
            emit("preference_done", {"ok": bool(result)})
            log(kind, {"ok": bool(result)})
            return
        if kind == "mark_preference_declared":
            emit("preference_done", {"ok": bool(result), "skipped": True})
            log(kind, {"ok": bool(result)})
            return
        if kind == "record_feedback":
            record = result if isinstance(result, dict) else {}
            emit("feedback_recorded", {
                "ok": bool(record),
                "feedback": record.get("feedback"),
                "attribution_confidence": record.get(
                    "attribution_confidence", "none"),
            })
            log(kind, {
                "ok": bool(record),
                "attribution": record.get("attribution_confidence", "none"),
            })
            return
        if kind == "record_cadence_feedback":
            record = result if isinstance(result, dict) else {}
            emit("cadence_feedback_recorded", {
                "ok": bool(record),
                "feedback": record.get("feedback"),
                "attribution_confidence": record.get(
                    "attribution_confidence", "none"),
            })
            log(kind, {
                "ok": bool(record),
                "attribution": record.get("attribution_confidence", "none"),
            })
            return
        if kind == "refine_preference":
            emit("refine_done", {"ok": bool(result)})
            if result:
                log("refine_preference", {})
            return
        raise ValueError(f"未知偏好扩展结果: {kind}")

    @staticmethod
    def follow_up_commands(*, parent_kind, result):
        if (
            parent_kind in {"user_message", "formal_required_reply"}
            and isinstance(result, dict)
            and result.get("refine_due")
        ):
            return (("refine_preference", {}, 1),)
        return ()


__all__ = (
    "PREFERENCE_COMMAND_KINDS",
    "PreferenceCalibrationActorCommands",
)
