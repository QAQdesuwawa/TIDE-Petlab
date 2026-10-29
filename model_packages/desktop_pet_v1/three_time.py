"""Package-owned temporal coordinates; no scheduling or state-write authority.

Receipt references identify evidence; this value object checks consistency, not
whether external evidence exists. The consuming host must verify those receipts.
"""
from dataclasses import asdict, dataclass, replace
from collections.abc import Mapping
from framework.model_api import model_data_fingerprint

VERSION = 'desktop-pet-observation-time-v1'


@dataclass(frozen=True, kw_only=True)
class ObservationTime:
    interaction_id: str
    observation_id: str
    sequence: int
    time_domain: str
    origin_ms: int
    world_start_ms: int
    world_end_ms: int
    local_start_ms: int
    local_end_ms: int
    query_started_ms: int
    source_ref: str
    query_completed_ms: int | None = None
    completion_ref: str | None = None

    def __post_init__(self):
        for name in ('interaction_id', 'observation_id', 'time_domain', 'source_ref'):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or value.strip() != value:
                raise ValueError(f'{name} requires an exact identifier')
        for name in ('sequence', 'origin_ms', 'world_start_ms', 'world_end_ms',
                     'local_start_ms', 'local_end_ms', 'query_started_ms'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f'{name} requires integer milliseconds or ordinal')
        if self.sequence < 1:
            raise ValueError('sequence must be positive')
        if not self.origin_ms <= self.world_start_ms <= self.world_end_ms <= self.query_started_ms:
            raise ValueError('world and query order differs')
        if (self.local_start_ms != self.world_start_ms-self.origin_ms or
                self.local_end_ms != self.world_end_ms-self.origin_ms):
            raise ValueError('local interval differs from continuous interaction origin')
        if self.query_completed_ms is None:
            if self.completion_ref is not None:
                raise ValueError('completion evidence without completion time')
        else:
            if (isinstance(self.query_completed_ms, bool) or
                    not isinstance(self.query_completed_ms, int) or
                    self.query_completed_ms < self.query_started_ms):
                raise ValueError('query completion precedes start or has invalid type')
            if (not isinstance(self.completion_ref, str) or not self.completion_ref or
                    self.completion_ref.strip() != self.completion_ref):
                raise ValueError('query completion requires receipt reference')

    def complete(self, coordinate, *, receipt_ref):
        if self.query_completed_ms is not None:
            raise ValueError('observation processing already completed')
        return replace(self, query_completed_ms=coordinate, completion_ref=receipt_ref)

    def verify_successor(self, previous):
        """Geometric continuity only, not permission to consume or settle twice."""
        if not isinstance(previous, ObservationTime):
            raise ValueError('previous observation is missing')
        if (self.interaction_id != previous.interaction_id or
                self.time_domain != previous.time_domain or
                self.origin_ms != previous.origin_ms or
                self.observation_id == previous.observation_id or
                self.sequence != previous.sequence+1 or
                self.world_start_ms != previous.world_end_ms or
                self.local_start_ms != previous.local_end_ms or
                self.query_started_ms < previous.query_started_ms):
            raise ValueError('observation is not a contiguous successor')
        # Completion can occur after another observation has started. No order
        # between independent processing completions is fabricated here.

    def to_record(self):
        record = {'version': VERSION, **asdict(self)}
        return {**record, 'fingerprint': model_data_fingerprint(record)}

    @classmethod
    def from_record(cls, record):
        if not isinstance(record, Mapping):
            raise ValueError('observation time record must be a mapping')
        raw = dict(record)
        fingerprint = raw.pop('fingerprint', None)
        if model_data_fingerprint(raw) != fingerprint or raw.pop('version', None) != VERSION:
            raise ValueError('observation time record integrity differs')
        try:
            return cls(**raw)
        except TypeError as error:
            raise ValueError('observation time fields differ') from error
