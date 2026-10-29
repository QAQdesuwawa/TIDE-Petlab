"""AUD-394 task-local forward-memory witness regressions."""
from __future__ import annotations

from model_packages.policy_transaction_v1.memory import (
    MEMORY_EVENT_VERSION,
    aggregate_experience_witnesses,
    read_historical_influence,
    select_task_history,
)


def _event(
    event_id,
    ordinal,
    source_kind,
    *,
    session_id="session:one",
    task_scope_ref="task:one",
    reference_slots=(),
    exact_tool_id=None,
    effectiveness="not_applicable",
    episode_ref=None,
):
    return {
        "version": MEMORY_EVENT_VERSION,
        "event_id": event_id,
        "session_id": session_id,
        "task_scope_ref": task_scope_ref,
        "ordinal": ordinal,
        "source_kind": source_kind,
        "reference_slots": list(reference_slots),
        "exact_tool_id": exact_tool_id,
        "effectiveness": effectiveness,
        "episode_ref": episode_ref,
    }


def _events():
    return [
        _event("e1", 1, "user_text", reference_slots=("request.operation",)),
        _event("e2", 2, "assistant_text", reference_slots=("constraints",)),
        _event(
            "e3", 3, "agent_tool_call", exact_tool_id="lookup_entity",
            episode_ref="episode:1",
        ),
        _event(
            "e4", 4, "tool_result", exact_tool_id="lookup_entity",
            effectiveness="effective", episode_ref="episode:1",
        ),
        _event(
            "e5", 5, "user_tool_result", exact_tool_id=None,
            effectiveness="not_applicable", episode_ref="episode:1",
        ),
        _event(
            "other-task", 6, "tool_result", task_scope_ref="task:two",
            exact_tool_id="lookup_entity", effectiveness="effective",
        ),
        _event(
            "other-session", 7, "tool_result", session_id="session:two",
            exact_tool_id="lookup_entity", effectiveness="effective",
        ),
    ]


def test_selection_is_exact_task_local_and_preserves_heterogeneous_event_ids():
    recall = select_task_history(
        scope={"session_id": "session:one", "task_scope_ref": "task:one"},
        query={
            "reference_slots": ["request.operation"],
            "exact_tool_ids": ["lookup_entity"],
        },
        candidate_events=_events(),
    )

    assert recall["selected_event_ids"] == ["e1", "e3", "e4"]
    assert "other-task" not in recall["selected_event_ids"]
    assert "other-session" not in recall["selected_event_ids"]
    assert "body" not in str(recall)
    assert recall["selection_rule"] == "task-local-exact-structured-reference"


def test_only_committed_agent_tool_results_become_experience():
    events = _events() + [
        _event(
            "e6", 6, "tool_result", exact_tool_id="change_entity",
            effectiveness="ineffective", episode_ref="episode:2",
        ),
    ]
    recall = select_task_history(
        scope={"session_id": "session:one", "task_scope_ref": "task:one"},
        query={
            "reference_slots": [],
            "exact_tool_ids": ["lookup_entity", "change_entity"],
        },
        candidate_events=events,
    )
    influence = read_historical_influence(
        memory_recall=recall,
        historical_events=events,
        exact_tool_ids=["lookup_entity", "change_entity"],
    )
    aggregate = aggregate_experience_witnesses(
        historical_influence=influence,
    )

    assert aggregate["experience_witnesses"] == [
        {
            "tool_id": "lookup_entity",
            "disposition": "support",
            "source_episode_refs": ["episode:1"],
            "source_event_refs": ["e4"],
        },
        {
            "tool_id": "change_entity",
            "disposition": "conflict",
            "source_episode_refs": ["episode:2"],
            "source_event_refs": ["e6"],
        },
    ]
    assert "updates" not in str(aggregate)
    assert "state_candidate" not in str(aggregate)
    assert aggregate["state_commit_authority"] is False


def test_unknown_result_is_not_promoted_to_positive_experience():
    events = [
        _event(
            "unknown", 1, "tool_result", exact_tool_id="lookup_entity",
            effectiveness="unknown", episode_ref="episode:unknown",
        ),
    ]
    recall = select_task_history(
        scope={"session_id": "session:one", "task_scope_ref": "task:one"},
        query={"reference_slots": [], "exact_tool_ids": ["lookup_entity"]},
        candidate_events=events,
    )
    influence = read_historical_influence(
        memory_recall=recall,
        historical_events=events,
        exact_tool_ids=["lookup_entity"],
    )
    aggregate = aggregate_experience_witnesses(
        historical_influence=influence)

    assert aggregate["experience_witnesses"][0]["disposition"] == "insufficient"


def test_prior_materialized_result_is_reused_and_only_new_edge_is_added():
    first_events = [
        _event(
            "old-result", 3, "tool_result", exact_tool_id="lookup_entity",
            effectiveness="effective", episode_ref="episode:old",
        ),
    ]
    first_recall = select_task_history(
        scope={"session_id": "session:one", "task_scope_ref": "task:one"},
        query={"reference_slots": [], "exact_tool_ids": ["lookup_entity"]},
        candidate_events=first_events,
    )
    first_influence = read_historical_influence(
        memory_recall=first_recall,
        historical_events=first_events,
        exact_tool_ids=["lookup_entity"],
    )
    prior = aggregate_experience_witnesses(
        historical_influence=first_influence)

    new_events = [
        _event(
            "new-result", 8, "tool_result", exact_tool_id="lookup_entity",
            effectiveness="ineffective", episode_ref="episode:new",
        ),
    ]
    new_recall = select_task_history(
        scope={"session_id": "session:one", "task_scope_ref": "task:one"},
        query={"reference_slots": [], "exact_tool_ids": ["lookup_entity"]},
        candidate_events=new_events,
    )
    new_influence = read_historical_influence(
        memory_recall=new_recall,
        historical_events=new_events,
        exact_tool_ids=["lookup_entity"],
    )
    current = aggregate_experience_witnesses(
        historical_influence=new_influence,
        prior_aggregate=prior,
    )

    witness = current["experience_witnesses"][0]
    assert witness["source_event_refs"] == ["old-result", "new-result"]
    assert witness["disposition"] == "conflict"
    assert current["newly_materialized_event_ids"] == ["new-result"]
    assert current["reused_prior_aggregate_fingerprint"] == prior["fingerprint"]
    assert current["materialized_through_ordinal"] == 8

