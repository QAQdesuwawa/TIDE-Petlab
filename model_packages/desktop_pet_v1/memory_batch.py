"""Batch metadata once; per-memory numeric values, no per-memory transcripts.

This is a package wire representation, not a recall algorithm. Source validation
happens before publication. The common header is bound to every numeric row by
the signed preparation output and a batch fingerprint. No quantization or cache.
"""
from collections.abc import Mapping
from dataclasses import replace
from framework.model_api import model_data_fingerprint
from model_packages.cx_six_seven_v1.interaction_geometry import ROOT_ORDER
from model_packages.cx_six_seven_v1.memory_recall_operators import PROJECTION_AXES
from model_packages.cx_six_seven_v1.memory_forward_readout import forward_live_readout_ref
from model_packages.cx_six_seven_v1.memory_numeric_contracts import MEMORY_EVENT_REF_SCHEMA, NEUTRAL_MEMORY_READOUT_SCHEMA
from model_packages.cx_six_seven_v1.memory_runtime_order import order_record, readout_order
from .memory_topology import CONTEXT_KEY

VERSION = 'desktop-pet-memory-batch-values-v3'

def enabled(context):
    return context.metadata.get(CONTEXT_KEY) == VERSION

def _packet(header, rows):
    if not rows:
        return []
    header = {**header, 'fingerprint': model_data_fingerprint({'header': header, 'rows': rows})}
    return [{'row': row, **({'header': header} if index == 0 else {})}
            for index, row in enumerate(rows)]

def pack(result, payload, context, *, parameter_version):
    inputs = result['memory_inputs']
    candidates = inputs['candidates']
    if not candidates:
        return {**result, 'memory_inputs': {**inputs, 'version': VERSION}}
    ids = [row['event_id'] for row in candidates]
    first = candidates[0]['influence_input']
    common = {'ids': ids,
              'evaluated_at': first['as_of_ms'],
              'evaluation_ordinal': first['receipt'].get('evaluation_runtime_ordinal'),
              'position': context.temporal_position.fingerprint,
              'source': model_data_fingerprint(payload),
              'live_parameter_version': parameter_version}
    bindings = []
    recalls, influences = [], []
    live_formula = first['formula_version']
    for candidate in candidates:
        identity = candidate['event_id']
        recall, live = candidate['recall_input'], candidate['influence_input']
        projection = recall['projection']
        pair = [projection['formula_version'], projection['constant_binding_version']]
        if pair not in bindings:
            bindings.append(pair)
        ordinal = live['receipt'].get('evaluation_runtime_ordinal')
        expected_ref = forward_live_readout_ref(identity, common['evaluated_at'],
                                                parameter_version, runtime_ordinal=ordinal)
        if (live['as_of_ms'] != common['evaluated_at'] or live['formula_version'] != live_formula
                or ordinal != common['evaluation_ordinal']
                or recall['live_readout_ref'] != expected_ref
                or tuple(live['lineage'])[:1] != (identity,)):
            raise ValueError('memory batch contains inconsistent replay coordinates')
        recalls.append({'values': [projection['vector'][axis] for axis in PROJECTION_AXES],
                        'binding': bindings.index(pair)})
        row = {'values': [live['live_vector'][axis] for axis in ROOT_ORDER],
               'magnitude': live['magnitude'], 'committed_at': live['event']['committed_at_ms']}
        node_ordinal = readout_order(live['receipt'])
        if node_ordinal is not None:
            row['ordinal'] = node_ordinal
        if len(live['lineage']) > 1:
            row['lineage'] = list(live['lineage'][1:])
        influences.append(row)
    recall_packets = _packet({**common, 'lane': 'recall', 'bindings': bindings}, recalls)
    influence_packets = _packet({**common, 'lane': 'influence', 'formula_version': live_formula}, influences)
    return {**result, 'memory_inputs': {**inputs, 'version': VERSION, 'candidates': [
        {'event_id': identity, 'recall_input': recall, 'influence_input': influence}
        for identity, recall, influence in zip(ids, recall_packets, influence_packets)]}}

def _unpack(packets, context, lane):
    if not isinstance(packets, (tuple, list)):
        raise ValueError('memory batch must be a sequence')
    if not packets:
        return None, []
    rows = []
    for index, packet in enumerate(packets):
        if not isinstance(packet, Mapping) or set(packet) != ({'row', 'header'} if index == 0 else {'row'}):
            raise ValueError('memory batch packet fields differ from contract')
        rows.append(packet['row'])
    header = packets[0]['header']
    fields = {'ids', 'evaluated_at', 'evaluation_ordinal', 'position', 'source',
              'live_parameter_version', 'lane', 'fingerprint',
              'bindings' if lane == 'recall' else 'formula_version'}
    if not isinstance(header, Mapping) or set(header) != fields:
        raise ValueError('memory batch header fields differ from contract')
    material = {key: value for key, value in header.items() if key != 'fingerprint'}
    ids = header['ids']
    if (not enabled(context) or header['lane'] != lane
            or not isinstance(ids, (tuple, list)) or len(ids) != len(rows)
            or any(not isinstance(identity, str) or not identity for identity in ids)
            or len(set(ids)) != len(ids)
            or header['position'] != context.temporal_position.fingerprint
            or header['evaluated_at'] != context.temporal_position.coordinate
            or (header['evaluation_ordinal'] is not None and header['evaluation_ordinal'] != context.runtime_ordinal)
            or not isinstance(header['source'], str) or len(header['source']) != 64
            or model_data_fingerprint({'header': material, 'rows': rows}) != header['fingerprint']):
        raise ValueError('memory batch identity or fingerprint mismatch')
    return header, rows

