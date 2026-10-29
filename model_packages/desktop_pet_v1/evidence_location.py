"""Resolve quoted evidence locally; no psychological values or thresholds."""
from copy import deepcopy


def locate_evidence(raw, source):
    result = deepcopy(raw)
    frame = result.get('reference_frame') if isinstance(result, dict) else None
    if not isinstance(frame, dict):
        return result
    claims = frame.get('relation_claims')
    resolved = []
    reason = 'missing_evidence_text'
    if isinstance(claims, list) and claims:
        for claim in claims:
            quote = claim.get('evidence_text') if isinstance(claim, dict) else None
            if not isinstance(quote, str) or not quote.strip():
                break
            start = source.find(quote)
            if start < 0:
                reason = 'evidence_not_found'
                break
            if source.find(quote, start + 1) >= 0:
                reason = 'ambiguous_evidence_text'
                break
            local = {k:v for k,v in claim.items() if k not in {'evidence_text','evidence_span'}}
            local['evidence_span'] = [start, start + len(quote)]
            resolved.append(local)
        else:
            frame['relation_claims'] = resolved
            return result
    frame.update(delivery_scope='unresolved', relation_claims=[],
                 resolver_source='local_evidence_locator', fallback_reason=reason)
    return result
