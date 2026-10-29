# ops/deny.py
"""否认挂账卡（记忆与反应向量化提案 §五 · M3/M4）。

M3 声明：零 LLM 词典检测（两级：暂时/永久）→ [边界·否认] 词元挂账——零压制。
M4 裁决：消费 M1 反应词元族对未决挂账做 R 方向裁决（裁定 8）——改口只追加
裁决行（boundary 流 append-only），仍零选择影响；有效否认模长的消费是 M5 的事。

方向 = 被否认事件家族方向的负向（−D_ev，复用 ops/recall_geometry.token_direction）。

M3 目标口径：否认挂在**本轮召回候选事件**上。"不在当前候选的 X 以后别提"的
全量 referent 匹配留待 M5。

边界流两种行（append-only，fold 出状态）：
- 声明行：ts / turn / turn_id / tier / phrase / denial_id / target_source /
  target_record_ts / text="[边界·否认]" / category="boundary" /
  strength=声明初始模长 / direction=−D_ev（无家族方向 → None）/
  half_life_ms / status="unconfirmed" / params_sha
- 裁决行（M4）：kind="deny_ruling" / ts / turn / denial_id / verdict
  （confirmed|falsified|stale）/ strength（证实×倍率 / 证伪=0）/ half_life_ms

裁决纪律（全部 [拍脑袋待标定]，D1 回标）：
- 裁决只看**声明之后**回合的反应（声明当轮的情绪不构成证据——
  "打字说了"不能被自己证实）；
- 证实 = 情绪浓/投入升越过个人基线+margin；证伪 = 对抗升越过基线+margin；
  同时越线 → 证伪优先（对抗是更硬的边界证据）；
- 观察窗口（轮）内未越线 → 不裁决；窗口过 → stale（观察结束，仍零影响）；
- 每张声明至多一次改口；未证实前对召回零影响。
"""
from core.params import DENY

BOUNDARY_TOKEN_TEXT = "[边界·否认]"


def detect_denial(text, params=None):
    """用户文本 → 最高级否认命中（永久先于暂时）；无命中 → None。"""
    p = params or DENY
    content = str(text or "")
    lexicon = p.get("lexicon") or {}
    for tier in ("permanent", "temporary"):
        for phrase in lexicon.get(tier) or []:
            if phrase in content:
                return {"tier": tier, "phrase": phrase}
    return None


def boundary_direction(record):
    """−D_ev：被否认事件家族方向的负向；事件无家族方向 → None。"""
    from ops.recall_geometry import token_direction
    direction = token_direction((record or {}).get("tokens") or [])
    if direction is None:
        return None
    return {ax: round(-value, 4) for ax, value in direction.items()}


def _half_life_ms(tier, params):
    return float(params["half_life_long_ms"] if tier == "permanent"
                 else params["half_life_short_ms"])


def denial_id(row):
    """挂账行稳定标识：同一回合、同一被否认事件 = 同一张声明。"""
    return "deny:%d:%s:%d" % (int(row.get("ts") or 0),
                              str(row.get("target_source") or ""),
                              int(row.get("target_record_ts") or 0))


def turn_index_of(turn_id):
    """turn_id（"turn:<ts>:<seq>"）→ 回合序号；不可解析 → None。"""
    if not turn_id:
        return None
    try:
        return int(str(turn_id).rsplit(":", 1)[1])
    except (ValueError, IndexError):
        return None


