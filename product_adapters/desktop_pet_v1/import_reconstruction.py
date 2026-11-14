"""Sequential jointly revised estimates; no formal state or historical effects yet."""
import json
from framework.model_api import model_data_fingerprint as fingerprint
from petlab_app.composition import PetDefinition,background_creation_context
from petlab_app.imported_source import build
from model_packages.desktop_pet_v1.creation import build_background_request,parse_background,VERSION as BACKGROUND
from model_packages.desktop_pet_v1.semantic import DesktopPetConstrainedSemanticCodec
from model_packages.desktop_pet_v1.address_semantics import AddressPropertyCodec,parse_query_response,parse_response as parse_properties
from model_packages.desktop_pet_v1.import_estimation import ImportedEventPropertyCodec, JointFitCodec
from .import_provider import estimate

VERSION='desktop-pet-import-reconstruction-v1'


def recover_query_properties(raw,event):
    properties=parse_query_response(raw,event,unknown_certainty=True)
    if not properties.get('property_normalizations'):
        raise ValueError('no query value to recover')
    return properties


class Reconstruction:
    def __init__(self,workspace,gateway,*,definition,coordinates,reuse_only=False):
        if not isinstance(definition,PetDefinition):raise TypeError('explicit import role definition required')
        status=workspace.status()
        if status['definition_ref']!=definition.fingerprint:raise ValueError('reconstruction role differs')
        if (not isinstance(coordinates,(list,tuple)) or len(coordinates)!=status['message_count']
                or any(type(x) is not int or x<0 for x in coordinates)
                or any(a>b for a,b in zip(coordinates,coordinates[1:]))):
            raise ValueError('explicit nondecreasing reconstruction coordinates required')
        self.workspace=workspace;self.gateway=gateway;self.definition=definition
        self.pair=workspace.setting('npc_pair')
        self.separate_expression_prompt=workspace.setting('pair_expression_contract')=='independent-expression-v2'
        self.reuse_only=reuse_only
        self.coordinates=tuple(coordinates)
        workspace.setting('reconstruction',{'version':VERSION,'definition':definition.to_record(),
            'coordinates':list(coordinates),'transport_binding':dict(gateway.transport.binding)})

    def _call(self,key,source_ref,coordinate,contract,messages,parse,*,recover_parse=None,source_digest=None):
        binding,position=background_creation_context(session_id=self.definition.session_id,coordinate=coordinate)
        return estimate(self.workspace,self.gateway,operation_key=key,session_binding=binding,
            current_position=position,source_ref=source_ref,codec_contract=contract,
            messages=messages,parse=parse,recover_parse=recover_parse,reuse_only=self.reuse_only,
            source_receipt_refs=(source_digest or self.workspace.status()['source_digest'],))

    def _persona_sources(self, *, ensure_speaker_coverage=False):
        count = self.workspace.status()['message_count']
        points = min(count, 24)
        indices = [0] if points == 1 else [i * (count - 1) // (points - 1) for i in range(points)]
        if ensure_speaker_coverage:
            # Regular spacing can land only on user rows in alternating dialogue.
            # Reserve up to two real AI turns before allocating the text budget.
            required = self.workspace.assistant_speech_indices(limit=2)
            indices = list(dict.fromkeys([*required, *indices]))[:points]
        rows = []
        remaining = 16000
        for index in indices:
            message = self.workspace.message(index)
            if message.event_tag is not None:
                continue
            row = {'index': index, 'role': message.role, 'text': message.text}
            cost = len(json.dumps(row, ensure_ascii=False, separators=(',', ':'))) + 1
            if cost <= remaining:
                rows.append(row)
                remaining -= cost
        if not any(row['role'] == 'assistant' for row in rows):
            raise ValueError('personality inference needs an AI utterance in the bounded sample')
        return sorted(rows, key=lambda row: row['index'])

    def initialize(self):
        saved=self.workspace.setting('initial_background')
        inference=self.workspace.setting('persona_inference')
        if self.pair is not None and not self.definition.background:
            from model_packages.cx_six_seven_v1.preference_parameters import B0_TARGETS
            from model_packages.desktop_pet_v1.import_persona import NEUTRAL
            result={'payload':{'persona_card':NEUTRAL,'weights':{name:1/len(B0_TARGETS) for name in B0_TARGETS}}}
        elif inference is None:
            system,user=build_background_request('角色名称：'+self.definition.name+'\n'+self.definition.background)
            result=self._call('initial-background','import-background:'+self.definition.fingerprint,
                self.coordinates[0],BACKGROUND,[{'role':'system','content':system},{'role':'user','content':user}],parse_background)
        else:
            from model_packages.desktop_pet_v1 import import_persona
            policy=import_persona.revision_policy(inference)
            if self.definition.background:
                raise ValueError('inferred persona requires its original empty seed')
            rows=self._persona_sources(ensure_speaker_coverage=policy is not None)
            result=self._call('initial-background','import-background:'+self.definition.fingerprint,
                self.coordinates[0],import_persona.VERSION,import_persona.request(rows),
                lambda raw:import_persona.parse(raw,rows),
                recover_parse=lambda raw:import_persona.recover_initial(raw,rows))
        if saved is not None:
            if fingerprint(saved)!=fingerprint(result):raise ValueError('initial background differs from sealed provider result')
            # Settings serialize keys in sorted order. Reuse the exact parsed
            # provider payload so an interrupted first joint request rebuilds
            # the original prompt bytes instead of a reordered JSON string.
            return result
        return self.workspace.setting('initial_background',result)

    def advance(self,*,max_records=1):
        if type(max_records) is not int or max_records<1:raise ValueError('positive import step budget required')
        initial=self.initialize();processed=0
        inference=self.workspace.setting('persona_inference')
        inferred=inference is not None
        revision_policy=None
        if inferred:
            from model_packages.desktop_pet_v1.import_persona import revision_policy as infer_policy
            revision_policy=infer_policy(inference)
        if self.pair is not None:
            from model_packages.desktop_pet_v1.import_persona import KEEP_POLICY
            revision_policy=KEEP_POLICY
            inferred=not self.definition.background
        persona=initial['payload']['persona_card'] if inferred else self.definition.background
        while processed<max_records:
            status=self.workspace.status();index=status['next_index']
            if index==status['message_count']:break
            previous=self.workspace.step(index-1) if index else None
            weights=initial['payload']['weights'] if previous is None else previous['fit']['payload']['weights']
            expression=None
            if self.pair is not None:
                expression=[] if previous is None else previous['fit']['payload']['expression_observations']
            message=self.workspace.message(index)
            digest,original_index=self.workspace.source_location(index)
            event,_=build(definition=self.definition,message=message,source_digest=digest,
                coordinate=self.coordinates[index],estimate_ref='reconstruction:'+str(index),pair=self.pair,
                original_index=original_index if original_index!=index else None)
            sources=[];remaining=16384
            for i in range(index,-1,-1):
                row=self.workspace.message(i)
                item={'index':i,'role':row.role,'text':row.text}
                if row.event_tag is not None:
                    item['event'] = row.event_tag.source_record()
                cost=len(json.dumps(item,ensure_ascii=False,separators=(',',':')))+1
                if cost>remaining-2:
                    if i==index:raise ValueError('current message exceeds import context budget; explicitly split or configure it')
                    break
                sources.append(item);remaining-=cost
            sources.reverse()
            references=[];chars=0
            for row in reversed(sources[:-1]):
                actor = '角色' if row['role']=='assistant' else ('另一位NPC' if self.pair is not None else '用户')
                kind = row.get('event', {}).get('kind')
                label = {'action': '动作记录', 'interaction': '交互记录'}.get(kind, '')
                text = actor + label + '：' + row['text']
                if chars+len(text)+1>2048:break
                references.append(text);chars+=len(text)+1
            prior='\n'.join(reversed(references))
            numeric_codec = DesktopPetConstrainedSemanticCodec()
            if message.event_tag is None:
                base=AddressPropertyCodec(numeric_codec,referent_text=prior)
            else:
                base=ImportedEventPropertyCodec(numeric_codec, tag=message.event_tag, referent_text=prior)
            system,user=base.build_request(event=event,recall_hint=None)
            if self.pair is not None:
                system += ('\n【发言方绑定】这是两位NPC之间的旧对话。current_npc 指本角色，'
                           'user 是既有量测合同的对端别名，在本工作区指另一位NPC。')
            def parse_initial(text, property_parser=parse_properties):
                numeric=base.parse_event_response(text,event=event)
                if numeric is None:raise ValueError('invalid initial import measurement')
                return {'measurement':numeric.as_record(),'properties':property_parser(text,event)}
            raw=self._call(f'{index}:initial',event.event_id,event.position.coordinate,base.request_version,
                [{'role':'system','content':system},{'role':'user','content':user}],parse_initial,
                recover_parse=lambda text:parse_initial(text,recover_query_properties),source_digest=digest)
            joint=JointFitCodec(base,persona=persona,persona_inferred=inferred,revision_policy=revision_policy,
                previous_weights=weights,
                initial_measurement=raw['payload']['measurement'],initial_properties=raw['payload']['properties'],
                current_index=index,sources=sources,expression_observations=expression,
                separate_expression_prompt=self.separate_expression_prompt)
            system,user=joint.build_request(event=event,recall_hint=None)
            fit=self._call(f'{index}:joint:'+fingerprint(joint.request_version)[:16],event.event_id,event.position.coordinate,joint.request_version,
                [{'role':'system','content':system},{'role':'user','content':user}],
                lambda text:joint.parse_event_response(text,event=event),
                recover_parse=lambda text:joint.parse_event_response(text,event=event,
                    property_parser=recover_query_properties,retain_unbacked_measurement=True),source_digest=digest)
            step={'version':VERSION,'index':index,'event_fingerprint':event.fingerprint,
                'source_message_fingerprint':fingerprint(message.record()),
                'previous_step_fingerprint':None if previous is None else fingerprint(previous),
                'initial':raw,'fit':fit}
            self.workspace.record_step(index,step)
            processed+=1
        return {**self.workspace.status(),'processed_this_call':processed}
