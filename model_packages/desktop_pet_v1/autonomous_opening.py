"""Desktop autonomous opening and idle cadence over committed seven-channel evidence."""
import math

from framework.model_api import model_data_fingerprint as fingerprint
from model_packages.cx_six_seven_v1.preference_parameters import CONSTANT_ORDER

LEGACY_VERSION = 'desktop-pet-autonomous-channel-v1'
VERSION = 'desktop-pet-autonomous-channel-v2'
INPUT = 'desktop_idle_evidence'
HALF_LIFE_MS = 40_000
QUIET_INTERVAL_MS = 90_000


def read_context(shared, evidence, *, owner, now):
    from .relation_package import META

    meta = shared['source_state_candidate'][META]
    edge = (owner, meta['active_peer'])
    if (meta['owner_ref'] != owner or evidence['version'] not in (LEGACY_VERSION, VERSION)
            or tuple(evidence['edge']) != edge or evidence['planned_at_ms'] > now):
        raise ValueError('idle channel evidence differs from its relationship or time')
    return edge


def cadence(evidence, *, low, high, now):
    recent = evidence['recent_events']
    bands = evidence['change_scales']
    peak = (0.0, '', '', 0)
    for event in recent:
        age = now - event['at_ms']
        if age < 0:
            raise ValueError('idle cadence source is in the future')
        decay = 2 ** (-age / HALF_LIFE_MS)
        for name, value in event['changes'].items():
            if bands[name]:
                peak = max(peak, (abs(value) / bands[name] * decay, event['id'], name, age))
    strength, source, channel, age = peak
    quiet = min(high, max(low, QUIET_INTERVAL_MS))
    interval = round(low + (quiet - low) / (1 + strength))
    return {'version': evidence['version'], 'strength': strength, 'channel': channel or None,
            'source_event': source or None, 'source_age_ms': age if source else None,
            'after_ms': interval, 'evidence_fingerprint': fingerprint(evidence)}


def assess(shared, evidence, *, owner, now):
    edge = read_context(shared, evidence, owner=owner, now=now)
    channels = shared['receipt']['channel_conditions']
    weights = shared['receipt']['effective_constants']
    channels_used = {name: {'weight': weights[name], 'level': weights[name] * channels[name]['after'],
                            'change': weights[name] * channels[name]['change']}
                     for name in ('a2', 'b1')}
    anchor = evidence['recent_events'][0] if evidence['recent_events'] else None
    quiet = evidence['quiet_reference']
    elapsed = now - evidence['planned_at_ms']
    quiet_band = quiet['pressure_per_ms'] * elapsed if quiet else None
    checks = {
        'unconsumed_same_edge_event': bool(anchor and not evidence['anchor_consumed']
                                          and now - anchor['at_ms'] <= evidence['horizon_ms']),
        'elapsed_time': elapsed > 0,
        'pressure_eased_beyond_same_edge_quiet': (
            quiet_band is not None and math.isfinite(quiet_band)
            and channels_used['b1']['change'] < -quiet_band),
        'exchange_condition_present': channels_used['a2']['level'] > 0,
    }
    pressure_eased = all(checks.values())
    reason = 'pressure_eased_over_time' if pressure_eased else None
    detail = {}
    if evidence['version'] == VERSION:
        from .peer_opening import supports_exchange_reply

        continuation = anchor.get('continuation') if anchor else None
        exchange = {}
        if continuation is not None and continuation['kind'] == 'light_question':
            for name, prior in continuation['exchange_evidence'].items():
                level = weights[name] * channels[name]['after']
                exchange[name] = {'weighted_level': level, **prior,
                                  'supports_reply': supports_exchange_reply(
                                      level, prior['reference_level'], prior['weighted_change'])}
        available = evidence.get('target_available', True)
        checks.update(target_available=available,
                      same_edge_continuation=any(x['supports_reply'] for x in exchange.values()),
                      source_allows_continuation=not continuation or continuation['kind'] == 'light_question')
        detail = {'continuation_source': continuation, 'continuation_exchange': exchange}
        permitted = (available and checks['source_allows_continuation'] and checks['elapsed_time']
                     and checks['unconsumed_same_edge_event'])
        reason = (reason or 'same_edge_light_question') if permitted and (
            pressure_eased or checks['same_edge_continuation']) else None
    return {'version': evidence['version'], 'edge': list(edge), 'reason': reason,
            'eligible': reason is not None, 'checks': checks, **detail,
            'anchor_event': anchor['id'] if anchor else None,
            'channels': channels_used, 'quiet_reference': quiet, 'quiet_band': quiet_band,
            'elapsed_ms': elapsed, 'evidence_fingerprint': fingerprint(evidence)}


def weighted_changes(projection):
    return {name: projection['effective_constants'][name] * projection['channel_conditions'][name]['change']
            for name in CONSTANT_ORDER}