def declare_denials(*, text, candidates, now_ms, turn_id=None, turn=None,
                    params=None, params_sha=None):
    """文本 × 本轮召回候选 → 边界词元挂账行（零压制，纯产出，不写库）。"""
    p = params or DENY
    detection = detect_denial(text, p)
    if detection is None:
        return []
    rows = []
    for candidate in candidates or []:
        if not isinstance(candidate, dict):
            continue
        record = candidate.get("_record")
        if not isinstance(record, dict):
            record = candidate
        source = str(candidate.get("source", ""))
        if source not in {"memory", "ledger"}:
            # 语义通道事件无词元/drift：M3 不挂账（语义轴留给 M5）
            continue
        row = {
            "ts": int(now_ms),
            "tier": detection["tier"],
            "phrase": detection["phrase"],
            "target_source": source,
            "target_record_ts": int(record.get("ts") or 0),
            "text": BOUNDARY_TOKEN_TEXT,
            "category": "boundary",
            "strength": round(float(p["declare_strength"]), 4),
            "direction": boundary_direction(record),
            "half_life_ms": _half_life_ms(detection["tier"], p),
            "status": "unconfirmed",
        }
        row["denial_id"] = denial_id(row)
        if turn is not None:
            row["turn"] = int(turn)
        if turn_id is not None:
            row["turn_id"] = str(turn_id)
        if params_sha is not None:
            row["params_sha"] = str(params_sha)
        rows.append(row)
    return rows


# ---------------------------------------------------------------------
# M4：R 方向裁决（消费 M1 反应词元族，裁定 8 口径）
# ---------------------------------------------------------------------
def r_parts_history(store, max_turns=None):
    """最近已完成回合的 R 分量历史（breakpoints extra.R_parts，M1 落盘）。

    按 ts 时间序取尾部（断点可能乱序写入）；不包含当前回合（裁决在断点落盘前）。
    """
    rows = []
    for rec in store.read_breakpoints():
        extra = rec.get("extra") or {}
        parts = extra.get("R_parts")
        if isinstance(parts, dict):
            rows.append((int(rec.get("ts") or 0), parts))
    rows.sort(key=lambda item: item[0])
    if max_turns is not None:
        rows = rows[-max_turns:]
    return [parts for _ts, parts in rows]


