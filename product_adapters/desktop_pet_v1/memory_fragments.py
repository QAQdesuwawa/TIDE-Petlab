"""Source-bound fragment matching for the desktop Writer property lane."""

from collections.abc import Mapping
from itertools import takewhile
from unicodedata import normalize

from model_packages.desktop_pet_v1.semantic import evidence_parts


def fragments(event, contents, properties):
    """Bind measured properties to one authorized fragment of their source."""
    wanted = {content.content_id for content in contents}
    subjects = {'current_npc': event.owner_ref}
    if event.actor_ref != event.owner_ref:
        subjects['user'] = event.actor_ref
    elif len(event.target_refs) == 1:
        subjects['user'] = event.target_refs[0]
    offset = 0
    result = []
    for content, text in zip(event.contents, evidence_parts(event)):
        end = offset + len(text)
        if content.content_id in wanted:
            tags = tuple((subjects.get(row['owner']), tuple(row['path']), row['kind'])
                         for row in properties if offset <= row['span'][0] < row['span'][1] <= end)
            # Action recipients may describe visibility. Only world object refs
            # in the action body binding are safe to label as physical targets.
            objects = tuple(ref for ref in content.recipient_refs if ref.startswith('world:'))
            seen_object = content.parameters.get('visible_object_ref')
            if content.kind == 'action' and isinstance(seen_object, str) and seen_object.startswith('world:'):
                objects = tuple(dict.fromkeys((*objects, seen_object)))
            for key in ('movement', 'movement_result', 'meal', 'social_contact', 'meal_button_result'):
                binding = content.parameters.get(key, {})
                if not isinstance(binding, Mapping):
                    continue
                target = binding.get('ref') if key == 'meal_button_result' else (
                    binding.get('target_ref') or binding.get('target'))
                if isinstance(target, str) and target:
                    objects = tuple(dict.fromkeys((*objects, target)))
            result.append({'id': content.content_id, 'source': content.kind,
                           'actor': event.actor_ref, 'behavior': content.semantic_key,
                           'objects': objects, 'properties': tags})
        offset = end + 1
    return result


def match(query, candidate):
    """Material and role alignment precede independent path comparisons."""
    if query['source'] != candidate['source']:
        return 0.0
    if query['objects'] and candidate['objects'] and not set(query['objects']).intersection(candidate['objects']):
        return 0.0
    if query['behavior'] is not None and candidate['behavior'] is not None:
        return float(query['behavior'] == candidate['behavior'])
    scores = []
    for subject, path, query_kind in query['properties']:
        for other_subject, other_path, kind in candidate['properties']:
            if subject is None or subject != other_subject or (kind == 'query' and query_kind != 'query'):
                continue
            shared = sum(1 for _ in takewhile(lambda pair: pair[0] == pair[1], zip(path, other_path)))
            scores.append(shared / max(len(path), len(other_path)))
    return max(scores, default=0.0)


FRAGMENT_ROLE_VERSION = 'desktop-pet-current-turn-layout-v21-memory-roles'
SOURCE_TYPED_VERSION = 'desktop-pet-current-turn-layout-v22-source-typed-memory'
ROLE_MATCH_LAYOUTS = (FRAGMENT_ROLE_VERSION, SOURCE_TYPED_VERSION)
WEIGHTED_VERSION = 'desktop-pet-fragment-label-ranking-v1'
COMPRESSED_VERSION = 'desktop-pet-fragment-label-ranking-v2'
WEIGHTED_VERSIONS = (WEIGHTED_VERSION, COMPRESSED_VERSION)
SOURCE_TYPED_LAYOUTS = (SOURCE_TYPED_VERSION, *WEIGHTED_VERSIONS)


def experience_text(content, *, intention):
    if intention:
        label = '已形成的意图'
    else:
        label = {'speech': '原话', 'action': '动作记录', 'expression': '表现记录',
                 'interaction': '现场记录'}.get(content.kind, '实际经历')
    return f'{label}：{content.description}'



def _match_level(query, candidate):
    if query['source'] != candidate['source']:
        return 0, ''
    q_objects, c_objects = set(query['objects']), set(candidate['objects'])
    if q_objects and c_objects and not q_objects.intersection(c_objects):
        return 0, ''
    if (query['source'] in {'action', 'self_amusement'} and query['behavior']
            and candidate['behavior']):
        if query['behavior'] == candidate['behavior']:
            if bool(q_objects) == bool(c_objects):
                return 3, 'same_action'
            return 1, 'action_target_unspecified'
        # Separate authoritative actions retain their distinct identities.
        return 0, ''

    best = (0, '')
    for subject, path, query_kind in query['properties']:
        for other_subject, other_path, kind in candidate['properties']:
            if subject is None or subject != other_subject or (kind == 'query' and query_kind != 'query'):
                continue
            common = sum(1 for _ in takewhile(lambda pair: pair[0] == pair[1], zip(path, other_path)))
            if not common:
                continue
            if path == other_path:
                return 3, 'same_attribute'
            if common == min(len(path), len(other_path)) and abs(len(path)-len(other_path)) == 1:
                best = max(best, (2, 'direct_parent_or_child'))
            else:
                best = max(best, (1, 'shared_ancestor'))
    return best


