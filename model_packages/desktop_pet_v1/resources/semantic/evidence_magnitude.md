# 当前事件语义量测

把本次已发生事件及提供的可见上下文量测为即时互动增量、互动方向和有原文证据的词元。下游保存累计状态、处理时间变化、选择行为及装配表达。

## 增量

variable_deltas 是对象，六轴取值范围 [-1,1]，表达本次新增作用：
- E_env：当前互动压力，增加为正，缓解为负。
- P_need：当下未满足的关系需求，增加为正，得到缓解为负。
- V_attr：靠近、认可和正向牵引，增强为正，减弱为负。
- V_rep：责备、归因、面子压力、防御或未解摩擦，增强为正，缓和为负。
- L_self：当前角色已经对外表达的立场、脆弱或信息。
- L_opponent：对端已经表达且当前角色可获知的立场、脆弱或信息。

变化方向和幅度由本次证据支持的实际作用决定，轻微作用和强烈作用使用同一取值范围。0表示本次该轴没有可识别的变化。confidence 独立记录量测把握程度，增量记录估计作用幅度。

peer_input 的受影响者是当前接收角色，L_self 为0；对端明确交出的信息可进入 L_opponent。npc_output 的受影响者是当前表达角色，L_opponent 为0；实际披露可进入 L_self。其余轴按此次事件对相应角色的即时作用量测。

交互刺激以事件事实和包内操作定义为依据；已有状态是下游背景，已发生片段是本轮量测范围。文字、动作、私有经历分别沿各自的事实与可见范围理解。

## 互动方向

behavior_weights 是五项非负权重，和为1，描述本次可见互动姿态：
- Counter_Phobic：对抗、顶撞、施压或以进攻试探边界。
- Mirroring：在意被认可、看见、评价，围绕反馈或肯定互动。
- Avoidance：回避、抽离、减少联系或结束话题。
- Trauma_Recon：高张力情绪、拉扯试探或关系强度诉求。
- Pragmatism：平静的信息交换和实际问题讨论。

各项依据当前事件的证据分配，作为此次互动方向。

## 输出合同

输出一个JSON对象：
- variable_deltas：上述六轴的数值对象。
- behavior_weights：上述五项权重对象。
- tokens：最多3个对象，每项含 text（当前互动含义）、strength（[0,1]）和 evidence（支撑它的本次原文片段）。
- confidence：[0,1]，表示本次量测的证据把握程度。
- reference_frame：有明确关系叙述时提供对象，否则省略。包含 delivery_scope、relation_claims 和 confidence。

reference_frame.delivery_scope 取 eligible（当前角色与用户）、third_party（用户与第三方）或 unresolved（对象未确定）。关系明确时 relation_claims 至少一条，每条含 subject、target、relation、evidence_text；subject/target 使用 user、current_npc、third_party、unresolved 归属标签。evidence_text 逐字引用本事件中可唯一定位的连续片段：话语取正文，交互取交互摘要，多内容依原顺序以换行连接。本地负责字符位置和来源关联。
