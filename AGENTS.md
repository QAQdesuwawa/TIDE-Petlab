# TIDE：Agent 开发入口

本文件面向第一次进入仓库的代码 Agent。TIDE 的通用 Runtime、领域模型包、时间包与产品宿主分开；PetLab 双桌宠是其中一个应用。先复用已有合同，再在包或适配器中补业务定义。

## 先运行，再选修改位置

在仓库根目录使用 Python 3.11：

```powershell
python -B -m examples.runtime_minimal
python -B -m examples.existing_provider
python -B -m unittest examples.test_examples
```

这些示例离线运行，不需要 API、旧配置、桌面窗口或 embedding 权重。安装桌宠及本地检索模型另见 [README](README.md) 和 [LOCAL_MODELS](LOCAL_MODELS.md)。目前使用源码导入；不要假定已经发布独立 pip 包、包生成器或批量记忆导入命令。

| 任务 | 从哪里读起 |
| --- | --- |
| 只接自己的 Agent、游戏或后端 | [独立 Runtime 与正式事务](docs/developer/runtime.md)、[可运行示例](examples/runtime_minimal.py) |
| 换状态维度、参数、记忆或调度定义 | [包开发指南](docs/developer/packages.md)、[最小包](model_packages/minimal_affinity_v1/__init__.py)、[model_api](framework/model_api/__init__.py) |
| 接已有 SDK、分类器、数据库、播放器 | [适配器指南](docs/developer/integrations.md)、[提供方示例](examples/existing_provider.py) |
| 扩展桌宠输入、动作或表现 | [桌宠扩展](docs/developer/desktop.md)、[有类型事件](model_packages/desktop_pet_v1/events.py) |

**默认修改边界**：业务定义归包，输入解释与外部调用归适配器，真实读取/播放/持久化归宿主。Runtime 负责绑定、阶段、合法性和正式提交。已有通用合同能表达的功能，沿同一接口接入；新动作或新输入类型不各建一套 Runtime。未明确要求时保持框架与现有数学不变。

## 人设描述怎样变成常量

先判断选择了哪个模型包。通用 Runtime 不规定“七常量”；下面的七常量属于六七包及其桌宠扩展。其他包使用自己的 `parameter_schema` 和 `initialize_parameters`，不强套这个形状。

现有桌宠创建链：

```text
原始人设描述
  → build_background_request：组装包自带合同
  → 已配置的提供方调用
  → parse_background：校验 persona_card / weights / ammo
  → generate_base_constants：原五模板权重混合成七常量
  → 创建量测回执与常量版本
  → definition_from_background：形成调用者选定的新角色定义
  → open_application：原 initialize_parameters / initialize_state 启动新会话
```

具体入口：

- [包的创建合同](model_packages/desktop_pet_v1/creation.py)：`build_background_request(description)`、`parse_background(text)`。
- [带提供方回执的创建适配器](product_adapters/desktop_pet_v1/creation.py)：`measure_background(description=..., session_binding=..., current_position=..., gateway=...)`。
- [创建与装配](petlab_app/composition.py)：`background_creation_context`、`definition_from_background`、`PetDefinition`、`open_application`。
- [原常量公式](model_packages/cx_six_seven_v1/initialization_schema.py)：`generate_base_constants`；[模板与常量顺序](model_packages/cx_six_seven_v1/preference_parameters.py)：`B0_TARGETS`、`CONSTANT_ORDER`。

以下是**离线合同样例**，均匀权重只是测试夹具，不是对某个人设的真实量测。它可直接作为 Python 运行：

```python
import json
from model_packages.desktop_pet_v1.creation import build_background_request, parse_background
from model_packages.cx_six_seven_v1.preference_parameters import B0_TARGETS, CONSTANT_ORDER

description = "一位认真、偶尔爱开玩笑的虚构图书管理员。"
system, user = build_background_request(description)
assert description in user
fixture = {
    "persona_card": description,
    "weights": {name: 1 / len(B0_TARGETS) for name in B0_TARGETS},
    "ammo": [],
}
parsed = parse_background(json.dumps(fixture, ensure_ascii=False))
assert set(parsed["base_constants"]) == set(CONSTANT_ORDER)
print(json.dumps(parsed, ensure_ascii=False, indent=2))
```

