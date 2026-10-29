# 独立使用 Runtime

[返回开发者入口](README.md)

## 可以直接运行的最小程序

```powershell
python -B -m examples.runtime_minimal
```

源码：[examples/runtime_minimal.py](../../examples/runtime_minimal.py)。它使用 `MinimalAffinityV1Package`，拥有自己的参数、三个状态量和两个量测字段。它不导入六七模型包，不依赖桌宠、API、人设或本地 embedding。

程序做了五件事：

1. 用 `compose_runtime_packages()` 注册并绑定模型包、时间包和运行变体。
2. 用 `bootstrap_runtime_session()` 明确给出初始坐标、包参数和初始状态。
3. 用 `execute_runtime_progress_transaction()` 把时间从示例坐标1000推进到1100。
4. 用 `execute_committed_event_transaction()` 消费已声明的事件量测，取得新的 session 和接受回执。
5. 将 session 的 profile 转为 JSON，再用 `restore_bootstrapped_runtime_session()` 恢复，检查状态指纹一致。

示例使用固定毫秒坐标，方便复现。正式墙钟宿主应提供真实发生时间和处理边界；时间包解释单位与进度，Runtime 不自己读取系统时钟。不能将动画帧率当作事件结算频率。

## 三个明确的装配输入

实际入口位于 [ops/runtime_package_composition.py](../../ops/runtime_package_composition.py)：

```python
composition = compose_runtime_packages(
    model_package=my_model_package,
    temporal_package=my_temporal_package,
    session_id="my-app:one-agent",
    variant_definition=my_variant,
    required_model_capabilities=("initialize_parameters", "initialize_state"),
)
```

`my_model_package` 等变量由调用者创建；完整可运行的创建代码在示例中。返回值提供 `model_runtime`、`temporal_runtime`、`variant_binding` 和装配回执。它不自动选择六七包，也不根据目录名导入任意模块。

| 层 | 提供什么 |
| --- | --- |
| 模型包 | manifest、参数/状态/量测 schema、能力与阶段实现、版本化数学和投影 |
| 时间包 | 坐标规范化、先后关系、进度、观察目标的单位和操作 |
| Runtime/Kernel | 绑定与阶段校验、正式状态接受、事务及来源的共同合同 |
| 你的宿主 | 用户/工具输入、真实时钟、持久化、外部执行与权限 |

改变包的状态维度，不要求先改 Kernel 的心理变量。反过来，包计算出的候选状态也要通过正式接受入口，不能由前端直接覆盖 session 中的状态。

## 接入已有存储与宿主

示例的 JSON 往返说明持久化可以由外部宿主负责：保存的是正式 session profile，恢复时仍使用原绑定的包和时间域。可选执行：

```powershell
python -B -m examples.runtime_minimal --output output/my-example/profile.json
```

这个命令只创建新文件，已有文件不会被覆盖。示例没有历史检索、动作发送或崩溃恢复日志；自己的数据库应在公布结果前完成必要耐久写，并保存事件/实际执行的关联。桌宠产品已经有 [SessionFile](../../petlab_app/storage.py)、增量存储和恢复实现，可参考复用。

如果你沿用现有 Conversation/Runner 产品功能，可看 [ops/runner.py](../../ops/runner.py) 与 [产品会话装配](../../ops/product_runtime_session.py)。该兼容产品层会引入既有产品适配；仅使用通用 Runtime 时，最小示例中的组合和事务入口更直接。

## 能力按需装配

`MinimalAffinityV1Package` 是替换能力的小例子，没有 `recall_history`。示例 profile 往返不等于完整记忆链。需要记忆时选择具备对应阶段的包和历史宿主，并在 `required_model_capabilities` 声明所需能力；缺能力时注册/绑定会报错。

完整阶段协议见 [framework/model_api](../../framework/model_api/__init__.py)，小包实现见 [minimal_affinity_v1](../../model_packages/minimal_affinity_v1/__init__.py)，另一个不同状态模型见 [neutral_signal_v1](../../model_packages/neutral_signal_v1/__init__.py)。
