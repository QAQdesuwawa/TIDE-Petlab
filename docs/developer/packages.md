# 开发或修改模型包

[返回开发者入口](README.md)

## 从最小可运行包改起

先运行 [独立 Runtime 示例](../../examples/runtime_minimal.py)，再看 [MinimalAffinityV1Package](../../model_packages/minimal_affinity_v1/__init__.py)。它包含 manifest、参数/状态/量测 schema 和十个已声明阶段，使用自己的三个状态量；完整六七模型只是另一个包实现。

最省事的开始方式是复制这个小包到自己的包目录，换成自己的 `package_id`、版本和定义，在自己的宿主中实例化，再传给 `compose_runtime_packages()`。`model_packages` 是仓库组织习惯，不是框架限定的安装目录；Python调用者能导入并传入包实例即可。当前没有基于包名字符串的动态安装/热加载入口。

## 要明确的定义

| 定义 | 在小包中看什么 | 修改目的 |
| --- | --- | --- |
| 包身份与合同 | `InteractionModelManifest.create()` | 标识版本、schema、能力与操作实现版本 |
| 参数与状态 | `initialize_parameters/initialize_state` | 初始化你自己的模型数据 |
| 量测 | `measurement_schema`、`measure_event` | 接受领域适配器产生的量，而不是由Kernel猜文本含义 |
| 事件与进度贡献 | `derive_event_contribution/advance_progress` | 定义事件影响及时间演化 |
| 结算 | `settle_state` | 给出候选结算，由原事务/Kernel接受 |
| 共享投影与输出资源 | `project_shared_semantics/project_semantic_resources` | 让调度和表达消费同一正式来源 |
| 观察与行动 | `plan_observation/assess_action` | 给出观察建议和行动资格 |

上表按当前小包组织；精确必选/可选能力以 [model_api](../../framework/model_api/__init__.py) 的 `BASE_MODEL_CAPABILITIES_V2` / `OPTIONAL_MODEL_CAPABILITIES_V2` 和宿主的 `required_model_capabilities` 为准。先实现产品真的需要的能力，不必照抄六七包的所有模块。内存/记忆功能也不会因包注册成功自动出现。

包计算领域数据；Runtime验证绑定、阶段和正式提交。把某个业务的条件写进自己的包很正常，通用Kernel不负责认识鲸鱼娘、鼠标或某条台词。

## 接已有算法，不重写算法

可以让你的语义适配器调用已有分类器，把结果整理为 `measurement_schema`；在包的贡献/投影阶段调用已有确定性算法。给算法传它声明需要的输入，返回包定义的值。包的阶段输入输出和版本可验证，不要求你把已有模块改成一套新的服务。

如果只想改变桌宠的动作候选、词元绑定、素材或输入子类，通常直接扩展 [desktop_pet_v1](../../model_packages/desktop_pet_v1/__init__.py) 及其适配器更短；不必为每个动画重新开发完整模型包。

## 参数、会话和升级

包ID、版本、参数指纹、时间域与会话有明确绑定。新包先用新会话验证。改字段含义、操作定义或旧回放规则时给新版本，并明确旧档怎样恢复；单纯加入一个素材不需要另造心理轴。

序列化/恢复使用正式 profile 和绑定，见 [Runtime示例](runtime.md)。在自己的存储里保存原事实、原始返回和实际执行的来源关联，避免每轮复制整个历史。已验证的相同来源可复用，新的来源仍需走验证。

## 一个小的验证闭环

1. 用明确量测夹具跑初始化、事件结算、时间推进和恢复，确认数值来源与预期一致。
2. 用无资格、缺能力、错误版本和来源变化检查边界。
3. 接真实语义/执行模块后，检查实际返回、资格、执行回执与最终记录，不只看是否抛异常。

本仓库提供 [公开离线用例](../../examples/test_examples.py) 作为起点；它不代替你自定义数学和真实服务的验证。更多非六七实现可参考 [neutral_signal_v1](../../model_packages/neutral_signal_v1/__init__.py) 与 [logical_relay_v1](../../model_packages/logical_relay_v1/__init__.py)。