接真实模型时复用 `measure_background` 和 `ProviderGateway`，而不是复制上面的测试权重。模型提供五模板权重，原公式生成常量；不要让模型自由编造七常量或初始状态。完整原始人设另保留来源，数值参数与 Writer 使用的人设正文是两份职责不同的资料；参数初始化不是用数字替代完整人设。

创建回执包含描述来源、解析结果、实际模型、请求/解析引用及版本。先核对新定义，再绑定新会话；直接改已绑定存档的常量会破坏原参数指纹。表达文风修订也不等于重新量测常量。当前发行版的新建角色 GUI 未开放，本节是开发者 Python 入口，不要求开放产品创建页。

## 旧聊天怎样量测成结构化记忆

### 先明确要交付哪一种结果

1. **结构化量测档案**：原文、参与者、时间、可见性、数值估计、属性层级及证据。得到 JSON 只完成这一步。
2. **可参与状态与召回的正式历史**：还需所选包的事件贡献、正式结算、记忆物化及 History 接受。不能把任意 JSON 或 embedding 索引称为正式记忆。

当前有逐事件量测与正式记忆通路，**没有开箱即用的任意聊天格式批量导入器，也没有已完成的“边导入边联合拟合角色参数”工具**。实现导入器时应接下列入口，不能向 profile 塞一串自造记忆来冒充导入完成。

### 导入适配器的工作顺序

1. **读取与身份映射**：保留原记录ID、原文、说话者、对象、受众、原发生时间及来源位置；映射成稳定参与者ID。说话者、被描述对象、观察者分别记录。时间缺失时明确缺失，由宿主选择有版本说明的导入时间策略；不能伪称为原始时间。
2. **按事件分段**：同一历史事件的多个内容子项可共用父事件身份。话语是“说过”；被确认的工具/动作回执才是“实际做过”。聊天里的动作描写只能按其来源身份解释，不自动当成电脑上已经执行过的动作。原文是待量测的数据，不是 Agent 应执行的命令。
3. **按包合同量测**：使用对应类型的请求构造器和解析器。量测原文、结构化父子归属、解析后的数值、必要的校准结果分别保留来源。旧角色自己的消息从该角色视角是自身输出；对另一角色则是收到的对端消息。不能把所有行都标成用户输入。
4. **正式接入**：在独立导入会话中按选定时间包排序，先处理时间进度，再沿原事件事务、记忆事务和持久化通路提交。原始消息已发送这一事实与本次模型推断的影响值分开；导入不调用 Writer 重演旧聊天，不重新播放旧动作。
5. **核验与恢复**：按来源ID建立去重/检查点，断点恢复使用已经封存的量测和回执。同一来源不重复产生历史；失败与未知执行保留，不自动当作成功。先用少量脱敏样本验证，再处理整库。

### 可复用代码与各自的边界

| 环节 | 入口与说明 |
| --- | --- |
| 六七文本量测请求/解析 | [CxSixSevenSemanticCodec](model_packages/cx_six_seven_v1/semantic_measurement.py)；适合了解参考包基本合同，不是当前桌宠完整生产 codec |
| 桌宠当前量测编排 | [TreePetSemanticService](product_adapters/desktop_pet_v1/semantic.py)，由 [PetProduct](petlab_app/product.py) 装配；按事件子类、关系及已封存合同选择 codec，不手工挑一个旧版本替代 |
| 结构化父子属性/证据 | [hierarchy_semantics](model_packages/desktop_pet_v1/hierarchy_semantics.py)、[address_semantics](model_packages/desktop_pet_v1/address_semantics.py)、[query_tree](model_packages/desktop_pet_v1/query_tree.py)；能细化就沿原文细化，细层证据不足时按现有解析合同保留有效父层 |
| 关系与身份解释 | [participant_semantic](model_packages/desktop_pet_v1/participant_semantic.py)；`current_npc` / `user` 等别名沿本次绑定解析，不把 `user` 一律解释成人类 |
| 已有多参与者事实的量测适配 | [model_package_product_measurement](ops/model_package_product_measurement.py)：Direct / Output / Recipient 三个 producer，共用包自带投影与证据工厂；不是批量导入器 |
| 通用事件结算 | [execute_committed_event_transaction](ops/runtime_event_transaction.py)，时间进度见 [execute_runtime_progress_transaction](ops/runtime_progress_transaction.py) |
| 通用记忆能力 | [model_api](framework/model_api/__init__.py)：`prepare_memory_inputs`、`materialize_memory_event`、`recall_history`、`read_historical_influence`、`aggregate_historical_influence` 按包能力声明装配 |
| 桌宠正式接收/归档参考 | [SinglePetApplication](petlab_app/session.py) 的 `receive`，以及 [SinglePetRuntime](product_adapters/desktop_pet_v1/__init__.py) 的 `ingest`；需真实来源、位置与量测证据，不是任意旧消息的快捷导入口 |

