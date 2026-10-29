"""模拟标定的固定多回合场景资产。

与 ``ops.simulated_users`` 一样，本模块只供测试/评审夹具使用。场景的公共历史和用户轮次可被
测试 Runner 按正常档案方式写入；隐藏 oracle 仅用于验收是否发生了误召回、漏召回或边界失败，
不得进入 NPC prompt。
"""
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Dict, Iterable, List, Tuple, Union


SIMULATION_SCENARIO_SCHEMA_VERSION = 1
DEFAULT_SCENARIO_PATH = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "presets" / "simulation_scenarios.json"


@dataclass(frozen=True)
class ScenarioHistory:
    role: str
    text: str
    topic: str
    salience: float
    ledger_fact: str = ""


@dataclass(frozen=True)
class ScenarioTurn:
    turn_id: str
    text: str
    topic: str
    delay_ms: int


@dataclass(frozen=True)
class PublicSimulationScenario:
    history: Tuple[ScenarioHistory, ...]
    turns: Tuple[ScenarioTurn, ...]
    declared_boundaries: Tuple[str, ...] = ()

    def runtime_input(self) -> Dict[str, object]:
        """供未来 harness 消费的唯一场景运行输入；不含预期结果。"""
        return {
            "history": [{"role": item.role, "text": item.text, "topic": item.topic,
                         "salience": item.salience, "ledger_fact": item.ledger_fact}
                        for item in self.history],
            "turns": [{"turn_id": item.turn_id, "text": item.text, "topic": item.topic,
                       "delay_ms": item.delay_ms}
                      for item in self.turns],
            "declared_boundaries": list(self.declared_boundaries),
        }


@dataclass(frozen=True)
class HiddenScenarioOracle:
    recall_required_turns: Tuple[str, ...] = ()
    recall_forbidden_turns: Tuple[str, ...] = ()
    required_dimensions: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SimulationScenario:
    scenario_id: str
    label: str
    public: PublicSimulationScenario
    hidden: HiddenScenarioOracle

    def runtime_input(self) -> Dict[str, object]:
        return self.public.runtime_input()


def _text(row: Dict[str, object], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"场景缺少字段：{field}")
    return value.strip()


def _string_tuple(value: object, field: str) -> Tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{field} 必须是非空字符串数组")
    return tuple(item.strip() for item in value)


def _object(row: Dict[str, object], field: str) -> Dict[str, object]:
    value = row.get(field)
    if not isinstance(value, dict):
        raise ValueError(f"{field} 必须是对象")
    return value


def _parse_history(value: object) -> Tuple[ScenarioHistory, ...]:
    if not isinstance(value, list):
        raise ValueError("public.history 必须是数组")
    rows = []
    for row in value:
        if not isinstance(row, dict):
            raise ValueError("public.history 中每项都必须是对象")
        try:
            salience = float(row.get("salience"))
        except (TypeError, ValueError):
            raise ValueError("public.history.salience 必须是数字")
        if not 0.0 <= salience <= 1.0:
            raise ValueError("public.history.salience 必须在 [0, 1]")
        role = str(row.get("role", "npc")).strip()
        if role not in {"user", "npc"}:
            raise ValueError("public.history.role 只能是 user 或 npc")
        ledger_fact = row.get("ledger_fact", "")
        if ledger_fact is None:
            ledger_fact = ""
        if not isinstance(ledger_fact, str):
            raise ValueError("public.history.ledger_fact 必须是字符串")
        if ledger_fact.strip() and role != "npc":
            raise ValueError("public.history.ledger_fact 只能来自 npc 历史消息")
        rows.append(ScenarioHistory(role=role, text=_text(row, "text"), topic=_text(row, "topic"),
                                    salience=salience, ledger_fact=ledger_fact.strip()))
    return tuple(rows)


def _parse_turns(value: object) -> Tuple[ScenarioTurn, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("public.turns 必须是非空数组")
    rows = []
    for row in value:
        if not isinstance(row, dict):
            raise ValueError("public.turns 中每项都必须是对象")
        try:
            delay_ms = int(row.get("delay_ms", 0))
        except (TypeError, ValueError):
            raise ValueError("public.turns.delay_ms 必须是整数")
        if delay_ms < 0:
            raise ValueError("public.turns.delay_ms 不能为负数")
        rows.append(ScenarioTurn(turn_id=_text(row, "id"), text=_text(row, "text"),
                                 topic=_text(row, "topic"), delay_ms=delay_ms))
    ids = [turn.turn_id for turn in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("同一场景的 turn id 必须唯一")
    return tuple(rows)


def _parse_scenario(row: Dict[str, object]) -> SimulationScenario:
    public = _object(row, "public")
    hidden = _object(row, "hidden")
    turns = _parse_turns(public.get("turns"))
    turn_ids = {turn.turn_id for turn in turns}
    required = _string_tuple(hidden.get("recall_required_turns"), "hidden.recall_required_turns")
    forbidden = _string_tuple(hidden.get("recall_forbidden_turns"), "hidden.recall_forbidden_turns")
    if set(required) - turn_ids or set(forbidden) - turn_ids:
        raise ValueError("hidden recall oracle 必须引用本场景的 turn id")
    if set(required) & set(forbidden):
        raise ValueError("同一 turn 不能同时要求和禁止召回")
    return SimulationScenario(
        scenario_id=_text(row, "id"),
        label=_text(row, "label"),
        public=PublicSimulationScenario(
            history=_parse_history(public.get("history", [])),
            turns=turns,
            declared_boundaries=_string_tuple(public.get("declared_boundaries"),
                                              "public.declared_boundaries"),
        ),
        hidden=HiddenScenarioOracle(
            recall_required_turns=required,
            recall_forbidden_turns=forbidden,
            required_dimensions=_string_tuple(hidden.get("required_dimensions"),
                                              "hidden.required_dimensions"),
        ),
    )


def load_simulation_scenarios(path: Union[Path, str] = DEFAULT_SCENARIO_PATH) -> List[SimulationScenario]:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or payload.get("schema_version") != SIMULATION_SCENARIO_SCHEMA_VERSION:
        raise ValueError("不支持的模拟场景 schema_version")
    rows = payload.get("scenarios")
    if not isinstance(rows, list) or not rows:
        raise ValueError("scenarios 必须是非空数组")
    scenarios = [_parse_scenario(row) for row in rows if isinstance(row, dict)]
    if len(scenarios) != len(rows):
        raise ValueError("scenarios 中每项都必须是对象")
    ids = [scenario.scenario_id for scenario in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError("模拟场景 id 必须唯一")
    return scenarios


def scenario_registry_report(scenarios: Iterable[SimulationScenario]) -> Dict[str, object]:
    scenarios = list(scenarios)
    return {"schema_version": SIMULATION_SCENARIO_SCHEMA_VERSION,
            "count": len(scenarios), "ids": [scenario.scenario_id for scenario in scenarios]}
