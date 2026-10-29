"""测试专用模拟用户卡：公开运行条件与隐藏评测 oracle 严格分离。

本模块只供 ``tools/`` 与 ``tests/`` 的模拟标定夹具使用，运行时 ``Runner`` 不导入它。
公开条件只可变成用户本轮文本和直接声明的 B₀；隐藏 oracle 只能由模拟用户环境和评审
读取，不能序列化进 NPC prompt、产品 user_card 或真实用户档案。
"""
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, Union

from interaction_extensions.preference_calibration.cx_six_seven.fit_bus import PROTOTYPES


SIMULATED_USER_SCHEMA_VERSION = 2
DEFAULT_CARD_PATH = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "presets" / "simulated_users.json"


@dataclass(frozen=True)
class PublicSimulatedUserCondition:
    """可被测试运行时消费的最小条件，不含评测期待或行为生成参数。"""

    opening_text: str
    declared_weights: Optional[Dict[str, float]] = None
    declared_preferences: Tuple[str, ...] = ()

    def runtime_input(self) -> Dict[str, object]:
        """唯一允许交给 Runner 的卡侧输入。"""
        return {
            "text": self.opening_text,
            "declared_weights": (dict(self.declared_weights)
                                 if self.declared_weights is not None else None),
        }


@dataclass(frozen=True)
class HiddenSimulatedUserOracle:
    """仅供模拟用户环境/评审使用的隐藏真值，绝不作为运行时输入。"""

    active_topic: str
    active_fact: str
    distractor_topic: str = ""
    distractor_fact: str = ""
    behavior_weights: Optional[Dict[str, float]] = None
    engage_delay_ms: int = 200
    neutral_delay_ms: int = 1_200
    neutral_continues: bool = True
    forbidden_topics: Tuple[str, ...] = ()
    repeat_tolerance: int = 2


@dataclass(frozen=True)
class SimulatedUserCard:
    """版本化测试资产；只通过 ``runtime_input`` 向 NPC 链路提供数据。"""

    card_id: str
    label: str
    public: PublicSimulatedUserCondition
    hidden: HiddenSimulatedUserOracle

    def runtime_input(self) -> Dict[str, object]:
        return self.public.runtime_input()


@dataclass
class SimulatedUser:
    """隐藏状态环境，只读取 NPC 可见文本和发送时间。"""

    oracle: HiddenSimulatedUserOracle
    topic_mentions: int = 0
    rejected: bool = False

    def __post_init__(self):
        if not isinstance(self.oracle, HiddenSimulatedUserOracle):
            raise TypeError("SimulatedUser 只能接收 HiddenSimulatedUserOracle")

    def react(self, npc_text: str, sent_at: int) -> Optional[Dict[str, object]]:
        text = str(npc_text or "")
        if any(topic and topic in text for topic in self.oracle.forbidden_topics):
            self.rejected = True
            return None

        if self.oracle.active_topic and self.oracle.active_topic in text:
            self.topic_mentions += 1
            if self.topic_mentions > self.oracle.repeat_tolerance:
                self.rejected = True
                return None
            return {
                "role": "user",
                "ts": int(sent_at) + self.oracle.engage_delay_ms,
                "text": "对，就是这个，我想继续说。",
                "R": 0.7,
                "sim_signal": "engaged",
            }

        if not self.oracle.neutral_continues:
            self.rejected = True
            return None
        return {
            "role": "user",
            "ts": int(sent_at) + self.oracle.neutral_delay_ms,
            "text": "嗯。",
            "R": 0.2,
            "sim_signal": "neutral_continue",
        }


def _as_tuple(value: object, field_name: str) -> Tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} 必须是字符串数组")
    return tuple(value)


def _as_weights(value: object, field_name: str, *, required: bool = False) -> Optional[Dict[str, float]]:
    """比例输入归一化为五维向量；``None`` 仅在非必填字段表示未声明。"""
    if value is None:
        if required:
            raise ValueError(f"{field_name} 必须包含且仅包含五种原型")
        return None
    if not isinstance(value, dict) or set(value) != set(PROTOTYPES):
        raise ValueError(f"{field_name} 必须包含且仅包含五种原型")
    try:
        weights = {key: float(value[key]) for key in PROTOTYPES}
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} 的权重必须是数字")
    if any(weight < 0 for weight in weights.values()):
        raise ValueError(f"{field_name} 的权重不能为负数")
    total = sum(weights.values())
    if total <= 0:
        raise ValueError(f"{field_name} 的权重和必须大于零")
    return {key: round(weight / total, 6) for key, weight in weights.items()}


