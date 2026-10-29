"""Bind package applicability to formal measurement and the live capture host."""
from petlab_app.owned_journals import publish_session
from dataclasses import replace
from framework.model_api import model_data_fingerprint as fingerprint,thaw_model_data
from model_packages.desktop_pet_v1.three_time import ObservationTime
from model_packages.desktop_pet_v1.continuation import assess_continuation
from .temporal_measurement import KEY as TIME_KEY,load,signed,verify

KEY='desktop_pet_continuation_checks_v1'
STAGES={'before_writer','before_execution','execution_guard'}


class ContinuationGuard:
    def __init__(self,*,product,mouse,event_id,clock,is_cancelled):
        self.app=product.application;self.mouse=mouse;self.clock=clock;self.is_cancelled=is_cancelled
        self.event=self.app.runtime.memory_instance(event_id).event
        self.app._verify_source(self.event)
        content=self.event.contents[0]
        if content.kind!='interaction' or content.parameters.get('phase')!='ongoing':
            raise ValueError('continuation requires its actual ongoing observation')
        row=self.app.runtime.session.profile.get(TIME_KEY,{}).get(event_id)
        if row is None:raise ValueError('ongoing continuation lacks formal query evidence')
        complete,_payload=load(self.app,product.semantic.gateway,self.event,thaw_model_data(row))
        candidates=[x for x in complete['observations'] if x['observation_id']==content.content_id]
        if len(candidates)!=1:raise ValueError('continuation source has no unique observation time')
        self.observation=ObservationTime.from_record(candidates[0]);self.last=None

    def check(self,stage=None,prepared=None):
        self.last=assess_continuation(self.observation,
            capture=self.mouse.capture_info(self.observation.interaction_id),checked_at_ms=self.clock(),
            time_domain=fingerprint(self.event.position.binding.to_record()),host_stopping=self.is_cancelled())
        if stage is not None:self.publish(stage,prepared=prepared)
        return self.last['applicable']

    def publish(self,stage,*,prepared=None,executor_cancelled=False):
        if stage not in STAGES or self.last is None:raise ValueError('continuation check stage is invalid')
        row=signed({'event_fingerprint':self.event.fingerprint,'decision':self.last,
            'prepared_fingerprint':None if prepared is None else prepared.fingerprint,
            'executor_cancelled':executor_cancelled})
        # Fixed checkpoints, never one record per movement or callback.
        profile=dict(self.app.runtime.session.profile)
        journal=dict(profile.get(KEY,{}));journal[self.event.event_id]=thaw_model_data(journal.get(self.event.event_id,{}));journal[self.event.event_id][stage]=row;profile[KEY]=journal
        session=replace(self.app.runtime.session,profile=profile)
        publish_session(self.app,session)


def verify_saved(app):
    for event_id,stages in app.runtime.session.profile.get(KEY,{}).items():
        event=app.runtime.memory_instance(event_id).event
        times=app.runtime.session.profile.get(TIME_KEY,{}).get(event_id)
        if times is None or times['status']!='parsed':raise ValueError('continuation lacks saved query source')
        observations={x['fingerprint']:ObservationTime.from_record(x) for x in times['observations']}
        for stage,row in stages.items():
            verify(row)
            if (stage not in STAGES or row['event_fingerprint']!=event.fingerprint
                    or not isinstance(row['executor_cancelled'],bool)):
                raise ValueError('continuation check belongs to another stage or event')
            value=row['decision'];observation=observations.get(value['observation_time_fingerprint'])
            if observation is None:raise ValueError('continuation check observation differs')
            expected=assess_continuation(observation,capture=value['capture'],checked_at_ms=value['checked_at_ms'],
                time_domain=fingerprint(event.position.binding.to_record()),host_stopping=value['host_stopping'])
            if fingerprint(value)!=fingerprint(expected):raise ValueError('continuation decision differs from its evidence')
