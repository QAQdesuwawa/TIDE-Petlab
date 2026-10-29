"""Implementation choice for an already selected package behavior, not eligibility."""
from .behavior import select_idle,selected_action

LANGUAGE='language'
LOCAL='local_action'

def route_for(selection):
    if selection is None:return LANGUAGE  # existing direct/legacy language path
    if selection.get('mode')=='symbolic':return LOCAL if selected_action(selection) else LANGUAGE
    if selection==select_idle(eligible=True,mode='speech'):return LANGUAGE
    if selection==select_idle(eligible=True,mode='play_tail'):return LOCAL
    if selection==select_idle(eligible=True,mode='play_tail_composite'):return LOCAL
    if selection==select_idle(eligible=True,mode='play_tail_speech'):return LOCAL
    raise ValueError('no registered realization for frozen package selection')
