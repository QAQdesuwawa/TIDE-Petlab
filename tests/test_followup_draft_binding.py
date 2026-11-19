from concurrent.futures import Future
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from petlab_app.providers import PROVISIONAL_WRITER_SUFFIX
from petlab_app.retired_transport import drain
from product_adapters.desktop_pet_v1.realization import PetRealizationService, _FollowupDraft


@pytest.mark.parametrize("changed", ["text", "target", "audience", "memory", None])
def test_followup_transport_reuse_and_retirement(changed):
    writer = object.__new__(PetRealizationService)
    product = NS()
    facts = {"prompt_delivery": {"system": "role", "user": "words", "memory_aliases": {"R1": "fact1"}},
             "audience_envelope": {"readers": ["peer"]}}
    first = NS(source_fact_ref="input", target_ref="peer", control={"version": "v"}, readonly_facts=facts)
    current = deepcopy(first)
    if changed == "text":
        current.readonly_facts["prompt_delivery"]["user"] = "updated"
    elif changed == "memory":
        current.readonly_facts["prompt_delivery"]["memory_aliases"]["R1"] = "fact2"
    elif changed == "target":
        current.target_ref = "visitor"
    elif changed == "audience":
        current.readonly_facts["audience_envelope"]["readers"].append("visitor")
    writer.temperature = 0
    writer.gateway = NS(transport_for=lambda _: NS(payload=lambda **kwargs: kwargs))
    _, messages = writer._provider_fields(first)
    receipts = []
    def finish():
        receipts.append("sealed")
        return NS(status="parsed")
    call = NS(request=NS(codec_contract="v"+PROVISIONAL_WRITER_SUFFIX,
                         request_payload=dict(messages=messages, temperature=0, require_json=True)), finish=finish)
    future = Future()
    writer._followup_writer = _FollowupDraft("input", product, first, call, future)
    if changed is None:
        future.set_result(None)
        assert writer.finish_followup("input", current) is call
    else:
        assert writer.finish_followup("input", current) is None
        assert writer._followup_writer is None
        drain(product)
        assert not receipts
        future.set_result(None)
        drain(product)
        drain(product)
    assert receipts == ["sealed"]


@pytest.mark.parametrize(("kind", "deferred", "expected"), [
    (None, True, None), ("deferred_reply", True, 123),
    (None, False, 123), ("deferred_reply", False, None),
])
def test_comparison_clock_tracks_original_delivery_scope(kind, deferred, expected):
    from product_adapters.desktop_pet_v1.realization import _comparison_clock
    request = NS(readonly_facts={"expression_delivery": {"source_kind": kind},
                                 "prompt_delivery": {"processing_ms": 123}})
    assert _comparison_clock(request, deferred) == expected


def test_deferred_proof_reuse_ends_before_transport_and_playback(monkeypatch):
    from product_adapters.desktop_pet_v1 import deferred_reply
    app = NS(runtime=NS(session=object()))
    monkeypatch.setattr(deferred_reply, "_authorize", lambda app, event: object())
    with deferred_reply.assembly_scope(app, "followup"):
        proof = deferred_reply.authorize(app, "followup")
        assert deferred_reply.authorize(app, "followup") is proof
        assert deferred_reply.authorize(app, "other") is not proof
        app.runtime.session = object()
        settled = deferred_reply.authorize(app, "followup")
        assert settled is not proof
    assert deferred_reply.authorize(app, "followup") is not settled
def test_self_settlement_only_relaxes_an_existing_weak_direction():
    import json
    from product_adapters.desktop_pet_v1.followup_revision import compatible_messages
    def material(direction, weight="微弱", words="original"):
        rows = [{"主体":"npc:a","关系对象":"user","心理状态词元":[
            {"kind":"continuous_direction","axis":"exchange","orientation":direction,"prominence":weight}]}]
        system = "【本角色各条关系的当前心理状态】\n本轮表达资源对应：user。\n" + json.dumps(rows,ensure_ascii=False)
        if direction=="neutral":
            system += "\n回应对象：此前已收到且尚未回应的原问题，以当前输入栏原文为准。"
        return [{"role":"system","content":system},{"role":"user","content":words}]
    assert compatible_messages(material("positive"),material("neutral"))
    assert not compatible_messages(material("positive"),material("negative"))
    assert not compatible_messages(material("positive","显著"),material("neutral","显著"))
    assert not compatible_messages(material("positive"),material("neutral",words="changed"))

@pytest.mark.parametrize(("parent","ordinal","same_source","expected"),[
    ("action:previous",12,True,True), ("action:other",12,True,False),
    ("action:previous",13,True,False), ("action:previous",12,False,False)])
def test_revision_requires_the_exact_interrupted_parent(monkeypatch,parent,ordinal,same_source,expected):
    from product_adapters.desktop_pet_v1 import followup_revision
    monkeypatch.setattr(followup_revision,"compatible_messages",lambda a,b:True)
    monkeypatch.setattr(PetRealizationService,"_provider_fields",staticmethod(lambda r:("v",[])))
    old=NS(source_runtime_ordinal=11,source_settlement_fingerprint="original")
    new=NS(source_runtime_ordinal=ordinal,source_settlement_fingerprint="original" if same_source else "other",
           readonly_facts={"expression_delivery":{"planning_view":{"source_ref":parent}}})
    pending=NS(action_id="action:previous",request=old)
    assert PetRealizationService._self_output_revision(pending,new) is expected


@pytest.mark.parametrize(("before", "after", "expected"), [
    (None, None, 125), (None, {"progress":{"state":"running"}}, None),
    ({"progress":{"state":"running"}}, None, None),
    ({"progress":{"state":"running"}}, {"progress":{"state":"completed"}}, None),
    ({"progress":{"state":"completed"},"fingerprint":"a"},
     {"progress":{"state":"completed"},"fingerprint":"b"}, 125),
])
def test_followup_clock_uses_current_visible_execution_progress(before, after, expected):
    from product_adapters.desktop_pet_v1.realization import _frontier_comparison_clock
    prior=NS(readonly_facts={"execution_frontier":before})
    assert _frontier_comparison_clock(125, prior, after)==expected

def test_gesture_occurrence_is_stable_only_for_same_actual_start():
    from product_adapters.desktop_pet_v1.execution_frontier import _gesture_occurrence
    content=NS(kind="action",semantic_key="play_tail",parameters={},recipient_refs=("user",))
    first=_gesture_occurrence(content,"started",100)
    assert first and first==_gesture_occurrence(content,"completed",100)
    assert first!=_gesture_occurrence(content,"completed",101)
    assert _gesture_occurrence(content,"interrupted",100) is None
    assert _gesture_occurrence(NS(**{**vars(content),"semantic_key":"handover"}),"completed",100) is None

@pytest.mark.parametrize("change",["speech","contact","interrupt"])
def test_mixed_or_interrupted_execution_retains_full_delivery(change):
    from product_adapters.desktop_pet_v1.execution_frontier import _gesture_occurrence, _occurrence_view
    ordinary={"实际进度":[{"内容":"原回执"}]}
    gesture=NS(kind="action",semantic_key="nod",recipient_refs=["user"])
    changed=NS(kind="speech" if change=="speech" else "action",
               semantic_key="handover" if change=="contact" else "nod",recipient_refs=["user"])
    rows=[("a",_gesture_occurrence(gesture,"started",10)),
          ("b",_gesture_occurrence(changed,"interrupted" if change=="interrupt" else "completed",12))]
    assert _occurrence_view(rows,ordinary) is ordinary
