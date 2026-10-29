"""比赛演示场景：可选显式背景种子 + 正式互动链路。

这里不是产品记忆算法，也不提供任何预写 NPC 台词。它只把一段可见的、明确标为
演示背景的共同经历写入既有 Memory / Ledger 入口；空种子场景只建立角色，不种共同事件。之后的用户输入、时间推进和
输出仍由 Actor -> Runner 处理。普通会话绝不会自动调用本模块。
"""
from __future__ import annotations

import copy
import time

from ops.fixture_provenance import DEMO_BACKGROUND_SOURCE
from ops.time_replay import apply_memory_seeds, validate_memory_seeds
from ops.turn_signals import derive_turn_signals
from interaction_extensions.preference_calibration.cx_six_seven.fitting import coarse_fit
from temporal.bus import new_state, on_message


SCHEMA_VERSION = 1
# 保持旧来源名兼容已创建的演示档案；它是记忆背景，不是可见聊天前情。
DEMO_PRELUDE_SOURCE = DEMO_BACKGROUND_SOURCE
SCENE_ID = "competition_memory_conflict_v1"
SCENE_TITLE = "一杯没喝成的咖啡"
SCENE_BACKGROUND = (
    "演示背景（不是历史聊天记录）：三天前，你们在雨里约好，下次路过老街咖啡店，"
    "就去喝那杯。"
)
SCENE_GUIDE = (
    "可先从“刚路过老街那家咖啡店”开始，再切到完全无关的话题；"
    "若要展示沉默后的主动追问，请使用“你根本不懂，别装了”这类冲突，"
    "不要用明确的拒绝联系。"
)
HIGH_TENSION_SCENE_ID = "competition_tension_boundary_v1"
LIVE_REHEARSAL_SCENE_ID = "competition_live_screening_v1"
HIGH_TENSION_TITLE = "别拿沉默来审判我"
HIGH_TENSION_BACKGROUND = (
    "演示背景（不是历史聊天记录）：两天前，你们因一次突然失联起了争执；"
    "对方曾说过，过去有人长期用沉默逼她妥协。争执后你们仍约好，等雨停去喝那杯咖啡。"
    "本场景另明确设定为：彼此在意，但目前带着未解的别扭。"
)
HIGH_TENSION_GUIDE = (
    "录制顺序：先输入“你又冷处理。每次一有事就躲，装得很无辜。”；"
    "等首条回复完整提交，若 F14 过线就让即时续话自然出现；再输入“行，不说了。"
    "你不是最会把人晾着吗。”，随后停止输入，观察 F15 的实际 tick、记忆选择与主动开口。"
    "这些句子只提供可复现的冲突方向，不预写回复、追发次数或主动时刻。"
)

# 演示可明确声明双方不是陌生人的关系起点。它不是伪造聊天、不是记忆 seed，也不由
# 人设文案暗写：创建时作为 X0 经正常 state schema 落盘，之后仍由 F03/F08 正式更新。
HIGH_TENSION_INITIAL_STATE = {
    # 这是背景已明确的“彼此在意、正在别扭”的 X0，不是把冲突句或台词硬写进
    # 状态。首个正式输入仍会走 F03；这组量只让演示从非陌生人的合理关系起点开始。
    "E_env": 0.58,
    "P_need": 0.72,
    "V_attr": 0.72,
    "V_rep": 0.64,
    "L_self": 0.0,
    "L_opponent": 0.0,
}

