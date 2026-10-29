# temporal/__init__.py
"""时间总线包（T.I.D.E. 系统核心）：连续动力学 + 事件采样，全部纯函数、闭式解。

gate   : τ₀ 启动门（连续函数）
lexicon: 结束词元 → 漂移场映射（冻结区规则表）
fields : 四场 + 原语闭式积分 + 场编译/求值
bus    : 采样协议（advance / on_message，可回放）
schedule: 下一数学边界规划（纯函数；运行时计时器属于 ops/）
output_time: T0.1-A 的 received/ready/sent 时间 oracle（尚不提交状态）
"""
