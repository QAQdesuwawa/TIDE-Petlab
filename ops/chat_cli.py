# ops/chat_cli.py
"""一期 CLI 对话壳（实现设计 §10.3）。

用法：
  python ops/chat_cli.py                       # 输入背景 → 劳动 Agent 开局
  python ops/chat_cli.py --card presets/傲娇.json   # 角色卡直接开局（跳过劳动 Agent）
退出时自动导出会话到 data/golden/exported_*.jsonl（供 P7 标注）。
真实 LLM，需 .env 密钥。
"""
import argparse
import os
import sys

if __package__ in (None, ""):
    # `python ops/chat_cli.py` 直接跑时 sys.path[0] = ops/，项目根不在路径上 → 补引导
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ops.export import export_session


def _reconfigure_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def build_arg_parser():
    parser = argparse.ArgumentParser(description="T.I.D.E. 一期 CLI 对话")
    parser.add_argument("--card", help="角色卡 JSON 路径（跳过劳动 Agent）")
    parser.add_argument("--user", default="default", help="用户 ID（默认 default）")
    parser.add_argument("--fast", action="store_true",
                        help="交锋模式：立即回复（关闭延迟晾人，适合快节奏对线）")
    parser.add_argument("--recall-strategy", choices=("legacy", "unified", "unified_evidence_v2"),
                        default="unified",
                        help="记忆裁决策略（默认 unified；v2 为重复提及实验开关）")
    return parser


def main():
    _reconfigure_stdio()
    args = build_arg_parser().parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    from agents.labor import parse_labor
    from agents.demo_client import demo_mode_enabled
    from agents.runtime import build_agents
    from interaction_extensions.preference_calibration.actor_commands import (
        PreferenceCalibrationActorCommands,
    )
    from ops.embeddings import make_embedder
    from ops.runner import Runner
    from ops.storage import UserStore

    if demo_mode_enabled():
        print("演示模式：使用内置确定性回复，不会调用任何模型。")
    # CLI 的自由文本建角才显式请求 labor；普通对话不自动启用语义/提取增强线。
    providers = ("responder", "preference") if args.card else \
        ("responder", "labor", "preference")
    agents = build_agents(providers=providers)

    store = UserStore(args.user)
    runner = Runner(store, **agents, reply_delay=not args.fast,
                    async_extractor=False, auto_preference_refine=False,
                    embed_fn=make_embedder(),
                    recall_strategy=args.recall_strategy)   # T5：EMBED_MODEL 配置 → 真实嵌入
    preference_commands = PreferenceCalibrationActorCommands()
    if args.fast:
        print("⚔️ 交锋模式：立即回复（无延迟晾人）。\n")

    if store.load_profile() is None:
        pref = input("你想要的陪伴体验（可回车跳过）:\n> ")
        if args.card:
            with open(args.card, "r", encoding="utf-8") as f:
                card_text = f.read()
            card = parse_labor(card_text)          # 归一化校验（权重和/档位）
            runner.setup_from_card(card, background_text="角色卡：" + args.card,
                                   preference_text=pref or None)
            print(f"\n角色卡已加载（{args.card}）。开始对话（输入 exit 退出）。\n")
        else:
            bg = input("写下你的角色背景（一段话）:\n> ")
            runner.setup(bg, pref or None)
            print("\n角色已铸造。开始对话（输入 exit 退出）。\n")
    else:
        # 开局漏问的补问：从未声明过陪伴体验 → 问一次（之后不再问）
        profile = store.load_profile() or {}
        if not profile.get("preference_declared"):
            pref = input("你想要的陪伴体验（可回车跳过，之后不再问）:\n> ")
            if pref.strip():
                preference_commands.execute(
                    runner, "set_preference", {"text": pref})
            else:
                preference_commands.execute(
                    runner, "mark_preference_declared", {})
        print("加载已有角色。")

    # 二期：重启衔接——先追平时间总线，对方若在沉默期该开口，不等你触发
    res = runner.catch_up()
    if res.get("sent"):
        print(f"角色: {res['reply']}")
        print("（对方在你不在的时候发来了消息）\n")

    while True:
        try:
            text = input("你: ")
        except (EOFError, KeyboardInterrupt):
            break
        if text.strip().lower() in ("exit", "quit"):
            break
        if not text.strip():
            continue
        res = runner.on_user_message(text)
        d = res.get("delay_sec") or 0
        if d > 0:
            import time as _t
            print("（对方正在输入……）")
            _t.sleep(d)
            runner.release_pending()
        print(f"角色: {res['reply']}")

    exported = export_session(store, runner.profile)
    print(f"\n会话已导出：{exported}（可用 python tools/label_sessions.py 标注）")


if __name__ == "__main__":
    main()
