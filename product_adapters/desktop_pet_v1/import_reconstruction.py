"""Sequential jointly revised estimates; no formal state or historical effects yet."""
import json
from framework.model_api import model_data_fingerprint as fingerprint
from petlab_app.composition import PetDefinition,background_creation_context
from petlab_app.imported_source import build
from model_packages.desktop_pet_v1.creation import build_background_request,parse_background,VERSION as BACKGROUND
from model_packages.desktop_pet_v1.semantic import DesktopPetConstrainedSemanticCodec
from model_packages.desktop_pet_v1.address_semantics import AddressPropertyCodec,parse_response as parse_properties
from model_packages.desktop_pet_v1.import_estimation import JointFitCodec
from .import_provider import estimate

VERSION='desktop-pet-import-reconstruction-v1'


class Reconstruction:
    def __init__(self,workspace,gateway,*,definition,coordinates):
        if not isinstance(definition,PetDefinition):raise TypeError('explicit import role definition required')
        status=workspace.status()
        if status['definition_ref']!=definition.fingerprint:raise ValueError('reconstruction role differs')
        if (not isinstance(coordinates,(list,tuple)) or len(coordinates)!=status['message_count']
                or any(type(x) is not int or x<0 for x in coordinates)
                or any(a>b for a,b in zip(coordinates,coordinates[1:]))):
            raise ValueError('explicit nondecreasing reconstruction coordinates required')
        self.workspace=workspace;self.gateway=gateway;self.definition=definition
        self.coordinates=tuple(coordinates)
        workspace.setting('reconstruction',{'version':VERSION,'definition':definition.to_record(),
            'coordinates':list(coordinates),'transport_binding':dict(gateway.transport.binding)})

    def _call(self,key,source_ref,coordinate,contract,messages,parse):
        binding,position=background_creation_context(session_id=self.definition.session_id,coordinate=coordinate)
        return estimate(self.workspace,self.gateway,operation_key=key,session_binding=binding,
            current_position=position,source_ref=source_ref,codec_contract=contract,
            messages=messages,parse=parse,source_receipt_refs=(self.workspace.status()['source_digest'],))

    def initialize(self):
        saved=self.workspace.setting('initial_background')
        if saved is not None:return saved
        system,user=build_background_request('角色名称：'+self.definition.name+'\n'+self.definition.background)
        result=self._call('initial-background','import-background:'+self.definition.fingerprint,
            self.coordinates[0],BACKGROUND,[{'role':'system','content':system},{'role':'user','content':user}],parse_background)
        return self.workspace.setting('initial_background',result)

    def advance(self,*,max_records=1):
        if type(max_records) is not int or max_records<1:raise ValueError('positive import step budget required')
        initial=self.initialize();processed=0
        while processed<max_records:
            status=self.workspace.status();index=status['next_index']
            if index==status['message_count']:break
            previous=self.workspace.step(index-1) if index else None
            weights=initial['payload']['weights'] if previous is None else previous['fit']['payload']['weights']
            message=self.workspace.message(index)
            event,_=build(definition=self.definition,message=message,source_digest=status['source_digest'],
                coordinate=self.coordinates[index],estimate_ref='reconstruction:'+str(index))
            sources=[];remaining=16384
            for i in range(index,-1,-1):
                row=self.workspace.message(i)
                item={'index':i,'role':row.role,'text':row.text}
                cost=len(json.dumps(item,ensure_ascii=False,separators=(',',':')))+1
                if cost>remaining-2:
                    if i==index:raise ValueError('current message exceeds import context budget; explicitly split or configure it')
                    break
                sources.append(item);remaining-=cost
            sources.reverse()
            references=[];chars=0
            for row in reversed(sources[:-1]):
                text=('角色' if row['role']=='assistant' else '用户')+'：'+row['text']
                if chars+len(text)+1>2048:break
                references.append(text);chars+=len(text)+1
            prior='\n'.join(reversed(references))
            base=AddressPropertyCodec(DesktopPetConstrainedSemanticCodec(),referent_text=prior)
            system,user=base.build_request(event=event,recall_hint=None)
            def parse_initial(text):
                numeric=base.parse_event_response(text,event=event)
                if numeric is None:raise ValueError('invalid initial import measurement')
                return {'measurement':numeric.as_record(),'properties':parse_properties(text,event)}
            raw=self._call(f'{index}:initial',event.event_id,event.position.coordinate,base.request_version,
                [{'role':'system','content':system},{'role':'user','content':user}],parse_initial)
            joint=JointFitCodec(base,persona=self.definition.background,previous_weights=weights,
                initial_measurement=raw['payload']['measurement'],initial_properties=raw['payload']['properties'],
                current_index=index,sources=sources)
            system,user=joint.build_request(event=event,recall_hint=None)
            fit=self._call(f'{index}:joint:'+fingerprint(joint.request_version)[:16],event.event_id,event.position.coordinate,joint.request_version,
                [{'role':'system','content':system},{'role':'user','content':user}],
                lambda text:joint.parse_event_response(text,event=event))
            step={'version':VERSION,'index':index,'event_fingerprint':event.fingerprint,
                'source_message_fingerprint':fingerprint(message.record()),
                'previous_step_fingerprint':None if previous is None else fingerprint(previous),
                'initial':raw,'fit':fit}
            self.workspace.record_step(index,step)
            processed+=1
        return {**self.workspace.status(),'processed_this_call':processed}
