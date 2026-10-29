"""Typed human interaction carried by the existing Conversation source."""
from model_packages.desktop_pet_v1.events import Content, Interaction

VERSION='pet-participant-human-interaction-v1'


def content(source):
    event=source['event']
    item=Content.from_record(source['human_content'])
    if (not isinstance(item,Interaction) or item.private
        or source['origin']!={'kind':'human','participant_id':event['sender_participant_id']}
        or event['sender_action_id'] is not None
        or event['text']!=item.as_text()
        or set(item.recipient_refs)!=set(event['addressed_participant_ids'])):
        raise ValueError('human interaction differs from its committed source')
    return item