# 这是版本化关系背景，不是产品侧关键词规则。每一条带 demo_prelude 来源戳，
# 只进入既有记忆／台账入口；展示和短尾上下文会过滤它，不能伪装为聊天记录。
MEMORY_SEEDS = validate_memory_seeds([
    {
        "id": "coffee_commitment",
        "offset_ms": -3 * 24 * 60 * 60 * 1000,
        "salience": 0.92,
        "messages": [{
            "role": "npc",
            "text": "下次路过老街那家咖啡店，就去喝那杯。别又临时改口。",
        }],
        "archive_tokens": [{"text": "咖啡店", "category": "other", "strength": 1.0}],
        "ledger_facts": ["我们约好下次路过老街咖啡店时去喝那杯"],
        "topic": "咖啡店",
    },
    {
        "id": "rainy_evening_detail",
        "offset_ms": -2 * 24 * 60 * 60 * 1000,
        "salience": 0.28,
        "messages": [{
            "role": "npc",
            "text": "那天雨太大，最后谁也没走到店门口。",
        }],
        "archive_tokens": [{"text": "下雨", "category": "other", "strength": 0.55}],
        "ledger_facts": ["那次去咖啡店前下了很大的雨，最后没有去成"],
        "topic": "下雨",
    },
])

HIGH_TENSION_MEMORY_SEEDS = validate_memory_seeds([
    {
        "id": "coffee_after_rain_commitment",
        "offset_ms": -2 * 24 * 60 * 60 * 1000,
        "salience": 0.82,
        "messages": [
            {"role": "user", "text": "雨停了就去吧。那杯咖啡还欠着。"},
            {"role": "npc", "text": "好。别把话又晾在那里。"},
        ],
        "archive_tokens": [{"text": "咖啡", "category": "other", "strength": 0.82}],
        "ledger_facts": ["我们约好等雨停后去喝那杯咖啡"],
        "topic": "咖啡",
    },
    {
        "id": "silence_wound",
        "offset_ms": -36 * 60 * 60 * 1000,
        "salience": 0.96,
        "messages": [
            {"role": "user", "text": "我不是在冷处理，只是想先静一静。"},
            {"role": "npc", "text": "别拿我不说话那阵来压我。以前有人就是这么逼我开口的。"},
        ],
        "archive_tokens": [{"text": "沉默", "category": "pain", "strength": 1.0}],
        "ledger_facts": ["我曾被人用长期沉默逼迫妥协，因此很抗拒被拿沉默审判"],
        "topic": "沉默",
    },
])

SCENES = {
    SCENE_ID: {
        "id": SCENE_ID, "title": SCENE_TITLE, "background": SCENE_BACKGROUND,
        "guide": SCENE_GUIDE, "seeds": MEMORY_SEEDS, "default_preset": "傲娇.json",
    },
    HIGH_TENSION_SCENE_ID: {
        "id": HIGH_TENSION_SCENE_ID, "title": HIGH_TENSION_TITLE,
        "background": HIGH_TENSION_BACKGROUND, "guide": HIGH_TENSION_GUIDE,
        "seeds": HIGH_TENSION_MEMORY_SEEDS, "default_preset": "高张力演示.json",
        "initial_state": HIGH_TENSION_INITIAL_STATE,
    },
    LIVE_REHEARSAL_SCENE_ID: {
        "id": LIVE_REHEARSAL_SCENE_ID, "title": "散场前的老搭档（真实聊出记忆）",
        "background": "角色设定：桑宁，24岁，老影院活动策划；你们是有默契也会争执的多年搭档。她正在筹备一场旧片放映。共同约定、具体时间和分歧由本次聊天形成，未预填聊天或事件记忆。",
        "guide": "先聊筹备，让一个具体约定自然形成，再谈你认可她什么、又对什么有分歧；之后真实等待。监测显示实际 tick 与开口条件，录完从本地记录整理摘要。",
        "seeds": [], "default_preset": "桑宁_散场前.json",
    },
}


def scene_catalog():
    """仅供 UI 选择器读取的不可变展示摘要。"""
    return tuple({k: row[k] for k in ("id", "title", "background", "guide", "default_preset")}
                 for row in SCENES.values())


def get_scene(scene_id):
    try:
        return SCENES[str(scene_id)]
    except KeyError as exc:
        raise ValueError("未知比赛演示场景") from exc


