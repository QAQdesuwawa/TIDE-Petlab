"""Keep required context when selected memory exercises its overflow policy.

This is a rendered-view budget policy, not a recall or Runtime change.
The required base must independently fit the configured technical budget.
"""
from dataclasses import replace
from agents.responder import compose_responder_packet
from framework.model_api import model_data_fingerprint

LANES=frozenset({'current_input','persona','conversation','expression','form'})

def _view(packet,sections):
    provenance=any(c.provenance for cards in sections.values() for c in cards)
    subcards=any(c.subcards for cards in sections.values() for c in cards)
    version='writer-packet-v4-card-provenance' if provenance else 'writer-packet-v3-subcards' if subcards else 'writer-packet-v2'
    result=replace(packet,sections=sections,version=version)
    return replace(result,packet_fingerprint=model_data_fingerprint(result.content_record()))

def preserve_required(packet,*,cap,core_text):
    base=_view(packet,{lane:tuple(replace(c,budget_policy='normal') for c in cards) if lane in LANES else ()
                       for lane,cards in packet.sections.items()})
    check=compose_responder_packet(base,budget_chars=cap,return_assembly=True,core_text=core_text)
    admitted={r['card_id'] for r in check.delivered_materials}
    missing=[c.card_id for cards in base.sections.values() for c in cards if c.card_id not in admitted]
    if check.core_over_budget or missing:
        raise ValueError('required Writer materials excluded by prompt budget: '+', '.join(missing))
    # The ordinary renderer retains ordering and records the existing overflow
    # exception. Subcards still use their original bounded admission path.
    return _view(packet,{lane:tuple(replace(c,budget_policy='preserve_selection') for c in cards) if lane in LANES else cards
                         for lane,cards in packet.sections.items()})


def preserves_required_selection(version):
    """Shared policy for compilation and supplemental-card budget accounting."""
    from . import prompt_delivery as versions
    return version.removesuffix('-persona-v1').removesuffix('-stationary-v1') in {
        versions.REQUIRED_VERSION, versions.REQUIRED_MARKER_VERSION,
        versions.ACTUAL_VERSION, versions.ACTUAL_MARKER_VERSION,
        versions.SITUATED_VERSION, versions.SITUATED_MARKER_VERSION,
        versions.RELATION_VERSION, versions.RELATION_MARKER_VERSION,
    }


def supplementary_budget(delivery, configured):
    """Carry only the base renderer's explicit retained-selection overflow."""
    if not preserves_required_selection(delivery['version']):
        return configured
    excess=max(0,len(delivery['system'])+len(delivery['user'])-delivery['budget_chars'])
    if excess and (delivery['core_over_budget'] or not delivery['budget_overflow_materials']):
        raise ValueError('unaccounted base Writer overflow')
    return configured+excess
