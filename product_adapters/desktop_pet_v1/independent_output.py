"""Recover independently executable parts of a Writer response."""
import json

from model_packages.desktop_pet_v1 import action_sequence, spatial


LEGACY_VERSION = 'petlab-independent-writer-output-v1'
EXPRESSION_SEGMENTS_VERSION = 'petlab-independent-writer-output-v2'
VERSION = 'petlab-independent-writer-output-v3'
VERSIONS = (LEGACY_VERSION, EXPRESSION_SEGMENTS_VERSION, VERSION)


def recover(text, context, *, scene, human_ref, expressions, gaze_independent, version=VERSION):
    body = json.loads(text)
    if not isinstance(body, dict):
        raise ValueError('writer output must be an object')
    offer = action_sequence.validate_offer(context)
    actions = body.get('actions')
    if actions is None:
        actions = []
    rejected_actions, adjustments = [], []
    if version == VERSION and body.get('type') == 'json_object':
        body.pop('type')
        adjustments.append({'field': 'type', 'reason': 'response_format_marker', 'original': 'json_object'})
    keys = []
    if not isinstance(actions, list):
        adjustments.append({'field': 'actions', 'reason': 'invalid_action_array'})
        actions = []
    # An ordered action suffix may depend on the failed step. Retaining only
    # the validated prefix also preserves every surviving action index.
    for index, key in enumerate(actions):
        if rejected_actions:
            reason = 'after_unavailable_action'
        else:
            try:
                keys = action_sequence.validate_keys([*keys, key], offer)
                continue
            except ValueError:
                reason = 'outside_frozen_candidates' if key not in offer['allowed_keys'] else 'invalid_action_sequence'
        rejected_actions.append({'index': index, 'proposed_key': key if isinstance(key, str) else None,
                                 'status': 'not_executed', 'reason': reason})
    body['actions'] = keys

    def segments(field, value_field, other_fields=()):
        rows = body[field]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 4:
            adjustments.append({'field': field, 'reason': 'invalid_segment_array'})
            return []
        result = []
        last_anchor = -1
        for index, row in enumerate(rows):
            if field == 'speech' and isinstance(row, str):
                row = {'text': row}
            valid = isinstance(row, dict) and set(row) in ({value_field, *other_fields}, {value_field, *other_fields, 'timing'})
            if valid and field == 'speech':
                valid = isinstance(row['text'], str) and bool(row['text'].strip())
            elif valid:
                valid = (isinstance(row['expression'], str) and row['expression'] in expressions and type(row['hold_ms']) is int
                         and 100 <= row['hold_ms'] <= 10000)
            if not valid:
                adjustments.append({'field': field, 'index': index, 'reason': 'invalid_segment'})
                continue
            try:
                timing = action_sequence.validate_speech_timing(row.get('timing'), keys, has_speech=True)
                if field == 'speech' and timing is not None:
                    anchor = timing['action_index'] * 2 + (timing['phase'] == 'end')
                    if anchor < last_anchor:
                        raise ValueError('speech anchors out of order')
                    last_anchor = anchor
            except ValueError:
                adjustments.append({'field': field, 'index': index, 'reason': 'invalid_or_unavailable_timing'})
                continue
            if 'timing' not in row:
                adjustments.append({'field': field, 'index': index, 'reason': 'implicit_unanchored_timing'})
            result.append({**row, 'timing': timing})
        return result

    expression = body.get('expression')
    if version in (EXPRESSION_SEGMENTS_VERSION, VERSION) and isinstance(expression, (dict, list)) and body.get('expressions') is None:
        body['expressions'] = expression if isinstance(expression, list) else [expression]
        adjustments.append({'field': 'expression', 'reason': 'expression_segments_in_scalar_field'})
    if body.get('expressions') is not None:
        faces = segments('expressions', 'expression', ('hold_ms',))
        if faces:
            body['expressions'] = faces
        else:
            body.pop('expressions')
    if isinstance(body.get('speech'), dict):
        body['speech'] = [body['speech']]
    if isinstance(body.get('speech'), list):
        # The segmented contract assigns timing per segment. A top-level
        # anchor cannot identify which segment it was meant to constrain.
        if body.pop('speech_timing', None) is not None:
            adjustments.append({'field': 'speech_timing', 'reason': 'segmented_speech_uses_segment_timing'})
        body['speech'] = segments('speech', 'text') or None
    elif body.get('speech_timing') is not None:
        try:
            action_sequence.validate_speech_timing(body['speech_timing'], keys,
                                                   has_speech=bool(body.get('speech')))
        except ValueError:
            body.pop('speech_timing')
            body['speech'] = None
            adjustments.append({'field': 'speech', 'reason': 'invalid_or_unavailable_timing'})
    if scene is not None and body.get('gaze_target') is not None:
        targets = spatial.targets(scene)
        gaze = body['gaze_target']
        if ((gaze not in targets and gaze != human_ref)
                or (not gaze_independent and body.get('speech') is None)):
            body.pop('gaze_target')
            adjustments.append({'field': 'gaze_target', 'reason': 'unavailable_gaze'})
    return json.dumps(body, ensure_ascii=False), rejected_actions, adjustments