def _seed_demo_timeline(runner, scene, now):
    """用已有用户输入测量 + 时间总线建立演示开场的最后状态锚。

    这不是手写变量快照：场景最后一句用户前情经过同一个 signals / coarse-fit /
    temporal.on_message 路径，得到一个可由启动 catch_up 正式推进的状态。它只用于
    明确标识的 demo 会话，且完整保留在事件台账。
    """
    spec = scene.get("timeline_seed")
    if not isinstance(spec, dict):
        return None
    ts = now + int(spec["offset_ms"])
    text = str(spec["text"])
    state = new_state()
    profile = runner.profile or {}
    signals = derive_turn_signals(
        text=text, ammo=profile.get("ammo") or [], now_state_vars=state["vars"],
        pending_extraction=None, semantic_projection=None)
    behavior = coarse_fit([{"ts": ts, "text": text, "delay_ms": None}],
                           baseline=runner.baseline)
    event = {
        "var_deltas": signals.variable_deltas,
        "lever_deltas": {"self": signals.lever_self, "opponent": signals.lever_opponent},
        "weights": behavior,
        "stable_weights": runner.prior,
        "weights_prev": None,
        "semantic_tokens": [],
    }
    time_binding = runner._effective_time_binding()
    state, report = on_message(
        state, profile.get("constants") or {}, event, ts,
        effective_constants=time_binding["effective_constants"],
        effective_constants_version=time_binding["effective_constants_version"])
    runner.state = state
    runner.weights_prev = behavior
    runner.store.save_state(state)
    runner.store.append_event({
        "kind": "competition_demo_timeline_seeded", "ts": ts,
        "detail": {"scene_id": scene["id"], "source": DEMO_PRELUDE_SOURCE,
                   "input_seed_id": "scenario_timeline_seed",
                   "field_token_count": len(report.get("field_tokens") or [])},
    })
    return {"ts": ts, "field_token_count": len(report.get("field_tokens") or [])}


def is_demo_profile(profile):
    scenario = (profile or {}).get("demo_scenario")
    return isinstance(scenario, dict) and scenario.get("id") in SCENES


def prepare_competition_scene(guild, char_id, card, *, now_ms=None,
                              preference_text=None, preference_declared=None,
                              scene_id=SCENE_ID):
    """创建一份可交互、可追溯的演示会话。

    ``guild.new_character`` 仍是角色开局唯一入口；背景种子只经已存在的
    ``apply_memory_seeds`` 写入。函数不调用 responder，也不制造当前时刻的 NPC 输出。
    """
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    scene = get_scene(scene_id)
    runner = guild.new_character(char_id, card, preference_text=preference_text,
                                 preference_declared=preference_declared,
                                 initial_state=scene.get("initial_state"))
    seeded = apply_memory_seeds(runner, copy.deepcopy(scene["seeds"]), now,
                                source=DEMO_PRELUDE_SOURCE)
    timeline = _seed_demo_timeline(runner, scene, now)
    profile = runner.store.load_profile() or {}
    profile["demo_scenario"] = {
        "id": scene["id"],
        "schema_version": SCHEMA_VERSION,
        "title": scene["title"],
        "background": scene["background"],
        "guide": scene["guide"],
        "prepared_at": now,
        "clock_offset_ms": 0,
        "seed_source": DEMO_PRELUDE_SOURCE,
        "initial_state": copy.deepcopy(scene.get("initial_state")),
    }
    runner.store.save_profile(profile)
    runner.profile = profile
    runner.store.append_event({
        "kind": "competition_demo_prepared",
        "ts": now,
        "detail": {"scene_id": scene["id"], "seed_count": len(seeded),
                   "seed_source": DEMO_PRELUDE_SOURCE, "timeline_seeded": bool(timeline),
                   "initial_state_explicit": bool(scene.get("initial_state"))},
    })
    return {
        "runner": runner,
        "scene_id": scene["id"],
        "title": scene["title"],
        "background": scene["background"],
        "guide": scene["guide"],
        "seeded": seeded,
        "timeline": timeline,
    }
