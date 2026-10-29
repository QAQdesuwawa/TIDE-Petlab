"""Intentional empty voice, separate from failed or executed speech."""
from dataclasses import dataclass
from framework.model_api import thaw_model_data

@dataclass(frozen=True)
class QuietRealization:
    request_id: str
    evidence: object

class SpeechNotSelected(Exception):
    def __init__(self, decision):
        super().__init__('no_speech_selected')
        self.decision=decision

def quiet_candidate(request,candidate):
    from model_packages.desktop_pet_v1.writer import OPTIONAL_IDLE_VERSION,MEMORY_MARKER_SUFFIX,PRIVATE_EXPRESSION_VERSION
    if not request.control.get('action_sequence_contract') and request.control['version'].removesuffix(MEMORY_MARKER_SUFFIX)not in {OPTIONAL_IDLE_VERSION,PRIVATE_EXPRESSION_VERSION}:return None
    if candidate.candidate_payload.get('contents')!=():return None
    if candidate.realization_evidence.get('speech_decision')!='silent':raise ValueError('empty candidate lacks quiet decision')
    if request.control.get('action_sequence_contract'):
        from model_packages.desktop_pet_v1.action_sequence import validate_candidate
        validate_candidate(request,candidate.candidate_payload)
    return QuietRealization(request.readonly_facts['writer_packet']['writer_request_id'],
        thaw_model_data({'request_fingerprint':request.fingerprint,'candidate':candidate.to_record()}))
