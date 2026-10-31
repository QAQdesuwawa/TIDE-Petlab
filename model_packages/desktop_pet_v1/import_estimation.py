"""Source-grounded joint reconstruction estimates; no runtime state writes.

This optional codec refines a previously parsed event estimate and the five
creation weights together. Constants are always computed by the old initializer.
It is not the online measurement calibration or a formal-history rewrite.
"""
import json
from collections.abc import Mapping
from framework.model_api import model_data_fingerprint, thaw_model_data
from .creation import parse_background
from model_packages.cx_six_seven_v1.semantic_measurement import _strip_fences

VERSION = 'desktop-pet-import-joint-estimate-v5'


def _weights(weights):
    parsed = parse_background(json.dumps({'persona_card':'reconstruction estimate',
        'weights':thaw_model_data(weights), 'ammo':[]}, ensure_ascii=False, allow_nan=False))
    return parsed['weights'], parsed['base_constants']


class JointFitCodec:
    require_json = True
    temperature = 0.0

    def __init__(self, base, *, persona, previous_weights, initial_measurement,
                 current_index, sources, max_source_chars=16384, initial_properties=None):
        if not isinstance(persona, str) or not persona.strip():raise ValueError('full persona required')
        if not isinstance(initial_measurement, Mapping):raise ValueError('initial parsed measurement required')
        self.base = base;self.persona = persona
        self.previous_weights = _weights(previous_weights)[0]
        self.initial_measurement = thaw_model_data(initial_measurement)
        self.initial_properties = None if initial_properties is None else thaw_model_data(initial_properties)
        self.current_index = current_index
        self.sources = {}
        for source in sources:
            if (not isinstance(source, dict) or set(source) != {'index','role','text'}
                    or type(source['index']) is not int or source['index'] < 0
                    or source['role'] not in ('user','assistant')
                    or not isinstance(source['text'],str) or not source['text'].strip()
                    or source['index'] in self.sources):
                raise ValueError('joint estimate sources require distinct actual transcript records')
            self.sources[source['index']] = dict(source)
        if type(current_index) is not int or current_index not in self.sources:
            raise ValueError('current transcript source required')
        if any(index > current_index for index in self.sources):
            raise ValueError('joint estimate cannot read future records')
        if type(max_source_chars) is not int or max_source_chars <= 0:
            raise ValueError('joint estimate source budget must be explicit positive characters')
        if len(json.dumps(list(self.sources.values()),ensure_ascii=False,separators=(',',':'))) > max_source_chars:
            raise ValueError('joint estimate source context exceeds configured budget; select source references explicitly')
        self.max_source_chars = max_source_chars
        self.request_version = base.request_version + ':' + VERSION

    def build_request(self, *, event, recall_hint=None):
        self._verify_event(event)
        system,user = self.base.build_request(event=event, recall_hint=recall_hint)
        # The base codec's output contract describes the measurement child,
        # not a second competing root response. Preserve its definitions.
        if system.count('输出一个JSON对象：')!=1:
            raise ValueError('import measurement contract output anchor differs')
        system=system.replace('输出一个JSON对象：','measurement 子对象包含：',1)
        system=system.replace('## 输出合同','## measurement 子对象合同',1)
        contract = {
            '职责':'在独立的旧聊天重建工作区校正估计。完整人设是约束；原始话语是已经发生的证据。',
            '估计对象':'五模板权重描述角色较稳定倾向的估计，measurement描述当前事件的作用。当前测得的幅度与角色倾向分别理解；一次情绪反应可只支持修改量测。',
            '依据':'根据当前原文及所给较早原文，对初始量测与角色权重一起核对；有证据时提出相应修订，证据不足保留对应估计。每项修订以来源行及连续原文定位。',
            '数值边界':'measurement沿原量测合同；weights沿原完整五模板非负归一权重合同。下游使用原公式生成七常量。这些是可修订导入估计，与在线运行根状态不同。',
            '格式':{'measurement':'按上述原codec输出合同填写完整对象',
                'weights':self.previous_weights,
                'revision_evidence':[{'target':'weights 或 measurement','source_index':'实际来源index',
                    'quote':'该来源中唯一连续原文','reason':'这项证据如何支持本次修订'}]},
            '输出范围':'返回measurement、weights、revision_evidence三个字段。measurement用于当前事件，之前的原文作为因果参照。',
            '上下文边界':'较早来源是导入宿主选定的有界原文参照，未提供的部分不代表不存在；当前可用证据不足时保留估计。',
        }
        root_contract=(
            '# 旧聊天重建：联合校正估计\n'
            '本次输出一个完整JSON根对象，根字段是 measurement、weights、revision_evidence。\n'
            'measurement 是当前事件量测子对象，内含下文定义的 variable_deltas、behavior_weights、tokens、confidence、semantic_properties，以及适用时的reference_frame。\n'
            'weights 是角色五模板权重对象；revision_evidence 是本次修订的原文依据数组。\n'
            '证据分区：measurement.semantic_properties 的 evidence_text 定位于【本次量测对象】的可定位原文；当前话语的复述也引用当前复述正文。较早来源帮助解释属性归属和指代。\n'
            'revision_evidence 的 quote 则定位于其 source_index 对应的当前或较早来源，用于解释估计修订。这两种证据分别对应各自的原文范围。\n'
            'reference_frame 为可选修订字段；省略表示沿用本次已封存的初始关系量测，提供时按原合同重新定位。\n'
            '结构：{"measurement":{当前事件量测字段},"weights":{五模板权重},"revision_evidence":[修订证据]}\n\n'
            '## measurement 的原语义与字段定义\n')
        system=root_contract+system+'\n【旧聊天联合估计合同】\n'+json.dumps(contract,ensure_ascii=False,separators=(',',':'))
        initial={key:self.initial_measurement[key] for key in
                    ('variable_deltas','behavior_weights','tokens','confidence','reference_frame') if key in self.initial_measurement}
        if self.initial_properties is not None:
            from .semantic import evidence_source
            text=evidence_source(event)
            initial['semantic_properties']=[{**{k:v for k,v in row.items() if k!='span'},
                'evidence_text':text[row['span'][0]:row['span'][1]]}
                for row in self.initial_properties['properties']]
        work = {'完整人设':self.persona, '当前来源index':self.current_index,
                '当前来源角色':self.sources[self.current_index]['role'],
                '当前原文位置':'上面的本次量测对象，source_index使用当前来源index',
                '此前角色权重估计':self.previous_weights,
                '本次初始量测估计':initial,
                '实际较早来源':[row for i,row in self.sources.items() if i!=self.current_index]}
        return system, user+'\n【重建工作区：待核对估计与原始来源】\n'+json.dumps(work,ensure_ascii=False,separators=(',',':'))

    def parse_event_response(self, raw, *, event):
        self._verify_event(event)
        data = json.loads(_strip_fences(raw))
        if not isinstance(data,dict) or set(data) != {'measurement','weights','revision_evidence'}:
            raise ValueError('joint estimate response fields differ')
        weights,constants = _weights(data['weights'])
        body = json.dumps(data['measurement'],ensure_ascii=False,allow_nan=False)
        measurement = self.base.parse_event_response(body,event=event)
        if callable(getattr(measurement, 'as_record', None)):
            measurement = measurement.as_record()
        if not isinstance(measurement, Mapping):raise ValueError('joint estimate measurement is invalid')
        # This is a refinement of an existing estimate. An omitted optional
        # relation is unchanged, not a new measurement of the parser default.
        # Explicit relation revisions still go through the original parser.
        if 'reference_frame' not in data['measurement'] and 'reference_frame' in self.initial_measurement:
            measurement={**measurement,'reference_frame':self.initial_measurement['reference_frame']}
        properties = None
        if getattr(self.base,'memory_recall_version',None)=='desktop-pet-property-address-recall-v8':
            from .address_semantics import parse_response
            properties=parse_response(body,event)
        evidence = data['revision_evidence']
        if not isinstance(evidence,list) or len(evidence)>32:raise ValueError('joint revision evidence exceeds contract')
        located = []
        for row in evidence:
            if not isinstance(row,dict) or set(row) != {'target','source_index','quote','reason'}:
                raise ValueError('joint revision evidence fields differ')
            index = row['source_index'];quote = row['quote'];reason = row['reason']
            if row['target'] not in ('weights','measurement') or type(index) is not int or index not in self.sources:
                raise ValueError('joint revision evidence source differs')
            source = self.sources[index]
            if not isinstance(quote,str) or not quote or source['text'].count(quote)!=1:
                raise ValueError('joint revision evidence requires unique actual text')
            if not isinstance(reason,str) or not reason.strip() or len(reason)>512:
                raise ValueError('joint revision requires a bounded rationale')
            at = source['text'].index(quote)
            located.append({'target':row['target'],'source_index':index,
                'source_fingerprint':model_data_fingerprint(source),'span':[at,at+len(quote)],'reason':reason})
        changed = {'weights':weights != self.previous_weights,
                   'measurement':model_data_fingerprint(measurement) != model_data_fingerprint(self.initial_measurement)}
        if self.initial_properties is not None:
            changed['measurement'] |= model_data_fingerprint(properties)!=model_data_fingerprint(self.initial_properties)
        for target,was_changed in changed.items():
            if was_changed and not any(row['target']==target for row in located):
                raise ValueError('joint revision lacks evidence for '+target)
        result = {'version':VERSION,'current_index':self.current_index,
            'persona_fingerprint':model_data_fingerprint(self.persona),
            'previous_weights_fingerprint':model_data_fingerprint(self.previous_weights),
            'initial_measurement_fingerprint':model_data_fingerprint(self.initial_measurement),
            'weights':weights,'base_constants':constants,'measurement':thaw_model_data(measurement),'properties':properties,
            'changed':changed,'revision_evidence':located,
            'authority':{'state_write':False,'history_write':False,'send':False}}
        return dict(result,fingerprint=model_data_fingerprint(result))

    def _verify_event(self, event):
        from .events import PetEvent
        from .semantic import evidence_source
        if not isinstance(event, PetEvent):raise ValueError('joint estimate requires a typed transcript event')
        expected_role = 'assistant' if event.event_kind == 'npc_output' else 'user'
        current = self.sources[self.current_index]
        if current['role'] != expected_role or current['text'] != evidence_source(event):
            raise ValueError('joint estimate current source differs from event text or perspective')
