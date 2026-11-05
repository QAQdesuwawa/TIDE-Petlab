"""Place already-delivered current facts in the current turn, without reselecting."""
from dataclasses import replace
import hashlib

LEGACY_VERSION = 'desktop-pet-current-turn-layout-v1'
PROPERTY_VERSION = 'desktop-pet-current-turn-layout-v2-property-owners'
SOURCE_VERSION = 'desktop-pet-current-turn-layout-v3-property-sources'
VERSION = 'desktop-pet-current-turn-layout-v4-occurrence'


def place_current_input(assembly, packet):
    from agents.responder import _PACKET_LANE_TITLES, _render_packet_card

    delivered = {row['card_id'] for row in assembly.delivered_materials}
    blocks = []
    for card in packet.sections['current_input']:
        if card.card_id not in delivered:
            continue
        blocks.append(_PACKET_LANE_TITLES['current_input'] + '：\n' + _render_packet_card(card))
        blocks.extend(_render_packet_card(child) for child in card.subcards
                      if child.subcard_id in delivered)
    if not blocks:
        return assembly
    block = '\n\n'.join(blocks)
    # Only move the exact renderer output, not text selected by names/keywords.
    framed = '\n\n' + block
    if assembly.system.count(framed) != 1:
        raise ValueError('current input renderer boundary differs')
    system = assembly.system.replace(framed, '', 1)
    user = block
    return replace(assembly, system=system, user=user,
                   prompt_fingerprint=hashlib.sha256((system + '\0' + user).encode('utf-8')).hexdigest())
