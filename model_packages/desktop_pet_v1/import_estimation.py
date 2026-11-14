"""Source-grounded joint reconstruction estimates; no runtime state writes.

This optional codec refines a previously parsed event estimate and the five
creation weights together. Constants are always computed by the old initializer.
It is not the online measurement calibration or a formal-history rewrite.
"""
import json
from collections.abc import Mapping
from framework.model_api import model_data_fingerprint, thaw_model_data
from .address_semantics import AddressPropertyCodec
from .creation import parse_background
from .import_event import ImportedEventTag
from model_packages.cx_six_seven_v1.semantic_measurement import _strip_fences

VERSION = 'desktop-pet-import-joint-estimate-v5'
TYPED_VERSION = 'desktop-pet-import-joint-estimate-v6-typed'


class ImportedEventPropertyCodec(AddressPropertyCodec):
    """Attach historical event type to the original measurement contract."""

    def __init__(self, base, *, tag, referent_text=''):
        super().__init__(base, referent_text=referent_text)
        self.tag = tag
        self.request_version += ':imported-event-type-v1'

    def build_request(self, *, event, recall_hint):
        system, user = super().build_request(event=event, recall_hint=recall_hint)
        source_type = {'父类': self.tag.kind, '包内类型': self.tag.semantic_key,
                       '原始来源类型': self.tag.source_record()}
        system += ('\n【旧事件来源】当前正文是外部记录中的已发生事实描述。'
                   '类型来自来源绑定，证据区间定位到描述原文。按有依据的父层或细层填写属性，'
                   '已发生的外部事实与本机执行分别理解。')
        return system, user + '\n【外部事件类型】' + json.dumps(source_type, ensure_ascii=False)


def _weights(weights):
    parsed = parse_background(json.dumps({'persona_card':'reconstruction estimate',
        'weights':thaw_model_data(weights), 'ammo':[]}, ensure_ascii=False, allow_nan=False))
    return parsed['weights'], parsed['base_constants']


