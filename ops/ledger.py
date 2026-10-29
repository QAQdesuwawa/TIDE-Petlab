# ops/ledger.py
"""NPC 陈述台账（承 MANE Agent0 台账的聊天版）：自己说过的话，连续性 + 情景记忆。

三层机制（决策全数理、零 LLM）：
1. 连续性：提取 NPC 自己的事实/承诺/立场（异步批量），最近 top_k 条注入提示词
   （"你之前说过……保持前后一致；要改口必须意识到并解释"），矛盾显式记账；
2. 时间衰减：条目定稿时盖章当时的 S（情感浓度）与 field_tokens（话题词元）——
   "AI 的话产生本身就是参数化的"；S_eff = S·e^(−age/半衰期)，超地平线不再注入/触发
   （存储 append-only 不删，"忘"发生在检索层——用户自己也忘了的，AI 不必常挂嘴上）；
3. 复合触发 maybe_recall（复刻用户输入侧三件套：情感浓度 × 相关性 × 概率门）：
   score = w_s·S_eff + w_rel·rel，rel = 词元重合 ∪ 向量余弦；概率门只决定本轮是否
   实际唤起，已提交唤起不会被固定小时冷却再次过滤；
4. 情景时间窗 episode_slice：任一侧触发（用户记忆唤起 / 台账触发 / 提问向量召回）
   → 以触发时刻为中心 ±窗口拉 messages 里**双方**原话节选注入——
   回忆以"事件"为单位整段浮现，像两个人互相记得。
"""
import math
import random
import time

from core.params import LEDGER
from ops.embeddings import has_exact_ngram_overlap
from ops.fixture_provenance import is_synthetic_fixture
from ops.memory import cosine, token_overlap


