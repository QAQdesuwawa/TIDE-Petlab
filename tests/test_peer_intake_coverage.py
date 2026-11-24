from types import SimpleNamespace as NS
import json
import pytest
from petlab_app.multi_actor import MultiPetActorLink
from petlab_app.multi_pet import _key

def fixture(tmp_path):
    def row(identity,phase,at):
        return {'sender_kind':'npc','delivery':{'delivery_id':identity,'delivery_scope':'addressed'},
                'visible':{'parent_event_id':'parent'},'projection':{'source_event_id':identity,
                'sender_participant_id':'npc:a','recipient_participant_id':'npc:b','committed_at_ms':at}}
    a=row('start','started',100);b=row('end','completed',200)
    sources={name:{'visible_manifest':{'items':[{'content_id':'walk','kind':'action','phase':phase,
        'started_at_ms':100,'at_ms':at,'recipient_refs':['npc:b']}]}}
        for name,phase,at in [('start','started',100),('end','completed',200)]}
    app=NS(_product=lambda:{'sources':{}},runtime=NS(session=NS(profile={})),store=NS(path=tmp_path/'session.json'))
    link=object.__new__(MultiPetActorLink);link.product=NS(application=app)
    link.workspace=NS(source_event=lambda identity:sources[identity]);link._pending=[a,b]
    link._refresh=lambda:None;link.flush_outputs=lambda:None;link.local=tmp_path
    link._human_frontier=None
    return link,sources,a,b

@pytest.mark.parametrize("field", ["conversation_id", "membership_version"])
def test_other_projection_cannot_close_start(tmp_path, field):
    link, _, start, end = fixture(tmp_path)
    start["projection"][field] = 1
    end["projection"][field] = 2
    assert link._covered_start(start) == ()


def test_parent_measurement_cannot_cover_another_action_interval(tmp_path):
    from product_adapters.desktop_pet_v1 import EVENT_JOURNAL_KEY
    from petlab_app.multi_pet import _key
    from petlab_app.visible_delivery import FRAGMENT_VERSION
    from petlab_app.visible_measurement import measured_parent

    link, sources, start, end = fixture(tmp_path)
    for seq, row in enumerate((start, end)):
        row["visible"].update(version=FRAGMENT_VERSION, sequence=seq, target_refs=[], contents=[
            {"kind":"action", "content_id":"walk", "recipient_refs":["npc:b"]}])
    link._visible_siblings = {"parent":[start, end]}
    link.product.application.runtime.session.profile[EVENT_JOURNAL_KEY] = {"participant:"+_key("start"):{}}
    sources["end"]["visible_manifest"]["items"][0]["started_at_ms"] = 150
    assert measured_parent(link, end) is None

def test_mixed_visual_receipt_needs_every_visible_interval(tmp_path):
    from copy import deepcopy
    from product_adapters.desktop_pet_v1 import EVENT_JOURNAL_KEY
    from petlab_app.multi_pet import _key
    from petlab_app.visible_delivery import FRAGMENT_VERSION
    from petlab_app.visible_measurement import measured_parent

    link, sources, start, end = fixture(tmp_path)
    for sequence, row in enumerate((start, end)):
        items = sources[row["projection"]["source_event_id"]]["visible_manifest"]["items"]
        items.append(dict(items[0], kind="expression", content_id="face"))
        row["visible"].update(version=FRAGMENT_VERSION, sequence=sequence, target_refs=[],
                              contents=deepcopy(items))
    link._visible_siblings = {"parent": [start, end]}
    link.product.application.runtime.session.profile[EVENT_JOURNAL_KEY] = {"participant:"+_key("start"): {}}
    assert measured_parent(link, end) == "start"
    face = sources["end"]["visible_manifest"]["items"][1]
    face["started_at_ms"] += 1
    assert measured_parent(link, end) is None
    del face["started_at_ms"]
    assert measured_parent(link, end) is None


def row(n,kind='npc',scope='addressed'):
    return {'sender_kind':kind,'delivery':{'delivery_id':str(n),'delivery_scope':scope},
            'projection':{'committed_at_ms':n,'source_event_id':str(n)}}


def test_direct_speech_and_oldest_witness_each_get_a_turn(tmp_path):
    link, _, _, _ = fixture(tmp_path)
    def visible(n, scope, kind, parent=None, sequence=0):
        r = row(n, scope=scope)
        r['projection']['recipient_participant_id'] = 'pet'
        r['visible'] = {'parent_event_id': parent or str(n), 'sequence': sequence,
                        'target_refs': ['pet'] if scope == 'addressed' else ['other'],
                        'contents': [{'kind': kind}]}
        return r
    witness = visible(1, 'observed', 'action')
    direct = visible(10, 'addressed', 'speech')
    link._pending = [witness, direct]
    link._sort_intake()
    assert link._pending[0] == direct
    link._sort_intake()
    assert link._pending.pop(0) == direct
    for n in range(11, 31):
        link._pending.append(visible(n, 'addressed', 'speech'))
        link._sort_intake()
        assert link._pending[0] == witness
    assert link._pending.pop(0) == witness

    prefix = visible(31, 'addressed', 'action', 'shared')
    line = visible(32, 'addressed', 'speech', 'shared', 1)
    link._pending = [visible(30, 'observed', 'action'), line, prefix]
    link._sort_intake()
    assert link._pending.pop(0) == prefix
    link._sort_intake()
    assert link._pending.pop(0) == line

    # With continuous traffic in both lanes the original witnesses drain in
    # occurrence order, even when they predate the latest human frontier.
    from petlab_app.visible_delivery import delivery_order
    link._human_frontier = delivery_order(row(50, 'human'))
    link._pending = [visible(i, 'observed', 'expression') for i in range(40, 50)]
    observed = []
    selected_direct = []
    for n in range(60, 80):
        link._pending.append(visible(n, 'addressed', 'speech'))
        link._sort_intake()
        selected = link._pending.pop(0)['delivery']
        if selected['delivery_scope'] == 'observed':
            observed.append(int(selected['delivery_id']))
        else:
            selected_direct.append(int(selected['delivery_id']))
    assert observed == list(range(40, 50))
    assert len(selected_direct) == 10
