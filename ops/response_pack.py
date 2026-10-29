# ops/response_pack.py
"""响应装配卡包（承 M.A.N.E. G6 on-demand 装配三件套，零 LLM）。

三条硬规则（照抄 MANE，破一条退回熵增）：
1. 过滤器永不生成文本——块全部人类预写，机器只做选/滤；
2. 核心区永不过筛——回复纪律/人格卡/写作直觉固定装配，本包只有"输入应对"块；
3. 目录变更走标定不走运行——块文本 golden 锁（tests/test_response_pack.py）。

触发信号全部复用既有确定性检测器（extract_features / dict_tier_hint / scan_ammo），
长度与问号数为文本统计——不新增任何测量。
每轮最多挂 max_blocks 块（MANE 教训：softmax 全局归一化，块越多越稀释），按优先级截断。
"""
import re

from core.params import RESPONSE_PACK
from ops.deny import detect_denial

MAX_BLOCKS = 3

# 块目录（人类预写，[先验待验]；priority 大 = 先挂）
BLOCK_CATALOG = {
    "trauma_catch": {
        "priority": 10,
        "text": "【输入应对·创伤】对方说了很重的事：绝不说教、不贴标签、不追问细节、"
                "不给廉价安慰。先承认这件事的重量，再按角色立场给一句具体回应；"
                "不给陪伴承诺、不替对方安置情绪，给完就停。",
    },
    "ammo_struck": {
        "priority": 9,
        "text": "【输入应对·被戳中】对方的话碰到了你最不想被知道的地方：你会动摇、慌乱、"
                "试图遮掩，但不会当场承认。反应要明显不自然——突然的沉默、岔开话题、"
                "语气发紧都行，最后用你的口癖找补一句。",
    },
    "attack_deflect": {
        "priority": 8,
        "text": "【输入应对·冲突】对方话里带刺：不道歉、不讨好、也不升级。按你的人格接住"
                "这一下——可以回刺、可以装没听见、可以冷处理，但不要顺着对方的火气走。",
    },
    "emotion_flood": {
        "priority": 7,
        "text": "【输入应对·情绪过载】对方情绪很满：把它当作当前语境，不是待解决任务。按角色立场回应眼前内容或给出自己的反应；"
                "若承认情绪，一句就够。不要分析对方、不要说教、安抚、劝休息或代为安排，也不要用关怀填满回复。",
    },
    "multi_topic": {
        "priority": 6,
        "text": "【输入应对·多话题】对方一次说了几件事：拆成两三点分别接住，每点一两句；"
                "最后收一句把话题拢回来。不要漏掉对方最在意的那件。",
    },
    "complex_long": {
        "priority": 5,
        "text": "【输入应对·长输入】对方说了很长一段：先用你自己的话把对方真正在说的意思"
                "点出来（不逐字复读），再回应最重的那一点；其余留到之后。",
    },
    "question_burst": {
        "priority": 4,
        "text": "【输入应对·连环问】对方连着问了好几个问题：只答最要紧的一个，其余可以"
                "明说「这个我记着」；不要一次全答成流水账。",
    },
    "avoid_withdraw": {
        "priority": 3,
        "text": "【输入应对·话题暂停】场景已转入停顿或新话题。当前输入是这句对白的落点；"
                "写角色此刻的判断、动作或留白，允许一句结束。旧话题仍被当前输入点到时，"
                "再自然承接。",
    },
}


def _split_sentences(text):
    return [s.strip() for s in re.split(r"[。！？!?\n]", text or "") if s.strip()]


def evaluate_triggers(text, features=None, tier_hint=None, ammo_hits=None):
    """→ [(block_id, 原因)]（触发评估器，零新增测量）。"""
    t = text or ""
    f = features or {}
    out = []
    if tier_hint in ("创伤", "私密"):
        out.append(("trauma_catch", f"三档分级 {tier_hint}"))
    if ammo_hits:
        out.append(("ammo_struck", "弹药关键词命中"))
    if f.get("attack", 0.0) > 0:
        out.append(("attack_deflect", "攻击词密度 > 0"))
    if f.get("emotion", 0.0) > RESPONSE_PACK["emotion_density_line"]:
        out.append(("emotion_flood", f"情绪词密度 {f.get('emotion', 0):.3f}"))
    if len(_split_sentences(t)) >= 3:
        out.append(("multi_topic", "分段 ≥ 3 句"))
    if len(t) >= RESPONSE_PACK["long_input_chars"]:
        out.append(("complex_long", f"长度 {len(t)}"))
    if t.count("？") + t.count("?") >= 2:
        out.append(("question_burst", "问号 ≥ 2"))
    # ``deny`` 已是输入侧的两级边界检测器。这里仅复用其“当前轮明确暂停”
    # 这个事实来选择一张渲染边界卡；不读取/改写挂账，不影响召回，也不把短语
    # 映射成一句预制对白。这样“先不聊 X，改说 Y”不会漏过仅因词典密度为零的
    # 正常换题。
    denial = detect_denial(t)
    # ``avoid`` 是连续词元特征，可表达态度强度，却无法区分“随便吃点”与“随便，
    # 不聊了”。话题暂停卡会改变作者对当前输入的解释，因此只接受已有边界检测器
    # 给出的明确短语证据；不把模糊词面升级为一次渲染指令。
    if denial is not None:
        reason = "既有边界检测：%s" % denial["phrase"]
        out.append(("avoid_withdraw", reason))
    return out


def assemble_response_blocks(text, features=None, tier_hint=None, ammo_hits=None,
                             max_blocks=None):
    """触发评估 + 优先级截断 → (块文本列表, 块 id 列表)。"""
    cap = max_blocks if max_blocks is not None else MAX_BLOCKS
    trig = evaluate_triggers(text, features, tier_hint, ammo_hits)
    trig.sort(key=lambda kv: -BLOCK_CATALOG[kv[0]]["priority"])
    ids = [block_id for block_id, _ in trig[:cap]]
    return [BLOCK_CATALOG[i]["text"] for i in ids], ids


def registry_report():
    """清单审计（文档/测试用）。"""
    return {"blocks": sorted(BLOCK_CATALOG.keys()),
            "max_blocks": MAX_BLOCKS}