class JointFitCodec:
    require_json = True
    temperature = 0.0

    def __init__(self, base, *, persona, previous_weights, initial_measurement,
                 current_index, sources, max_source_chars=16384, initial_properties=None, persona_inferred=False, revision_policy=None, expression_observations=None, separate_expression_prompt=False):
        if not isinstance(persona, str) or not persona.strip():raise ValueError('full persona required')
        if not isinstance(initial_measurement, Mapping):raise ValueError('initial parsed measurement required')
        self.base = base;self.persona = persona
        self.persona_inferred = persona_inferred
        self.expression_observations = expression_observations
        self.separate_expression_prompt = separate_expression_prompt
        self.keep_without_evidence = revision_policy == 'keep-without-evidence-v1'
        if revision_policy is not None and ((not persona_inferred and expression_observations is None) or not self.keep_without_evidence):
            raise ValueError('unknown import revision policy')
        self.previous_weights = _weights(previous_weights)[0]
        self.initial_measurement = thaw_model_data(initial_measurement)
        self.initial_properties = None if initial_properties is None else thaw_model_data(initial_properties)
        self.current_index = current_index
        self.sources = {}
        for source in sources:
            if (not isinstance(source, dict) or set(source) not in ({'index','role','text'}, {'index','role','text','event'})
                    or type(source['index']) is not int or source['index'] < 0
                    or source['role'] not in ('user','assistant')
                    or not isinstance(source['text'],str) or not source['text'].strip()
                    or source['index'] in self.sources):
                raise ValueError('joint estimate sources require distinct actual transcript records')
            if 'event' in source:
                ImportedEventTag.read(source['event'], role=source['role'])
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
        self.version = TYPED_VERSION if any('event' in source for source in self.sources.values()) else VERSION
        self.request_version = base.request_version + ':' + self.version
        if persona_inferred:
            self.request_version += ':inferred-persona-v1'
        if self.keep_without_evidence:
            self.request_version += ':' + revision_policy
        if expression_observations is not None:
            self.request_version += ':paired-expression-evolution-v1'
            if separate_expression_prompt:
                self.request_version += ':independent-expression-v2'

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
        if self.version == TYPED_VERSION:
            contract['职责'] = ('在独立工作区重建历史。完整人设约束角色估计，'
                                '较早材料中的 event 标记用于区分话语、动作和交互。')
        if self.persona_inferred:
            contract['职责'] = ('在独立工作区重建历史。表达倾向只是旧 AI 发言的有证据估计。'
                                '原文可推翻这个估计；其余设定保持未知。')
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
        if self.keep_without_evidence:
            root_contract = ('没有可定位的修订依据时，返回 {"measurement":null,"weights":null,"revision_evidence":[]}。'
                             '这表示保留本次已验证的初测和上一步权重。下文的字段说明适用于有依据的修订。' + '\n' + root_contract)
        if self.expression_observations is not None:
            root_contract = root_contract.replace('measurement、weights、revision_evidence。','measurement、weights、revision_evidence、expression_revision。')
            root_contract = root_contract.replace('"revision_evidence":[]}', '"revision_evidence":[],"expression_revision":null}')
            root_contract = root_contract.replace('[修订证据]}', '[修订证据],"expression_revision":null}')
            if self.separate_expression_prompt:
                root_contract = root_contract.replace(
                    '没有可定位的修订依据时，返回 {"measurement":null,"weights":null,"revision_evidence":[],"expression_revision":null}。',
                    '数值没有修订依据时，measurement 和 weights 填 null，revision_evidence 填 []。'
                    '无论数值是否变化，都独立判断表达。')
                root_contract = root_contract.replace('"expression_revision":null}',
                    '"expression_revision":[{"summary":"面对意外夸奖时先短暂停顿，再用反问确认。",'
                    '"evidence":[{"source_index":4,"quote":"……真的？"},{"source_index":8,"quote":"你是认真的？"}]}]}')
                root_contract = root_contract.replace('结构：','格式示例，示例话语和行号不属于本案：')
            contract['输出范围'] = '返回根合同的四个字段。数值修订与表达修订各自独立。'
            contract['职责'] = '用旧对话重建当前NPC的角色估计。完整作者人设如有，始终保留为约束。'
            contract['表达修订'] = ('expression_revision 为 null 表示保留上一步表达档案。只在本条是当前NPC自己的话，'
                '且它与较早的本人发言一起提供新的依据时，才给出更新后的完整档案。最多四项，每项格式为'
                '{"summary":"你……","evidence":[{"source_index":0,"quote":"逐字原文"}]}。'
                '每项至少引用两条不同的本人话语，整份修订至少引用当前这条。'
                '只归纳可观察的措辞或反应习惯；剧情事实、角色临时动作和动作候选走各自材料。')
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
        if self.persona_inferred:
            work['旧聊天估计的表达倾向'] = work.pop('完整人设')
        if self.expression_observations is not None:
            work['上一步表达档案'] = [item['summary'] for item in self.expression_observations]
            work['发言方解释'] = {'assistant':'当前NPC本人','user':'另一位NPC'}
        if 'event' in self.sources[self.current_index]:
            work['当前来源类型'] = self.sources[self.current_index]['event']
        return system, user+'\n【重建工作区：待核对估计与原始来源】\n'+json.dumps(work,ensure_ascii=False,separators=(',',':'))

    def parse_event_response(self, raw, *, event, property_parser=None, retain_unbacked_measurement=False):
        self._verify_event(event)
        data = json.loads(_strip_fences(raw))
        fields = {'measurement','weights','revision_evidence'}
        if self.expression_observations is not None:
            fields.add('expression_revision')
        if not isinstance(data,dict) or set(data) != fields:
            raise ValueError('joint estimate response fields differ')
        expression = None
        if self.expression_observations is not None:
            from .import_persona import revise_expression
            expression = revise_expression(self.expression_observations, data['expression_revision'],
                                           sources=self.sources, index=self.current_index)
        if self.keep_without_evidence and data['revision_evidence'] == []:
            if any(value is not None and not isinstance(value, dict) for value in (data['weights'],data['measurement'])):
                raise ValueError('unchanged import estimates require null or a candidate object')
            weights, constants = _weights(self.previous_weights)
            return self._result(weights, constants, self.initial_measurement, self.initial_properties,
                                {'weights':False,'measurement':False}, [],
                                disposition='retained_no_revision_evidence', expression=expression)
        weights,constants = _weights(data['weights'])
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
        if (retain_unbacked_measurement and self.expression_observations is not None
                and any(row['target']=='weights' for row in located)
                and not any(row['target']=='measurement' for row in located)):
            if data['measurement'] is not None and not isinstance(data['measurement'],dict):
                raise ValueError('unchanged import measurement must be null or a candidate object')
            return self._result(weights, constants, self.initial_measurement, self.initial_properties,
                                {'weights':weights!=self.previous_weights,'measurement':False}, located,
                                disposition='retained_unbacked_measurement', expression=expression)
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
            if property_parser is None:
                from .address_semantics import parse_response as property_parser
            properties=property_parser(body,event)
        changed = {'weights':weights != self.previous_weights,
                   'measurement':model_data_fingerprint(measurement) != model_data_fingerprint(self.initial_measurement)}
        if self.initial_properties is not None:
            # Recovery audit material is provenance, not an estimated property change.
            initial_properties = {k:v for k,v in self.initial_properties.items() if k!='property_normalizations'}
            comparable = None if properties is None else {k:v for k,v in properties.items() if k!='property_normalizations'}
            changed['measurement'] |= model_data_fingerprint(comparable)!=model_data_fingerprint(initial_properties)
        for target,was_changed in changed.items():
            if was_changed and not any(row['target']==target for row in located):
                raise ValueError('joint revision lacks evidence for '+target)
        return self._result(weights, constants, measurement, properties, changed, located, expression=expression)

    def _result(self, weights, constants, measurement, properties, changed, located, *, disposition=None, expression=None):
        result = {'version':self.version,'current_index':self.current_index,
            'persona_fingerprint':model_data_fingerprint(self.persona),
            'previous_weights_fingerprint':model_data_fingerprint(self.previous_weights),
            'initial_measurement_fingerprint':model_data_fingerprint(self.initial_measurement),
            'weights':weights,'base_constants':constants,'measurement':thaw_model_data(measurement),'properties':properties,
            'changed':changed,'revision_evidence':located,
            'authority':{'state_write':False,'history_write':False,'send':False}}
        if expression is not None:
            result['expression_observations'] = expression
            result['expression_changed'] = expression != self.expression_observations
        if disposition is not None:
            result['revision_disposition'] = disposition
        return dict(result,fingerprint=model_data_fingerprint(result))

    def _verify_event(self, event):
        from .events import PetEvent
        from .semantic import evidence_source
        if not isinstance(event, PetEvent):raise ValueError('joint estimate requires a typed transcript event')
        expected_role = 'assistant' if event.event_kind == 'npc_output' else 'user'
        current = self.sources[self.current_index]
        if current['role'] != expected_role or current['text'] != evidence_source(event):
            raise ValueError('joint estimate current source differs from event text or perspective')
