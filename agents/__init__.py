# agents/__init__.py
"""LLM 边界层：系统与 LLM 的唯一接口。

llm_client 照抄 M.A.N.E.；各 agent 模块只做 prompt 组装与输出解析（纯函数），
LLM 调用由 P6 runner 接线——测试可全程零真 LLM。
"""
