"""Product-side gates for the package-owned semantic reference frame.

The schema and parser live in ``cx_six_seven_v1``.  This compatibility module
retains only product delivery/state eligibility gates used by the legacy path.
"""
from model_packages.cx_six_seven_v1.reference_frame import (
    ReferenceFrame,
    coerce_reference_frame,
)
from core.params import LEDGER
from ops.embeddings import has_exact_ngram_overlap


def referent_delivery_gate(events, text, frame):
    """按引用帧压制不应作为共同经历交付的动态事件。"""
    if frame is None or frame.delivery_scope == "eligible":
        return tuple(events or ()), ()
    delivered, suppressed = [], []
    reason = (
        "reference_scope_conflict"
        if frame.delivery_scope == "third_party"
        else "reference_unresolved"
    )
    for event in tuple(events or ()):
        relationship_event = getattr(event, "source", None) in {
            "memory", "semantic", "ledger",
        }
        if relationship_event and frame.delivery_scope == "third_party":
            suppressed.append({"event_id": event.event_id, "reason": reason})
            continue
        anchored = has_exact_ngram_overlap(
            text,
            getattr(event, "text", ""),
            LEDGER["fallback_min_shared_ngram_chars"],
        )
        if relationship_event and not anchored:
            suppressed.append({"event_id": event.event_id, "reason": reason})
            continue
        delivered.append(event)
    return tuple(delivered), tuple(suppressed)


def referent_state_eligibility_gate(selected_records, frame, text=None):
    """筛掉不能作为当前 NPC 关系状态来源的已选 recall 记录。"""
    records = tuple(selected_records or ())
    if frame is None or frame.delivery_scope == "eligible":
        return records, ()

    allowed, suppressed = [], []
    reason = (
        "reference_scope_conflict"
        if frame.delivery_scope == "third_party"
        else "reference_unresolved"
    )
    for source, record in records:
        relationship_record = str(source) in {"memory", "semantic", "ledger"}
        anchored = has_exact_ngram_overlap(
            str(text or ""),
            str((record or {}).get("text", "")),
            LEDGER["fallback_min_shared_ngram_chars"],
        )
        must_suppress = relationship_record and (
            frame.delivery_scope == "third_party"
            or (frame.delivery_scope == "unresolved" and not anchored)
        )
        if must_suppress:
            try:
                ts = int((record or {}).get("ts", 0))
            except (AttributeError, TypeError, ValueError):
                ts = 0
            suppressed.append({
                "memory_id": "%s:%d" % (str(source), ts),
                "reason": reason,
            })
            continue
        allowed.append((source, record))
    return tuple(allowed), tuple(suppressed)


__all__ = (
    "ReferenceFrame",
    "coerce_reference_frame",
    "referent_delivery_gate",
    "referent_state_eligibility_gate",
)