def _ref(header, identity):
    return forward_live_readout_ref(identity, header['evaluated_at'], header['live_parameter_version'],
                                   runtime_ordinal=header['evaluation_ordinal'])

def recall_inputs(packets, context, candidate_ids):
    header, rows = _unpack(packets, context, 'recall')
    if header is None:
        if candidate_ids:
            raise ValueError('empty memory batch omits candidates')
        return []
    if tuple(header['ids']) != tuple(candidate_ids):
        raise ValueError('memory batch differs from authorized candidates')
    result = []
    for identity, row in zip(header['ids'], rows):
        if (not isinstance(row, Mapping) or set(row) != {'values', 'binding'}
                or len(row['values']) != len(PROJECTION_AXES)
                or type(row['binding']) is not int or not 0 <= row['binding'] < len(header['bindings'])):
            raise ValueError('memory batch recall row is invalid')
        formula, binding = header['bindings'][row['binding']]
        result.append({'event_id': identity, 'evaluation_time_ms': header['evaluated_at'],
                       'live_readout_ref': _ref(header, identity),
                       'temporal_position_fingerprint': header['position'],
                       'projection': {'vector': dict(zip(PROJECTION_AXES, row['values'])),
                                      'formula_version': formula, 'constant_binding_version': binding,
                                      'receipt': {}}})
    return result

def influence_inputs(packets, context, recalled_ids):
    header, rows = _unpack(packets, context, 'influence')
    if not isinstance(recalled_ids, (tuple, list)) or any(not isinstance(x, str) for x in recalled_ids):
        raise ValueError('recalled IDs must be a sequence of strings')
    selected = set(recalled_ids)
    if header is None:
        if selected:
            raise ValueError('empty memory batch omits recalled events')
        return []
    if not selected.issubset(header['ids']):
        raise ValueError('memory batch omits recalled events')
    result = []
    for identity, row in zip(header['ids'], rows):
        # Whole-batch fingerprint above validates unselected rows as well.
        # Only the selected vectors become full consumer readout objects.
        if identity not in selected:
            continue
        if (not isinstance(row, Mapping) or not {'values', 'magnitude', 'committed_at'} <= set(row)
                or set(row) - {'values', 'magnitude', 'committed_at', 'ordinal', 'lineage'}
                or len(row['values']) != len(ROOT_ORDER)):
            raise ValueError('memory batch influence row is invalid')
        receipt = {'readout_ref': _ref(header, identity), 'source_input_fingerprint': header['source']}
        if 'ordinal' in row:
            receipt['runtime_order'] = order_record(row['ordinal'])
        result.append({'schema_version': NEUTRAL_MEMORY_READOUT_SCHEMA,
                       'event': {'schema_version': MEMORY_EVENT_REF_SCHEMA, 'event_id': identity, 'committed_at_ms': row['committed_at']},
                       'as_of_ms': header['evaluated_at'],
                       'live_vector': dict(zip(ROOT_ORDER, row['values'])),
                       'magnitude': row['magnitude'], 'formula_version': header['formula_version'],
                       'lineage': [identity, *row.get('lineage', ())], 'receipt': receipt})
    return result

def rebuild_evidence(*, package, payload, context, prepared):
    if prepared['memory_inputs']['version'] != VERSION:
        raise ValueError('memory batch replay requires its explicit format')
    regenerated = package.prepare_memory_inputs(payload=payload, context=context)
    if regenerated != prepared:
        raise ValueError('memory batch replay source or values differ')
    metadata = {key: value for key, value in context.metadata.items() if key != CONTEXT_KEY}
    return package.prepare_memory_inputs(payload=payload, context=replace(context, metadata=metadata))


def compact_recall(result):
    """Publish each decision's four existing scalars, not its calculation log."""
    decision = result['recall_decision']
    evidence = {identity: {key: row[key] for key in ('impulse', 'threshold', 'margin', 'recalled')}
                for identity, row in decision['candidate_evidence'].items()}
    claims = result['memory_content_claims']
    return {**result, 'recall_decision': {**decision, 'candidate_evidence': evidence},
            'memory_content_claims': {**claims, 'claims': [
                {**claim, 'source_evidence_fingerprint': model_data_fingerprint(evidence[claim['event_id']])}
                for claim in claims['claims']]}}


def rebuild_recall_evidence(*, package, payload, context, prepared):
    """Detailed recall evidence from the exact signed compact stage input."""
    if not enabled(context) or package.recall_history(payload=payload, context=context) != prepared:
        raise ValueError('recall evidence replay differs from published decisions')
    expanded = {**payload, 'historical_live': recall_inputs(payload['historical_live'], context,
                                                           payload['candidate_event_ids'])}
    metadata = {key: value for key, value in context.metadata.items() if key != CONTEXT_KEY}
    return package.recall_history(payload=expanded, context=replace(context, metadata=metadata))