def best_match(queries, candidates):
    """Normalize ordered match categories for the existing property lane."""
    strongest = None
    for query in queries:
        for candidate in candidates:
            level, relation = _match_level(query, candidate)
            if level and (strongest is None or level > strongest['level']):
                strongest = {'query_child': query['id'], 'history_child': candidate['id'],
                             'level': level, 'relation': relation, 'score': level / 3}
    return strongest


_BEHAVIOR_PATHS = {
    'move_prop': ('操作', '移动', '移动道具'),
    'move_near': ('操作', '移动', '走近'),
    'stroll': ('操作', '移动', '散步'),
    'move_away': ('操作', '移动', '走开'),
    'relocate': ('操作', '移动', '换位置'),
    'press_meal_button': ('操作', '按按钮', '按出饭按钮'),
    'request_bowl': ('用餐', '饭碗交接', '索要饭碗'),
    'offer_bowl': ('用餐', '饭碗交接', '递碗'),
    'accept_bowl': ('用餐', '饭碗交接', '接碗'),
    'decline_bowl': ('用餐', '饭碗交接', '拒绝接碗'),
    'keep_bowl': ('用餐', '持碗', '保留饭碗'),
    'eat_rice': ('用餐', '进食', '吃饭'),
}
_LABEL_ALIASES = {'挪道具': '移动道具', '挪动物件': '移动道具', '移动物件': '移动道具'}
_BOUND_ACTIONS = {'move_prop', 'move_near', 'press_meal_button',
                  'request_bowl', 'offer_bowl', 'accept_bowl', 'decline_bowl'}
_KNOWN_PARENT_PATHS = {path[:depth] for path in _BEHAVIOR_PATHS.values()
                       for depth in range(1, len(path))}


def _canonical_path(path):
    names = tuple(normalize('NFKC', name).strip() for name in path)
    label = _LABEL_ALIASES.get(names[-1], names[-1])
    for behavior, known in _BEHAVIOR_PATHS.items():
        if label in {behavior, known[-1]} and names[:-1] in ((), known[:1], known[:-1]):
            return known
    return (*names[:-1], label)


def _prefix_depth(left, right):
    return sum(1 for _ in takewhile(lambda pair: pair[0] == pair[1], zip(left, right)))


def _independent_paths(properties, behavior, actor):
    by_branch = {}
    for subject, path, kind in properties:
        if subject is None:
            continue
        key = subject, _canonical_path(path)
        if kind == 'assertion' or key not in by_branch:
            by_branch[key] = kind
    rows = [(subject, path, kind) for (subject, path), kind in by_branch.items()]
    return [(subject, path, kind) for subject, path, kind in rows
            if not (behavior and subject == actor and behavior[:len(path)] == path)
            and not any(subject == other_subject
                        and len(path) < len(other) and other[:len(path)] == path
                        for other_subject, other, _ in rows)]


def _hierarchy_score(path, other, shared):
    if path == other and path not in _KNOWN_PARENT_PATHS:
        return 1.0, 'exact'
    if shared == 1:
        return 0.05, 'shared_root'
    if path == other:
        return 0.4, 'same_registered_parent'
    if shared == min(len(path), len(other)) and abs(len(path) - len(other)) == 1:
        return 0.4, 'direct_parent_or_child'
    return 0.2, 'shared_ancestor'


def _label_matches(query, candidate, *, compressed):
    q_key, h_key = query['behavior'], candidate['behavior']
    q_behavior = _BEHAVIOR_PATHS.get(q_key, (q_key,)) if q_key else None
    h_behavior = _BEHAVIOR_PATHS.get(h_key, (h_key,)) if h_key else None
    q_behavior = q_behavior if query['source'] in {'action', 'self_amusement'} else None
    h_behavior = h_behavior if candidate['source'] in {'action', 'self_amusement'} else None
    q_actor, h_actor = query.get('actor'), candidate.get('actor')
    query_paths = _independent_paths(query['properties'], q_behavior, q_actor)
    history_paths = _independent_paths(candidate['properties'], h_behavior, h_actor)
    if q_behavior:
        query_paths.insert(0, (q_actor, q_behavior, 'behavior'))
    if h_behavior:
        history_paths.insert(0, (h_actor, h_behavior, 'behavior'))
    pairs = []
    for qi, (subject, path, kind) in enumerate(query_paths):
        for hi, (other_subject, other, other_kind) in enumerate(history_paths):
            same_role = kind == other_kind == 'behavior' or (subject is not None and subject == other_subject)
            if not same_role or (other_kind == 'query' and kind != 'query'):
                continue
            shared = _prefix_depth(path, other)
            if shared:
                score, relation = (_hierarchy_score(path, other, shared) if compressed
                                   else (2 * shared / (len(path) + len(other)), None))
                pairs.append((score, qi, hi, shared, relation))
    chosen, used = {}, set()
    # Pair the strongest branches first. Each query and history branch can
    # contribute at most once, even when it is an ancestor of several labels.
    for score, qi, hi, shared, relation in sorted(pairs, key=lambda row: (-row[0], -row[3] if compressed else 0, row[1], row[2])):
        if qi not in chosen and hi not in used:
            chosen[qi] = {'history': list(history_paths[hi][1]), 'shared_depth': shared, 'score': score}
            if relation is not None:
                chosen[qi]['relation'] = relation
            used.add(hi)
    matches = [{'query': list(path), **chosen.get(index, {'history': None, 'score': 0.0})}
               for index, (_, path, _) in enumerate(query_paths)]
    return matches


