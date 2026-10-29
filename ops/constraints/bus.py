# ops/constraints/bus.py
"""检查卡总线（T1 v1 · 承 MANE ops/constraints/bus.py 纪律）：
跑全部启用卡；observe 默认（on_fail 一律 log，先实测触发率）；
enforce 模式下 repair/inject 仍默认关（v1 只做 log——先实测再谈注入）。"""
from ops.constraints.checks import CARD_DEFS, CARD_IDS


def run_checks(ctx, enforce=False, enabled=None, params=None):
    """跑全部（或指定）卡 → {"results": [...], "violations": [...]}。

    每张卡 result = {"card_id", "pass", "verdict", "action"}：
    - pass → action "none"；
    - fail → action = 卡自身 on_fail（v1 恒 "log"，enforce 也不注入——
      repair/inject 默认关，承 MANE G1 纪律）。
    """
    results = []
    for card_id in (enabled if enabled is not None else CARD_IDS):
        card = CARD_DEFS[card_id]
        try:
            verdict = card["check_fn"](ctx, params)
            if not isinstance(verdict, dict):
                raise TypeError("check verdict 必须是 dict")
        except Exception as exc:
            # 检查卡是 observe 护栏而非回复主路径；卡自身的形状/实现错误要以一条
            # 可观测 violation 留痕，但绝不能让已生成的 NPC 回复丢失。
            verdict = {"pass": False,
                       "detail": {"reason": "check_error",
                                  "error_type": type(exc).__name__}}
        passed = bool(verdict.get("pass", True))
        action = "none" if passed else card["on_fail"]["action"]
        results.append({"card_id": card_id, "pass": passed,
                        "verdict": verdict, "action": action})
    violations = [r for r in results if not r["pass"]]
    return {"results": results, "violations": violations}