class Ledger:
    def __init__(self, store, rng=None, params=None):
        self.store = store
        self.rng = rng if rng is not None else random.Random()
        self.params = params or LEDGER

    # ── 连续性与衰减 ──
    def read(self):
        """全部条目（含历史无 S/tokens 的旧行；调用方按需容错）。"""
        return self.store.read_self_ledger()

    def _alive(self, rec, now):
        """地平线：超过 horizon_days 的条目不再注入/触发（"双方都忘了"）。"""
        age_days = (now - rec.get("ts", 0)) / 86_400_000.0
        return age_days <= self.params["horizon_days"]

    def recent_texts(self, k=None, now=None):
        """最近 k 条（时间序，近的在后）；now 缺省 = 不做过期过滤。"""
        k = k or self.params["existing_window"]
        rows = self.read()
        if now is not None:
            rows = [r for r in rows if self._alive(r, now)]
        # 时间序（非文件序）：播种/乱序 merge 时文件序 ≠ 时间序（P4 实证）——
        # 旧实现 read()[-k:] 会把"最晚合并的最旧事件"当最近陈述注入 prompt
        rows = sorted(rows, key=lambda r: r.get("ts", 0))
        return [e["text"] for e in rows[-k:]]

    def top_recent(self, k=None, now=None):
        """注入用：最近 k 条陈述（时间序，近的在后，超地平线不注入）。"""
        return [record["text"] for record in self.top_recent_records(k, now=now)]

    def top_recent_records(self, k=None, now=None, include_synthetic=False):
        """注入候选的完整台账记录；由 prompt 层按已选事件去重。"""
        k = k or self.params["top_k"]
        rows = self.read()
        if not include_synthetic:
            rows = [row for row in rows if not is_synthetic_fixture(row)]
        if now is not None:
            rows = [row for row in rows if self._alive(row, now)]
        rows = sorted(rows, key=lambda r: r.get("ts", 0))
        return rows[-k:]

    def merge(self, facts, ts, contradictions=None, S=0.0, tokens=None, topic=None,
              source=None, seed_id=None, event_id=None):
        """新增陈述（去重）；定稿时刻盖章 S（情感浓度）、tokens（话题词元）与
        topic（当时话题的用户原话节选，向量相关性用）。

        矛盾显式记账（contradicts 指向旧陈述）；矛盾行不盖章。
        """
        normalized_event_id = None
        if event_id is not None:
            normalized_event_id = str(event_id).strip()
            if not normalized_event_id:
                raise ValueError("Ledger annotation event_id 不能为空")
        existing = set(e["text"] for e in self.read())
        added = 0
        for f in facts or []:
            text = str(f).strip()
            if not text or text in existing:
                continue
            record = {"ts": ts, "text": text, "S": float(S),
                      "tokens": list(tokens or []),
                      "topic": str(topic or "")[:self.params["topic_cut_chars"]]}
            if source is not None:
                record["source"] = str(source)
            if seed_id is not None:
                record["seed_id"] = str(seed_id)
            if normalized_event_id is not None:
                # Ledger 是该 committed event 的事实 annotation，而不是另一件
                # 可独立进入 writer/P4 的记忆。无 event_id 的旧行显式保留 legacy
                # 形状，迁移时不得用 ts/text 反猜。
                record["event_id"] = normalized_event_id
            self.store.append_self_ledger(record)
            existing.add(text)
            added += 1
        for c in contradictions or []:
            if isinstance(c, dict) and c.get("old") and c.get("new"):
                record = {"ts": ts, "text": str(c["new"])[:80],
                          "contradicts": str(c["old"])[:80], "S": 0.0, "tokens": [],
                          "topic": ""}
                if source is not None:
                    record["source"] = str(source)
                if seed_id is not None:
                    record["seed_id"] = str(seed_id)
                if normalized_event_id is not None:
                    record["event_id"] = normalized_event_id
                self.store.append_self_ledger(record)
        return added

    # ── 复合触发 ──
    def decayed_salience(self, rec, now):
        """S_eff = S · e^(−age/半衰期)（超地平线 → 0）。"""
        if not self._alive(rec, now):
            return 0.0
        age_days = (now - rec.get("ts", 0)) / 86_400_000.0
        return float(rec.get("S", 0.0)) * math.exp(-age_days / self.params["half_life_days"])

    def candidates(self, query_tokens, query_text, now, embed_fn=None):
        """复合候选：score = w_s·S_eff + w_rel·rel。

        rel = max(词元重合, 向量相关性)——两条独立证据，任一命中即相关：
        词元重合用盖章的话题词元（已剔除准常驻类别）；
        向量相关性 = 现在的话题 vs 当时的话题/陈述原文（BigramEmbedder 可本地算）。
        """
        p = self.params
        query_vec = embed_fn(query_text) if embed_fn else None
        out = []
        for rec in self.read():
            if "contradicts" in rec:      # 矛盾行只是记账，不参与唤起
                continue
            s_eff = self.decayed_salience(rec, now)
            if s_eff <= 0:
                continue
            rel = token_overlap(query_tokens, rec.get("tokens") or [])
            if query_vec is not None:
                for field in ("topic", "text"):
                    target = str(rec.get(field, "")).strip()
                    if target:
                        similarity = cosine(query_vec, embed_fn(target))
                        # 默认 BigramEmbedder 是哈希桶近似：必须有足够长的真实连续
                        # 词面证据，才允许它的余弦成为“记得这件事”的证据。真实
                        # embedding 不带该标记，仍允许同义表达以纯向量相关性召回。
                        if (not getattr(embed_fn, "fallback_relevance_guard", False)
                                or has_exact_ngram_overlap(
                                    query_text, target,
                                    p["fallback_min_shared_ngram_chars"])):
                            rel = max(rel, similarity)
            # 显著性只决定“相关记忆有多值得想起”，不制造相关性。否则无关但高 S 的
            # 旧事会仅凭情绪强度进入 legacy/unified 任一路径，破坏换题与边界纪律。
            if rel <= 0.0:
                continue
            score = p["recall_w_s"] * s_eff + p["recall_w_rel"] * rel
            out.append((score, s_eff, rel, rec))
        out.sort(key=lambda x: -x[0])
        return out

    def weak_probe_candidates(self, query_text, now, minimum_score, query_tokens=None):
        """只读的弱相关探测，供重复提及证据层累计；不参与当前召回选择。

        它刻意不消费 RNG、不检查冷却、更不返回提示词文本给调用方。强相关仍由
        ``candidates`` 负责；这里的结果只能成为后续跨回合的无正文证据。
        """
        from ops.recall_evidence import weak_text_probe
        probes = []
        for record in self.read():
            if "contradicts" in record or self.decayed_salience(record, now) <= 0:
                continue
            target = str(record.get("topic") or record.get("text") or "")
            # 高频资格沿用事件盖章时已有的词元向量和当前回合词元。它只提供一次
            # 可审计的弱相关探测，不能单次越过 strong 资格，也不写任何平行计数器。
            # 无词元的旧记录仍可走保守的词面降级路径，保证旧档案可读取。
            text_score = weak_text_probe(query_text, target)
            token_score = token_overlap(query_tokens or (), record.get("tokens") or ())
            score = max(text_score, token_score)
            if score >= float(minimum_score):
                probes.append({
                    "record": record, "score": score,
                    "probe_basis": ("token_overlap" if token_score > text_score
                                    else "text_overlap"),
                })
        probes.sort(key=lambda item: (-item["score"], -int(item["record"].get("ts", 0))))
        return probes

    def select_recall(self, query_tokens, query_text, now, embed_fn=None):
        """只选择一次 NPC 陈述唤起，不写冷却账本。

        返回 ``(entry, hint, score, probability)``。随机抽样仍恰好发生一次，
        但真正的 recall 行可由上层在输出获得提交资格后再落盘。
        """
        p = self.params
        cands = self.candidates(query_tokens, query_text, now, embed_fn=embed_fn)
        if not cands:
            return None, "", None, None
        score, s_eff, rel, rec = cands[0]
        prob = min(p["recall_p_max"], p["recall_p_base"] + p["recall_p_slope"] * score)
        if self.rng.random() > prob:
            return None, "", None, None
        return rec, self.recall_hint(rec), score, prob

    def recall_hint(self, rec):
        """已选陈述的 prompt 提示；不写台账。"""
        return "你想起你之前说过：" + str(rec.get("text", ""))[:60]

    def maybe_recall(self, query_tokens, query_text, now, embed_fn=None):
        """兼容入口：选择后立即提交一次 NPC 陈述唤起。"""
        rec, hint, score, probability = self.select_recall(
            query_tokens, query_text, now, embed_fn=embed_fn)
        if rec is None:
            return None, ""
        self.commit_recall(rec, now, score=score, probability=probability)
        return rec, hint

    def commit_recall(self, rec, now, score, probability=None, policy=None):
        """提交一次已由外部确定选择的 NPC 陈述唤起。"""
        row = {"ts": now, "recall_of_ts": rec["ts"], "score": round(float(score), 4)}
        if probability is not None:
            row["p"] = round(float(probability), 4)
        if policy is not None:
            row["policy"] = str(policy)
        self.store.append_ledger_recall(row)
        return rec, self.recall_hint(rec)