def reaction_baseline(history, params=None):
    """个人反应基线：各方向分量在历史中的中位值；样本不足 → None（冷启动不裁决）。"""
    p = params or DENY
    history = list(history or [])
    if len(history) < int(p.get("confirm_baseline_warmup_n", 5)):
        return None
    out = {}
    for key in ("emo", "atk", "len"):
        values = sorted(float(parts.get(key, 0.0) or 0.0) for parts in history)
        out[key] = values[len(values) // 2]
    return out


def r_direction_signal(parts, baseline, params=None):
    """本轮 R 方向（裁定 8）：情绪浓/投入升 = 证实、对抗升 = 证伪。

    同时越线 → 证伪优先（对抗是更硬的边界证据）；无基线/无越线 → None。
    """
    p = params or DENY
    if not baseline:
        return None
    margin = float(p["confirm_r_margin"])
    parts = parts or {}
    if float(parts.get("atk", 0.0) or 0.0) >= float(baseline.get("atk", 0.0)) + margin:
        return "falsify"
    if (float(parts.get("emo", 0.0) or 0.0) >= float(baseline.get("emo", 0.0)) + margin
            or float(parts.get("len", 0.0) or 0.0) >= float(baseline.get("len", 0.0)) + margin):
        return "confirm"
    return None


def _already_ruled(rows, row_id):
    return any(row.get("kind") == "deny_ruling" and row.get("denial_id") == row_id
               for row in rows if isinstance(row, dict))


def adjudicate_denials(*, store, parts, now_ms, turn, turn_id=None, params=None,
                       history=None):
    """对观察窗口内的未决否认声明做 R 方向裁决；只追加裁决行，不改声明行。

    - 声明当轮（age=0）不裁决：声明自己的情绪不是证据；
    - age ∈ [1, window]：confirm → 模长×confirm_strength_mult、
      半衰期×confirm_half_life_mult；falsify → 候选撤销（strength=0）；无越线不裁决；
    - age > window 且未证实 → stale（观察结束，仍零影响）；
    - 无 "turn" 的声明行（旧形状）跳过：窗口按轮算，无法归龄则不猜。
    返回追加的裁决行（已写入 boundary 流）。
    """
    p = params or DENY
    rows = store.read_boundary()
    baseline = (reaction_baseline(history, p) if history is not None
                else reaction_baseline(r_parts_history(store), p))
    window = int(p["confirm_r_dir_window"])
    rulings = []
    for row in rows:
        if not isinstance(row, dict) or row.get("kind") == "deny_ruling":
            continue
        if "turn" not in row:
            continue
        row_id = row.get("denial_id") or denial_id(row)
        if _already_ruled(rows, row_id):
            continue
        age = int(turn) - int(row["turn"])
        if age < 1:
            continue                      # 声明当轮：不构成证据
        signal = (r_direction_signal(parts, baseline, p)
                  if age <= window else None)
        if age > window:
            verdict, strength = "stale", 0.0
            half_life = row.get("half_life_ms")
        elif signal == "confirm":
            verdict = "confirmed"
            strength = round(float(row.get("strength", 0.0))
                             * float(p["confirm_strength_mult"]), 4)
            half_life = float(row.get("half_life_ms") or 0.0) \
                * float(p["confirm_half_life_mult"])
        elif signal == "falsify":
            verdict = "falsified"
            strength = 0.0
            half_life = row.get("half_life_ms")
        else:
            continue                      # 窗口内无越线：继续观察
        ruling = {"ts": int(now_ms), "kind": "deny_ruling", "denial_id": row_id,
                  "verdict": verdict, "strength": strength,
                  "half_life_ms": half_life, "turn": int(turn)}
        if turn_id is not None:
            ruling["turn_id"] = str(turn_id)
        rulings.append(ruling)
    for ruling in rulings:
        store.append_boundary(ruling)
    return rulings


def effective_denials(store):
    """fold 边界流 → 当前有效否认（M5 压制的输入；M4 只产不改任何选择）。

    有效 = 声明 × 最新裁决行，且 verdict=confirmed；时间衰减作用于模长是 M5
    接线时的几何消费，此处只交 strength/half_life_ms/declared_ts。
    """
    declarations, rulings = {}, {}
    for row in store.read_boundary():
        if not isinstance(row, dict):
            continue
        if row.get("kind") == "deny_ruling":
            row_id = row.get("denial_id")
            if row_id and (row_id not in rulings
                           or int(row.get("ts") or 0) >= int(rulings[row_id].get("ts") or 0)):
                rulings[row_id] = row
        else:
            row_id = row.get("denial_id") or denial_id(row)
            declarations[row_id] = row
    out = []
    for row_id, declaration in declarations.items():
        ruling = rulings.get(row_id)
        if ruling is None or ruling.get("verdict") != "confirmed":
            continue
        out.append({
            "denial_id": row_id,
            "target_source": declaration.get("target_source"),
            "target_record_ts": declaration.get("target_record_ts"),
            "direction": declaration.get("direction"),
            "strength": float(ruling.get("strength") or 0.0),
            "half_life_ms": float(ruling.get("half_life_ms") or 0.0),
            "declared_ts": int(declaration.get("ts") or 0),
        })
    return out


# ---------------------------------------------------------------------
# M5：几何压制消费者（边界词元 → 候选压制因子；用户翻回立即失效）
# ---------------------------------------------------------------------
def _text_hits_referent(text, record):
    """用户原文是否重提被否认事件的话题词（CJK 词边界，零 LLM）。"""
    from ops.lexmatch import word_hit_count
    content = str(text or "")
    if not content:
        return False
    words = []
    for token in (record or {}).get("tokens") or []:
        words.append(str(token.get("text") if isinstance(token, dict) else token or ""))
    topic = str((record or {}).get("topic") or "")
    if topic:
        words.append(topic)
    return any(word and word_hit_count(word, content) > 0 for word in words)


def suppression_factor(candidate, denial, now_ms, params=None):
    """候选 × 有效否认 → 压制因子 ∈ [suppress_floor, 1]（残余证据 = 永远留混沌）。

    有效模长 = strength × 0.5^(Δt/half_life)（时间衰减作用于模长）；
    对齐 = max(0, cos(D_candidate, −boundary.direction))——boundary.direction =
    −D_ev，故对齐即候选与被否认事件方向的同向度；无方向/无对齐 → 1.0（不压制）。
    """
    p = params or DENY
    from ops.recall_geometry import directional_cosine, token_direction
    record = candidate.get("_record") if isinstance(candidate, dict) else None
    if not isinstance(record, dict):
        record = candidate
    boundary = (denial or {}).get("direction")
    candidate_dir = token_direction((record or {}).get("tokens") or [])
    if not boundary or not candidate_dir:
        return 1.0
    denied_dir = {ax: -float(boundary[ax]) for ax in boundary}
    alignment = max(0.0, directional_cosine(candidate_dir, denied_dir))
    if alignment <= 0:
        return 1.0
    delta_ms = max(0.0, float(now_ms) - float(denial.get("declared_ts") or 0))
    half_life = float(denial.get("half_life_ms") or 1.0)
    magnitude = float(denial.get("strength") or 0.0) * (0.5 ** (delta_ms / half_life))
    return round(max(float(p["suppress_floor"]), 1.0 - magnitude * alignment), 4)


def apply_denial_suppression(*, store, candidates, text, now_ms, turn_id=None,
                             params=None):
    """把有效否认应用到本轮候选（M5 消费端；仅在显式策略 unified_suppressed 启用）。

    - 用户翻回：用户原文重提被否认事件的话题词 → 该否认立即失效
      （append verdict=expired_by_user，主权在用户），本轮不压制；
    - 否则：候选被压制——relevance ×= 因子（因子连续 ∈ [suppress_floor, 1]，
      时间衰减作用于模长；地板 = 残余证据永不为零的"混沌"），并在本轮确定性
      仲裁中**排除**（drop_reason=denial_suppressed，压制后不注入）；
    - 方向缺失的否认不参与几何压制（语义轴留给后续）。
    返回 (保留候选, 失效裁决行, 被压制剔除的候选)；不修改传入列表。
    """
    p = params or DENY
    effective = effective_denials(store)
    if not effective:
        return list(candidates or []), [], []
    kept, rejected, expiries = [], [], []
    for candidate in candidates or []:
        if not isinstance(candidate, dict):
            kept.append(candidate)
            continue
        record = candidate.get("_record")
        if not isinstance(record, dict):
            record = candidate
        matching = [denial for denial in effective
                    if denial.get("target_source") == str(candidate.get("source", ""))
                    and int(denial.get("target_record_ts") or 0)
                    == int(record.get("ts") or 0)]
        candidate = dict(candidate)
        if not matching:
            kept.append(candidate)
            continue
        if _text_hits_referent(text, record):
            for denial in matching:
                ruling = {"ts": int(now_ms), "kind": "deny_ruling",
                          "denial_id": denial["denial_id"],
                          "verdict": "expired_by_user", "strength": 0.0,
                          "half_life_ms": float(denial.get("half_life_ms") or 0.0)}
                if turn_id is not None:
                    ruling["turn_id"] = str(turn_id)
                expiries.append(ruling)
            kept.append(candidate)
            continue
        factor = min((suppression_factor(candidate, denial, now_ms, p)
                      for denial in matching), default=1.0)
        if factor < 1.0:
            candidate["relevance_before_suppression"] = candidate.get("relevance")
            candidate["denial_suppression"] = {
                "denial_id": matching[0]["denial_id"], "factor": factor}
            candidate["relevance"] = round(
                float(candidate.get("relevance", 0.0)) * factor, 4)
            candidate["drop_reason"] = "denial_suppressed"
            rejected.append(candidate)
        else:
            kept.append(candidate)
    for ruling in expiries:
        store.append_boundary(ruling)
    return kept, expiries, rejected
