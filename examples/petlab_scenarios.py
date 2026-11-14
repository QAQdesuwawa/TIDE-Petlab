"""Offline PetLab journeys over the production adapters, not a real LLM or UI.

Run: python -B -m examples.petlab_scenarios --output output/my-scenarios
Fixture numbers are declared test inputs. No API key, socket or model weights.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import uuid

from examples.existing_provider import ExistingClientTransport
from framework.model_api import thaw_model_data
from model_packages.cx_six_seven_v1.initialization_schema import generate_base_constants
from model_packages.desktop_pet_v1.events import Speech
from petlab_app.composition import PetDefinition,background_creation_context,definition_from_background,open_application
from petlab_app.desktop_app import configuration_from_record
from petlab_app.history_import import ImportWorkspace,read_transcript
from petlab_app.history_import_tool import prepare
from petlab_app.product import PetProduct,wall_clock_ms
from petlab_app.providers import ProviderGateway
from petlab_app.setup_dialog import save_configuration
from product_adapters.desktop_pet_v1.creation import measure_background
from product_adapters.desktop_pet_v1.execution import ExecutionOutcome,ExecutionUncertain
from product_adapters.desktop_pet_v1.import_reconstruction import Reconstruction
from product_adapters.desktop_pet_v1.import_publication import publish

WEIGHTS={'Counter_Phobic':.1,'Mirroring':.3,'Avoidance':.1,'Trauma_Recon':0.,'Pragmatism':.5}


class SimulatedInterruption(RuntimeError):
    """Example-only fault after provider receipts, before the step checkpoint."""


def require(condition,message):
    if not condition:raise AssertionError(message)


def load_cases():
    return json.loads(Path(__file__).with_name('petlab-cases.json').read_text('utf8'))['cases']


class OfflineFixture:
    """Finite synthetic measurements; writer reads only actually delivered cards."""
    def __init__(self,case):
        self.case=case;self.calls=[];self.current_question=None;self.fail_measurement=False

    def writer(self,prompt):
        marker='相关记忆（按发生顺序）：'
        if marker not in prompt:raise ValueError('no delivered memory cards')
        cards=json.JSONDecoder().raw_decode(prompt.split(marker,1)[1].lstrip())[0]
        if not cards:raise ValueError('no delivered memory cards')
        if self.current_question is None:raise ValueError('fixture question not selected')
        values=[];used=[]
        for requested in self.current_question['properties']:
            matches=[(card,prop) for card in cards for prop in card.get('语义属性',[])
                     if prop['owner']==requested['owner'] and prop['path']==requested['path']
                     and prop['kind']=='assertion']
            if not matches:raise ValueError('required property absent from delivered cards')
            # Use actual delivered source chronology, never the fixture's expected answer.
            card,prop=max(matches,key=lambda pair:pair[0]['发生时间'])
            values.append(prop['value']);used.append(card['记忆标记'])
        return {'speech':'；'.join(values),'reported_used_memory_ids':list(dict.fromkeys(used)),
                **({'actions':[]} if '可选动作序列：' in prompt else {})}

    def complete(self,payload):
        self.calls.append(deepcopy(payload))
        system=payload['messages'][0]['content'];user=payload['messages'][-1]['content']
        # Identify the declared Writer contract, independent of persona/header
        # layout. This synthetic adapter never calls a real model.
        if 'reported_used_memory_ids' in system:value=self.writer(system+'\n'+user)
        elif '【桌宠创建范围】' in system:
            value={'persona_card':self.case['persona'],'weights':WEIGHTS,'ammo':[]}
        elif '【旧聊天联合估计合同】' in system:
            work=json.loads(user.split('【重建工作区：待核对估计与原始来源】\n')[1])
            value={'measurement':work['本次初始量测估计'],'weights':work['此前角色权重估计'],'revision_evidence':[]}
            # The normalized relationship record contains local spans, not the raw
            # wire schema. Omission explicitly retains the validated initial relation.
            value['measurement'].pop('reference_frame',None)
            if work['当前来源index']==0:
                value['weights']={**value['weights'],'Mirroring':.2,'Pragmatism':.6}
                value['measurement']['variable_deltas']['V_attr']=.3
                value['revision_evidence']=[{'target':target,'source_index':0,
                    'quote':self.case['messages'][0]['content'],
                    'reason':'合成夹具声明的修订依据；仅验证有来源的合同传递，不表示真实心理量测。'}
                    for target in ('weights','measurement')]
        else:
            if self.fail_measurement:
                return {'model':payload['model'],'choices':[{'message':{'content':'invalid synthetic JSON'},'finish_reason':'stop'}]}
            facts=json.JSONDecoder().raw_decode(user.split('【本次量测对象】\n')[1].lstrip())[0]
            text=facts['内容'][0]['可定位原文']
            properties=[]
            for row in self.case['fixture_facts']:
                if text!=self.case['messages'][row['message']]['content']:continue
                properties.append({'owner':row['owner'],'path':row['path'],'value':row['value'],
                    'kind':'assertion','certainty':'stated','evidence_text':row['quote']})
            for query in self.case['questions']:
                if text!=query['text']:continue
                properties=[{'owner':r['owner'],'path':r['path'],'value':None,'kind':'query',
                    'certainty':'unknown','evidence_text':text} for r in query['properties']]
            value={'variable_deltas':{k:(.2 if k=='V_attr' else 0.) for k in
                ('E_env','P_need','V_attr','V_rep','L_self','L_opponent')},
                'behavior_weights':WEIGHTS,'tokens':[],'confidence':.9,'semantic_properties':properties}
        return {'model':payload['model'],'choices':[{'message':{'role':'assistant','content':json.dumps(value,ensure_ascii=False)},'finish_reason':'stop'}]}

    def gateway(self,path):
        return ProviderGateway(ExistingClientTransport(endpoint='http://127.0.0.1:9/offline-callback',
            model='synthetic-petlab-example',completion=self.complete),trace_dir=path)


def seed(case):
    token=uuid.uuid4().hex
    return PetDefinition(session_id='example:'+token,owner_ref='pet:example:'+token,user_ref='user:example',
        scope_ref='scope:example:'+token,name='小书',background=case['persona'],
        base_constants=generate_base_constants(WEIGHTS),base_constants_version='synthetic-seed-only',
        b0_binding={'version':'petlab-example-disabled-b0-v1','enabled':False,'weights':None})


def configuration(definition):
    return {'version':'desktop-pet-app-config-v2','definition':definition.to_record(),
        'provider':{'endpoint':'http://127.0.0.1:9/offline-callback','model':'synthetic-petlab-example'},
        'writer_temperature':0.,'memory_delivery':{'numbered':True,'links':False,'budget_chars':None},'memory_recall_version':'desktop-pet-property-address-recall-v8',
        'mouse':{'definitions':{},'response_policy':'required'}}


def no_execution(*args):
    raise AssertionError('creation/import must not execute current actions')


class TextFileExecutor:
    """Records a real local file effect; does not pretend to animate or display UI."""
    executable_action_keys = frozenset()

    def __init__(self,path):self.path=path
    def __call__(self,action_id,contents):
        with self.path.open('a',encoding='utf8') as f:
            f.write(json.dumps({'action_id':action_id,'contents':[c.to_record() for c in contents]},ensure_ascii=False)+'\n')
        return ExecutionOutcome(completed_content_ids=tuple(c.content_id for c in contents),
            trace_ref=str(self.path),details={'adapter':'example-text-file','native_UI':False})

    def execute_composite(self,action_id,contents,*,plan,is_cancelled):
        # This adapter only realizes text, including its current serial lane.
        # It records actual file writes; it does not claim animation capability.
        require(all(c.kind=='speech' for c in contents),'text adapter received a non-text action')
        require(not is_cancelled(),'text adapter cancelled before write')
        started=wall_clock_ms();outcome=self(action_id,contents);ended=wall_clock_ms()
        rows={c.content_id:{'status':'completed','started_at_ms':started,'ended_at_ms':ended} for c in contents}
        return ExecutionOutcome(outcome.completed_content_ids,outcome.trace_ref,
            {**outcome.details,'occurred_at_wall_ms':ended,'child_results':rows,
             'action_results':{},'composite_version':plan.version})


def runner(work,fixture):
    cfg=configuration_from_record(work.setting('configuration'),require_api_key=False)
    gateway=fixture.gateway(work.path/'providers')
    return Reconstruction(work,gateway,definition=cfg.definition,coordinates=work.setting('timeline')['coordinates']),gateway


def exercise_case(root,case):
    folder=root/case['id'];folder.mkdir();fixture=OfflineFixture(case);original=seed(case)
    gateway=fixture.gateway(folder/'creation')
    try:
        binding,position=background_creation_context(session_id=original.session_id,coordinate=1000)
        measured=measure_background(description=case['persona'],session_binding=binding,current_position=position,gateway=gateway)
    finally:gateway.close()
    selected=definition_from_background(original,measured)
    require(selected.base_constants==generate_base_constants(measured['weights']),'constants drifted from original formula')
    require(selected.background==case['persona'],'full fixture persona was lost')
    empty=open_application(definition=selected,session_path=folder/'created-empty.json',action_verifier=no_execution,initial_coordinate=1000)
    try:require(not empty.runtime.events,'creation fabricated history')
    finally:empty.close()
    config=folder/'seed.json';save_configuration(config,configuration(selected));before=config.read_bytes()
    source=folder/'chat.json';source.write_text(json.dumps({'messages':case['messages']},ensure_ascii=False,indent=2),'utf8')
    source_before=source.read_bytes();work_path=folder/'work'
    preview=prepare(source=source,config=config,work=work_path,time_mode='order')
    require(preview['new_owner_ref']!=selected.owner_ref,'import reused source identity')
    with ImportWorkspace.restore(work_path) as work:
        engine,gateway=runner(work,fixture)
        original_checkpoint=work.record_step
        def interrupt_checkpoint(index,estimate):raise SimulatedInterruption('example checkpoint gap')
        work.record_step=interrupt_checkpoint
        try:
            try:engine.advance(max_records=1)
            except SimulatedInterruption:pass
            else:raise AssertionError('checkpoint interruption was not reached')
            require(work.status()['next_index']==0,'interrupted step was committed')
            saved_calls=len(fixture.calls)
        finally:
            work.record_step=original_checkpoint;gateway.close()
    with ImportWorkspace.restore(work_path) as work:
        engine,gateway=runner(work,fixture)
        try:
            require(engine.advance(max_records=1)['next_index']==1,'first checkpoint missing after restore')
            require(len(fixture.calls)==saved_calls,'completed provider return was resent after checkpoint gap')
            engine.advance(max_records=len(case['messages']))
            calls=len(fixture.calls);engine.advance(max_records=1)
            require(len(fixture.calls)==calls,'completed run redispatched')
            require(calls==2+2*len(case['messages']),'unexpected repeated fixture request')
        finally:gateway.close()
        first=work.step(0)['fit']['payload'];last=work.step(len(case['messages'])-1)['fit']['payload']
        require(first['changed']['weights'] and first['changed']['measurement'],'fixture revision was not applied')
        require(first['weights']==last['weights'],'revised weights were not carried forward')
        cfg_record=work.setting('configuration')
        role=folder/'role'
        partial=publish(work,configuration_record=cfg_record,destination=role,max_records=1)
        require(partial['published_records']==1 and not partial['complete'],'partial publication failed')
    with ImportWorkspace.restore(work_path) as work:
        done=publish(work,configuration_record=work.setting('configuration'),destination=role)
        require(done['published_records']==len(case['messages']),'prefix replay or missing history')
        require(publish(work,configuration_record=work.setting('configuration'),destination=role)==done,'duplicate publication changed result')
        require(len(fixture.calls)==calls,'publication used model')
    cfg=configuration_from_record(json.loads((role/'config.json').read_text('utf8')),require_api_key=False)
    product=PetProduct.open(definition=cfg.definition,session_path=role/'session.json',gateway=fixture.gateway(role/'providers'),
        writer_temperature=cfg.writer_temperature,numbered_memory=True,memory_recall_version=cfg.memory_recall_version)
    queries=[]
    try:
        imported=tuple(product.application.runtime.events)
        require(not product.application._product()['executions'],'old speech replayed as current execution')
        for event,row in zip(imported,case['messages']):
            expected=cfg.definition.owner_ref if row['role']=='assistant' else cfg.definition.user_ref
            require(event.actor_ref==expected,'imported speaker swapped')
            require(all(type(c) is Speech for c in event.contents),'speech became physical action')
        for index,question in enumerate(case['questions']):
            fixture.current_question=question
            product.interact(event_id='example:question:'+str(index),contents=(Speech(content_id='question',
                description=question['text'],recipient_refs=(cfg.definition.owner_ref,)),),coordinate=wall_clock_ms(),
                executor=TextFileExecutor(folder/'speech.jsonl'),completion_coordinate=wall_clock_ms)
            sources=product.application._product()['sources']
            output=next(sources[event.event_id] for event in reversed(product.application.runtime.events)
                        if sources[event.event_id]['origin']=='execution')
            evidence=output['evidence']['fact']['model_payload']['execution_receipt']['realization_evidence']
            delivered=[e.event_id for e in imported if e.event_id in evidence['delivered_memory_ids']]
            used=[e.event_id for e in imported if e.event_id in evidence['reported_used_memory_ids']]
            reply=product.latest_speech()
            require(delivered and used,'imported memory not delivered or adopted')
            require(all(r['expected'] in reply for r in question['properties']),'wrong delivered property chronology or owner')
            queries.append({'question':question['text'],'reply':reply,'delivered_imported_ids':delivered,'reported_used_imported_ids':used})
        require(not product.pending_reply_ids(),'uncompleted reply after example')
    finally:product.close()
    product=PetProduct.open(definition=cfg.definition,session_path=role/'session.json',gateway=fixture.gateway(role/'providers'),
        writer_temperature=cfg.writer_temperature,memory_recall_version=cfg.memory_recall_version)
    try:
        require(len(product.application.runtime.events)==len(case['messages'])+2*len(queries),'cold history lost')
        require(not product.pending_reply_ids(),'cold restore created pending reply')
    finally:product.close()
    require(source.read_bytes()==source_before and config.read_bytes()==before,'original source/config changed')
    return {'id':case['id'],'description':case['description'],'source':source.relative_to(root).as_posix(),
        'fixture_calls':len(fixture.calls),'checkpoint_resume_no_extra_calls':True,'duplicate_publication_no_extra_events':True,
        'cold_restore':True,'queries':queries}


def exercise_failures(root,case):
    folder=root/'negative';folder.mkdir();fixture=OfflineFixture(case);fixture.fail_measurement=True
    config=folder/'seed.json';save_configuration(config,configuration(seed(case)))
    source=folder/'chat.json';source.write_text(json.dumps({'messages':case['messages']},ensure_ascii=False),'utf8')
    prepare(source=source,config=config,work=folder/'work',time_mode='order')
    with ImportWorkspace.restore(folder/'work') as work:
        engine,gateway=runner(work,fixture)
        try:
            for _ in range(2):
                try:engine.advance(max_records=1)
                except ExecutionUncertain:pass
                else:raise AssertionError('malformed measurement was accepted')
            require(len(fixture.calls)==2 and work.status()['next_index']==0,'failure resent or advanced')
        finally:gateway.close()
    for index,data in enumerate(([{'role':'tool','content':'unmapped'}],
            [{'role':'user','content':[{'image':'not-text'}]}],
            [{'id':'duplicate','role':'user','content':'a'},{'id':'duplicate','role':'assistant','content':'b'}])):
        path=folder/f'invalid-{index}.json';path.write_text(json.dumps(data),'utf8')
        try:read_transcript(path)
        except ValueError:pass
        else:raise AssertionError('unsupported input accepted')
    return True


def check_isolation(root,cases):
    from petlab_app.imported_source import ingest
    products=[]
    try:
        for case in cases[:2]:
            role=root/case['id']/'role';cfg=configuration_from_record(json.loads((role/'config.json').read_text('utf8')),require_api_key=False)
            products.append(PetProduct.open(definition=cfg.definition,session_path=role/'session.json',
                gateway=OfflineFixture(case).gateway(role/'providers'),writer_temperature=cfg.writer_temperature,memory_recall_version=cfg.memory_recall_version))
        one,two=[p.application for p in products];event=one.runtime.events[0]
        row=thaw_model_data(one._product()['sources'][event.event_id]);before=two.runtime.session.state.fingerprint
        require(two.definition.scope_ref!=one.definition.scope_ref,'scope reused')
        try:ingest(two,event=event,evidence=row['evidence'],measurement=row['measurement'])
        except ValueError:pass
        else:raise AssertionError('foreign imported source accepted')
        require(two.runtime.session.state.fingerprint==before,'rejected foreign source changed state')
    finally:
        for product in products:product.close()
    return True


def run(destination):
    destination=Path(destination).resolve();destination.mkdir(parents=True,exist_ok=False)
    cases=load_cases();results=[]
    for case in cases:results.append(exercise_case(destination,case))
    failure=exercise_failures(destination,cases[0]);isolation=check_isolation(destination,cases)
    report={'passed':True,'scope':'deterministic fixture, real package/provider/receipt/history pipeline; no native UI or LLM quality claim',
        'network_requests':0,'cases':results,'failed_return_stops_without_resend':failure,
        'invalid_inputs_rejected':True,'source_scope_isolated':isolation}
    (destination/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n','utf8')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path('output')/('petlab-examples-'+uuid.uuid4().hex[:10]))
    args=parser.parse_args()
    report=run(args.output)
    print(json.dumps({'passed':report['passed'],'cases':len(report['cases']),'network_requests':0,
        'report':str(args.output.resolve()/'report.json')},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