def _weighted_pair(query, candidate, *, compressed):
    labels = _label_matches(query, candidate, compressed=compressed)
    q_objects, h_objects = set(query['objects']), set(candidate['objects'])
    object_score = len(q_objects & h_objects) / len(q_objects) if q_objects else 0.0
    label_score = sum(row['score'] for row in labels) / len(labels) if labels else 0.0
    if not (label_score or object_score):
        return None
    same_material = query['source'] == candidate['source']
    q_phase, h_phase = query.get('phase'), candidate.get('phase')
    same_phase = bool(same_material and q_phase and q_phase == h_phase)
    support = max(label_score, object_score) if compressed else 1.0
    requires_object = bool(q_objects) or (query['source'] == 'action' and query['behavior'] in _BOUND_ACTIONS)
    components = {
        'labels': {'weight': 2 if labels else 0, 'score': label_score, 'matches': labels},
        'object': {'weight': 2 if requires_object else 0, 'score': object_score,
                   'relation': ('unspecified' if not requires_object else 'unknown' if not q_objects or not h_objects
                                else 'exact' if object_score == 1 and q_objects == h_objects
                                else 'overlap' if object_score else 'different')},
        'material': {'weight': 1, 'score': support if same_material else 0.0,
                     'query': query['source'], 'history': candidate['source']},
        'phase': {'weight': int(q_phase is not None), 'score': support if same_phase else 0.0,
                  'query': q_phase, 'history': h_phase},
    }
    total = sum(part['weight'] * part['score'] for part in components.values())
    weight = sum(part['weight'] for part in components.values())
    return {'query_child': query['id'], 'history_child': candidate['id'],
            'relation': 'weighted_labels', 'components': components,
            'score': total / weight}


def weighted_match(queries, candidates, *, version=WEIGHTED_VERSION):
    """Score each query fragment once and keep its authorized witness."""
    compressed = version == COMPRESSED_VERSION
    matches = []
    for query in queries:
        pairs = (_weighted_pair(query, candidate, compressed=compressed) for candidate in candidates)
        best = max((pair for pair in pairs if pair is not None),
                   key=lambda pair: pair['score'], default=None)
        matches.append(best or {'query_child': query['id'], 'history_child': None, 'score': 0.0})
    if not any(row['history_child'] is not None for row in matches):
        return None
    return {'version': version, 'fragments': matches,
            'score': sum(row['score'] for row in matches) / len(matches)}


def evidence_rows(rows, *, phases=None, explicit_material=False):
    """Parallel to the original visible content order, without duplicating text."""
    labels = {'speech': '话语', 'action': '动作记录', 'thought': '当时的内在想法',
              'interaction': '现场记录', 'expression': '表现记录',
              'self_amusement': '自身活动记录'}
    phases = phases or {}
    statuses = {'started': '当时开始', 'completed': '已完成', 'interrupted': '已打断'}
    explicit_completed = {'speech': '发言已结束', 'action': '动作已完成',
                          'expression': '表现已完成'}
    result = []
    for index, row in enumerate(rows, 1):
        phase = phases.get(row['id'])
        status = statuses.get(phase)
        if explicit_material and phase == 'completed':
            status = explicit_completed.get(row['source'], status)
        result.append({'片段': index, '材料性质': labels.get(row['source'], row['source']),
             **({'记录阶段': status} if status is not None else {}),
             **({'行为': row['behavior']} if row['behavior'] is not None else {}),
             **({'动作对象': list(row['objects'])} if row['objects'] else {})})
    return result


def receipt_phases(app, event):
    from petlab_app.session import PRODUCT_KEY
    from .visible_context import _artifact

    source = app.runtime.session.profile[PRODUCT_KEY]['sources'].get(event.event_id)
    if source is None:
        return {}
    if source['origin'] == 'execution':
        receipt = source['evidence']['fact']['model_payload']['execution_receipt']
        return {identity: row['status'] for identity, row in
                receipt['outcome']['details'].get('child_results', {}).items()}
    artifact = _artifact(app, source)
    if artifact is None:
        return {}
    manifest = artifact['source'].get('visible_manifest', {})
    return {item['content_id']: item['phase'] for item in manifest.get('items', ())
            if 'phase' in item}
