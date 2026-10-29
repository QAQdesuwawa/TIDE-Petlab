近期用户话语按新到旧提供，逐条reference_id标识实际来源；它们是指代参照，正式召回仍由后续机制判定。
memory_cues保留本轮原文text和唯一evidence_text，并给cue_kind：explicit_topic是本轮直接给出的具体主题名称；reference_slot是本轮正询问、需从前文补出的对象、属性值或约定内容。词面中的问法与所问的答案值分别理解。
reference_cues每项为{"text":"来源话语中的原词","reference_id":"来源标识","evidence_text":"本轮指代槽的唯一原文片段","reference_text":"所选来源中包含原词的唯一原文片段"}。来源里的实际陈述用于补出所问值；来源问句用于识别问法，其尚未给出的答案仍待确定。主题转换与本轮询问的时间/约定关系一并沿原话理解。
reference_cues仅补reference_slot，explicit_topic仍沿本轮原词。参照不足时数组为空。两类cue合计最多16项，text最多48字，每段证据最多256字。当前事件是量测对象；参照本身的内容与数值影响分开，参照不冒充本轮新影响或已正式唤起的记忆。
