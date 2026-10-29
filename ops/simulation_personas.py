"""模拟标定专用 NPC 人格资产；与产品人格和隐藏 oracle 隔离。"""
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Dict, Iterable, List, Tuple, Union

from interaction_extensions.preference_calibration.cx_six_seven.fit_bus import PROTOTYPES


SIMULATION_NPC_PERSONA_SCHEMA_VERSION = 2
DEFAULT_SIMULATION_NPC_PERSONA_PATH = (
    Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "presets" / "simulation_npc_personas.json")


@dataclass(frozen=True)
class SimulationNPCPersona:
    persona_id: str
    label: str
    persona_card: str
    field_weights: Dict[str, float]
    ammo: Tuple[Dict[str, object], ...] = ()


def _text(row: Dict[str, object], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("模拟 NPC 人格缺少字段：" + field)
    return value.strip()


def _field_weights(row: Dict[str, object]) -> Dict[str, float]:
    value = row.get("field_weights")
    if not isinstance(value, dict) or set(value) != set(PROTOTYPES):
        raise ValueError("模拟 NPC 人格 field_weights 必须包含且仅包含五种原型")
    try:
        weights = {key: float(value[key]) for key in PROTOTYPES}
    except (TypeError, ValueError):
        raise ValueError("模拟 NPC 人格 field_weights 必须为数字")
    if any(weight < 0.0 for weight in weights.values()) or sum(weights.values()) <= 0.0:
        raise ValueError("模拟 NPC 人格 field_weights 必须非负且总和大于零")
    total = sum(weights.values())
    return {key: round(value / total, 6) for key, value in weights.items()}


def _ammo(row: Dict[str, object]) -> Tuple[Dict[str, object], ...]:
    """读取模拟人格自带的私密事实；缺省即没有。

    这是测试资产的一部分，沿用产品角色卡的 ``ammo`` 结构。这里不从人格标签
    推断任何关键词，也不为夹具另建“高张力”规则；每条事实必须由测试矩阵明确给出。
    """
    value = row.get("ammo", [])
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError("模拟 NPC 人格 ammo 必须是对象数组")
    copied = []
    for item in value:
        text = item.get("text")
        keywords = item.get("keywords", [])
        if not isinstance(text, str) or not text.strip():
            raise ValueError("模拟 NPC 人格 ammo 每项必须有 text")
        if isinstance(keywords, str):
            keywords = [keywords]
        if not isinstance(keywords, list) or any(not isinstance(word, str) or not word.strip()
                                                  for word in keywords):
            raise ValueError("模拟 NPC 人格 ammo.keywords 必须是非空字符串数组")
        copied.append({"text": text.strip(), "tier": str(item.get("tier") or "私密"),
                       "keywords": [word.strip() for word in keywords]})
    return tuple(copied)


def load_simulation_npc_personas(
        path: Union[str, Path] = DEFAULT_SIMULATION_NPC_PERSONA_PATH) -> List[SimulationNPCPersona]:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or payload.get("schema_version") != SIMULATION_NPC_PERSONA_SCHEMA_VERSION:
        raise ValueError("不支持的模拟 NPC 人格 schema_version")
    rows = payload.get("personas")
    if not isinstance(rows, list) or not rows:
        raise ValueError("模拟 NPC 人格必须是非空数组")
    personas = [SimulationNPCPersona(_text(row, "id"), _text(row, "label"),
                                     _text(row, "persona_card"), _field_weights(row), _ammo(row))
                for row in rows if isinstance(row, dict)]
    if len(personas) != len(rows):
        raise ValueError("模拟 NPC 人格中每项必须是对象")
    ids = [item.persona_id for item in personas]
    if len(ids) != len(set(ids)):
        raise ValueError("模拟 NPC 人格 id 不能重复")
    return personas


def simulation_npc_persona_report(personas: Iterable[SimulationNPCPersona]):
    values = list(personas)
    return {"schema_version": SIMULATION_NPC_PERSONA_SCHEMA_VERSION,
            "count": len(values), "ids": [item.persona_id for item in values]}
