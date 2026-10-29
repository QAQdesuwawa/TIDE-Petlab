"""Package composition and actual child results; no scheduling/state authority.

The current renderer has one whole-body surface. Parallel expression/speech
are independent channels, while concurrent body clips require future explicit
asset composition support. This contract does not authorize or execute effects.
"""
from dataclasses import dataclass
from collections.abc import Mapping
from framework.model_api import freeze_model_payload, model_data_fingerprint
from .face_expression import CODES

VERSION='desktop-pet-composite-v1'
THOUGHT_VERSION='desktop-pet-composite-v2-private-thought'
PRESENTATION_VERSION='desktop-pet-composite-v3-optional-presentation'
SEQUENCE_VERSION='desktop-pet-composite-v4-sequence'


@dataclass(frozen=True)
class CompositePlan:
    children: Mapping

    def __post_init__(self):
        if not isinstance(self.children,Mapping) or not self.children:
            raise ValueError('composite needs children')
        sequence=any(isinstance(r,Mapping) and 'after' in r for r in self.children.values())
        if sequence and len(self.children)>16:raise ValueError('sequence exceeds bounded child capacity')
        channels=set()
        for identity,row in self.children.items():
            if not isinstance(identity,str) or not identity.strip():
                raise ValueError('child identity missing')
            if not isinstance(row,Mapping) or set(row)!=({'kind','key','after'} if sequence else {'kind','key'}):
                raise ValueError('invalid child declaration')
            kind,key=row['kind'],row['key']
            if kind not in {'action','expression','speech','thought'} or not isinstance(key,str) or not key:
                raise ValueError('invalid child channel or key')
            if kind in channels and not (sequence and kind=='action'):raise ValueError('channel already occupied')
            if kind=='expression' and key not in (*CODES,'selected_presentation'):raise ValueError('unknown expression')
            channels.add(kind)
            if sequence:
                deps=row['after']
                if (not isinstance(deps,(list,tuple)) or any(not isinstance(d,str) or d not in self.children for d in deps)
                        or len(deps)!=len(set(deps)) or identity in deps):raise ValueError('invalid child dependencies')
        if sequence:
            ancestors={};visiting=set()
            def collect(key):
                if key in visiting:raise ValueError('cyclic child dependencies')
                if key not in ancestors:
                    visiting.add(key);found=set(self.children[key]['after'])
                    for dep in self.children[key]['after']:found.update(collect(dep))
                    ancestors[key]=found;visiting.remove(key)
                return ancestors[key]
            for key in self.children:collect(key)
            bodies=[k for k,r in self.children.items() if r['kind']=='action']
            for i,left in enumerate(bodies):
                for right in bodies[i+1:]:
                    if left not in ancestors[right] and right not in ancestors[left]:
                        raise ValueError('whole-body actions require an execution order')
        object.__setattr__(self,'children',freeze_model_payload(self.children))

    @classmethod
    def create(cls,children):return cls(children)

    @property
    def version(self):
        if any('after' in r for r in self.children.values()):return SEQUENCE_VERSION
        if any(r['kind']=='expression' and r['key']=='selected_presentation' for r in self.children.values()):return PRESENTATION_VERSION
        return THOUGHT_VERSION if any(row['kind']=='thought' for row in self.children.values()) else VERSION

    @property
    def fingerprint(self):
        return model_data_fingerprint({'version':self.version,'children':self.children})

    def validate_contents(self,contents):
        from .events import Content
        contents=tuple(contents)
        if any(not isinstance(item,Content) for item in contents):raise ValueError('invalid content')
        ids=[item.content_id for item in contents]
        optional={key for key,row in self.children.items() if (self.version==THOUGHT_VERSION and row['kind'] in {'speech','thought'}) or (self.version in {PRESENTATION_VERSION,SEQUENCE_VERSION} and row['kind'] in {'speech','thought','expression'})}
        if (len(ids)!=len(set(ids)) or not set(ids).issubset(self.children)
                or not (set(self.children)-set(ids)).issubset(optional)):
            raise ValueError('contents differ from declared children')
        for item in contents:
            row=self.children[item.content_id]
            if item.kind!=row['kind']:raise ValueError('child channel changed')
            if item.kind=='action' and item.semantic_key!=row['key']:
                raise ValueError('selected action changed')
            if item.kind=='expression' and row['key']!='selected_presentation' and item.parameters['presentation']['expression']!=row['key']:
                raise ValueError('selected expression changed')
            if item.kind=='speech' and item.parameters:
                from .face_expression import validate
                if set(item.parameters)!={'presentation'}:raise ValueError('invalid temporary speech presentation')
                validate(item.parameters['presentation'])