def _object(row: Dict[str, object], field_name: str) -> Dict[str, object]:
    value = row.get(field_name)
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} 必须是对象")
    return value


def _text(row: Dict[str, object], field_name: str) -> str:
    value = row.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"模拟用户卡缺少字段：{field_name}")
    return value.strip()


def _parse_card(row: Dict[str, object]) -> SimulatedUserCard:
    public = _object(row, "public")
    hidden = _object(row, "hidden")
    behavior = _object(hidden, "behavior")
    try:
        engage_delay = int(behavior.get("engage_delay_ms", 200))
        neutral_delay = int(behavior.get("neutral_delay_ms", 1_200))
        repeat_tolerance = int(behavior.get("repeat_tolerance", 2))
    except (TypeError, ValueError):
        raise ValueError("hidden.behavior 的延迟和重复容忍度必须是整数")
    if engage_delay < 0 or neutral_delay < 0 or repeat_tolerance < 0:
        raise ValueError("延迟和重复容忍度不能为负数")
    return SimulatedUserCard(
        card_id=_text(row, "id"),
        label=_text(row, "label"),
        public=PublicSimulatedUserCondition(
            opening_text=_text(public, "opening_text"),
            declared_weights=_as_weights(public.get("declared_weights"),
                                          "public.declared_weights"),
            declared_preferences=_as_tuple(public.get("declared_preferences"),
                                           "public.declared_preferences"),
        ),
        hidden=HiddenSimulatedUserOracle(
            active_topic=_text(hidden, "active_topic"),
            active_fact=_text(hidden, "active_fact"),
            distractor_topic=str(hidden.get("distractor_topic") or "").strip(),
            distractor_fact=str(hidden.get("distractor_fact") or "").strip(),
            behavior_weights=_as_weights(hidden.get("behavior_weights"),
                                         "hidden.behavior_weights", required=True),
            engage_delay_ms=engage_delay,
            neutral_delay_ms=neutral_delay,
            neutral_continues=bool(behavior.get("neutral_continues", True)),
            forbidden_topics=_as_tuple(behavior.get("forbidden_topics"),
                                       "hidden.behavior.forbidden_topics"),
            repeat_tolerance=repeat_tolerance,
        ),
    )


def load_simulated_user_cards(path: Union[Path, str] = DEFAULT_CARD_PATH) -> List[SimulatedUserCard]:
    """读取 v2 测试资产；卡 id 唯一，隐藏字段不会出现在 runtime_input 中。"""
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or payload.get("schema_version") != SIMULATED_USER_SCHEMA_VERSION:
        raise ValueError("不支持的模拟用户卡包 schema_version")
    rows = payload.get("cards")
    if not isinstance(rows, list) or not rows:
        raise ValueError("cards 必须是非空数组")
    cards = [_parse_card(row) for row in rows if isinstance(row, dict)]
    if len(cards) != len(rows):
        raise ValueError("cards 中每项都必须是对象")
    ids = [card.card_id for card in cards]
    if len(ids) != len(set(ids)):
        raise ValueError("模拟用户卡 id 必须唯一")
    return cards


def card_registry_report(cards: Iterable[SimulatedUserCard]) -> Dict[str, object]:
    """只暴露卡包元数据，避免报告或 manifest 泄漏隐藏真值。"""
    cards = list(cards)
    return {"schema_version": SIMULATED_USER_SCHEMA_VERSION,
            "count": len(cards), "ids": [card.card_id for card in cards]}


def hidden_oracle_context(oracle: HiddenSimulatedUserOracle) -> Dict[str, object]:
    """仅供测试环境的 AI 用户模拟器读取；调用方不得把返回值交给 NPC Runner。"""
    if not isinstance(oracle, HiddenSimulatedUserOracle):
        raise TypeError("hidden_oracle_context 需要 HiddenSimulatedUserOracle")
    return {
        "active_topic": oracle.active_topic,
        "active_fact": oracle.active_fact,
        "distractor_topic": oracle.distractor_topic,
        "distractor_fact": oracle.distractor_fact,
        "behavior_weights": dict(oracle.behavior_weights or {}),
        "engage_delay_ms": oracle.engage_delay_ms,
        "neutral_delay_ms": oracle.neutral_delay_ms,
        "neutral_continues": oracle.neutral_continues,
        "forbidden_topics": list(oracle.forbidden_topics),
        "repeat_tolerance": oracle.repeat_tolerance,
    }