def episode_slice(store, ts, params=None):
    """情景时间窗：以 ts 为中心 ±window_hours，拉 messages 里双方原话节选。

    返回注入文本（"你想起那阵子……你说过 / 对方说过"），无邻居返回 ""。
    角色视角：NPC 是"你"，用户是"对方"。
    """
    p = params or LEDGER
    half = p["window_hours"] * 3_600_000.0
    cap_each = int(p["window_max_each"])
    cut = int(p["window_cut_chars"])
    user_lines, npc_lines = [], []
    for rec in store.read_messages():
        t = rec.get("ts", 0)
        if abs(t - ts) > half:
            continue
        text = str(rec.get("text", ""))[:cut].rstrip("。！？")
        if not text:
            continue
        (user_lines if rec.get("role") == "user" else npc_lines).append(text)
    user_lines, npc_lines = user_lines[-cap_each:], npc_lines[-cap_each:]
    parts = []
    if npc_lines:
        parts.append("你说过：" + "；".join(npc_lines))
    if user_lines:
        parts.append("对方说过：" + "；".join(user_lines))
    if not parts:
        return ""
    return "你想起那阵子的事——" + "；".join(parts) + "。"


def ledger_section(facts):
    """注入块：台账 → 提示词小节文本（机器零生成纪律外的唯一格式化）。"""
    if not facts:
        return ""
    lines = "\n".join("- " + f for f in facts)
    return ("【你的陈述台账——你之前说过的话，保持前后一致；"
            "若要改口，必须意识到自己改口了并解释。】\n" + lines)