@dataclass(frozen=True)
class ChildResult:
    child_id: str
    status: str
    started_at_ms: int | None
    ended_at_ms: int
    trace_ref: str
    reason: str | None = None
    cause_ref: str | None = None

    def __post_init__(self):
        if not self.child_id or not isinstance(self.child_id,str):raise ValueError('missing child')
        if self.status not in {'completed','interrupted','cancelled','failed','uncertain'}:
            raise ValueError('invalid result status')
        if type(self.ended_at_ms) is not int:raise ValueError('missing end time')
        if self.started_at_ms is not None and (type(self.started_at_ms) is not int or self.started_at_ms>self.ended_at_ms):
            raise ValueError('invalid actual interval')
        if not isinstance(self.trace_ref,str) or not self.trace_ref.strip():raise ValueError('missing evidence')
        if self.status in {'completed','interrupted'} and self.started_at_ms is None:
            raise ValueError('actual effect requires start evidence')
        if self.status in {'cancelled','failed'} and self.started_at_ms is not None:
            raise ValueError('partial actual effects require interrupted result')
        if self.reason not in {None,'user_contact','source_expired','technical_failure','window_closed','unknown','no_speech_selected','no_content_selected'}:
            raise ValueError('unknown interruption reason')
        if self.status!='completed' and self.reason is None:raise ValueError('termination reason required')
        if self.reason in {'user_contact','source_expired'} and not self.cause_ref:
            raise ValueError('causal source required')
        if self.cause_ref is not None and (not isinstance(self.cause_ref,str) or not self.cause_ref.strip()):
            raise ValueError('invalid causal source')

    def to_record(self):return dict(vars(self))


def summarize(plan,results):
    """Close only known outcomes. No effects are committed by this helper."""
    rows=tuple(results)
    if any(not isinstance(row,ChildResult) for row in rows):raise ValueError('invalid child result')
    ids=[row.child_id for row in rows]
    if len(ids)!=len(set(ids)) or set(ids)!=set(plan.children):
        raise ValueError('all declared children need exactly one terminal result')
    if any(row.status=='uncertain' for row in rows):
        raise ValueError('unknown effects require reconciliation')
    by_id={row.child_id:row for row in rows}
    if plan.version==SEQUENCE_VERSION:
        for key,row in by_id.items():
            if row.started_at_ms is None:continue
            for dep in plan.children[key]['after']:
                previous=by_id[dep]
                if previous.status!='completed' or previous.ended_at_ms>row.started_at_ms:
                    raise ValueError('child ran before its dependency completed')
    return freeze_model_payload({'version':plan.version,'plan_fingerprint':plan.fingerprint,
        'actual_child_ids':[key for key in plan.children if by_id[key].started_at_ms is not None],
        'results':{key:by_id[key].to_record() for key in plan.children}})


def validate_outcome(plan,outcome):
    if 'composite_version' in outcome.details and outcome.details['composite_version']!=plan.version:
        raise ValueError('execution composite version differs')
    rows=outcome.details.get('child_results')
    if not isinstance(rows,Mapping):raise ValueError('composite needs actual child receipts')
    results=[]
    for key,row in rows.items():
        if not isinstance(row,Mapping) or set(row)-{'status','started_at_ms','ended_at_ms','reason','cause_ref'}:
            raise ValueError('invalid child receipt fields')
        results.append(ChildResult(key,row.get('status'),row.get('started_at_ms'),row.get('ended_at_ms'),
            outcome.trace_ref,row.get('reason'),row.get('cause_ref')))
    summary=summarize(plan,results)
    if set(summary['actual_child_ids'])!=set(outcome.completed_content_ids):
        raise ValueError('actual identities differ from child receipts')
    for key in summary['actual_child_ids']:
        if plan.children[key]['kind']=='action':
            actual=outcome.details.get('action_results',{}).get(key,{})
            if actual.get('status')!=rows[key]['status'] or type(actual.get('rendered_frames')) is not int or actual['rendered_frames']<=0:
                raise ValueError('action playback differs from child receipt')
            if rows[key]['status']=='interrupted' and any(actual.get(field)!=rows[key].get(field) for field in ('reason','cause_ref')):
                raise ValueError('action cause differs from child receipt')
    return summary