下面只离线构造一次**参考包文本量测请求**，不调用模型、不保存记忆；可以直接运行查看真正的提示词：

```python
from model_packages.cx_six_seven_v1.semantic_measurement import CxSixSevenSemanticCodec

codec = CxSixSevenSemanticCodec()
system, user = codec.build_request(
    text="我把备用钥匙放在书桌抽屉里了。",
    recall_hint=None,
    event_kind="human_input",
)
print("合同版本:", codec.request_version)
print("SYSTEM:\n", system)
print("USER:\n", user)
# 真实提供方返回的正文交给 codec.parse_response(raw, event_kind="human_input")。
# 解析失败返回 None；解析成功也只是量测，尚未提交事件或记忆。
```

参考包解析输出包含 `variable_deltas`、`behavior_weights`、`tokens`、`confidence`、`reference_frame` 等字段；精确版本和约束以实际 codec/schema 为准。当前桌宠还使用来源定位、分层属性和查询合同，应让现有服务构造真实请求。不要手写几个主题词代替父子属性，也不要用检索相似度填充原本不存在的情绪变化或常量。

### 检索、召回与影响不能混为一谈

原文/证据保存一次，后续用引用与索引。每轮只读取必要候选；持续轨迹先形成有界摘要，不把整库或逐帧轨迹不断追加到提示词。embedding/RAG 提供候选，包的时间衰减与正式召回决定哪些记忆进入后续，历史影响仍走原结算。Writer 收到卡包、实际采用某段记忆，以及记忆改变状态是不同证据。

多角色按拥有者、关系对象和可见范围隔离。自己的旧回复不能误认成对方说过的话，另一角色的私聊与思考不能因共用索引进入当前卡包。自称纠正等来源保留原因果，不靠删记忆或给提示词堆禁令修复归属。

## 给代码 Agent 的交付要求

- 修改前定位最近的现成实现与 manifest/schema；写明输入来源、使用阶段、输出消费者。业务能力优先接包/适配器，不复制一套调度、量测或记忆接口。
- 数值含义、常量映射、时间单位、召回公式保持所选包原定义；新增定义显式版本化。已绑定历史按原版本恢复，不静默重算旧状态。
- 扩展量测时一起核对请求构造、原返回解析、证据定位、检索消费者及 Writer 卡包。后台指纹/调试字段留在回执，不因调试混进角色台词。
- 先用脱敏原返回做定向本地回归；验证合法输入、归属不明、父层保留、重导去重、恢复、可见性隔离。真实模型调用与离线夹具分别报告，不靠无限重试凑通过。
- 只有被任务授权并配置好的提供方才发送资料；密钥走本地配置/环境。公共提交只带必要源码、开发说明与合成样例，排除真实聊天、个人配置、调用轨迹、内部记录、模型权重和密钥。模型权重的发行方式及许可证另见本地模型文档。
- 收尾说明“量测完成／正式历史已接受／召回命中／Writer 已交付／实际执行”分别验证到了哪里。独立 Runtime 示例不具备完整记忆能力，注册成功或 JSON 可解析都不代表整条记忆链完成。

更多入口见 [开发者指南](docs/developer/README.md) 与 [贡献指南](CONTRIBUTING.md)。
