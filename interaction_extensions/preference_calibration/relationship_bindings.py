"""可选偏好扩展的 Human→NPC B₀ 关系绑定纯数据合同。

这不是会话设置：一个绑定只说明某个真实用户对某个 NPC 的显式偏好版本是否可用。
它不持有 Conversation、可见性、状态、记忆或调度资格，因此不能把 B₀ 变成群聊属性，
也不能从互动文本推测用户偏好。每次显式变更都产生一个新的不可变 revision；旧 revision
仍可作为历史 receipt 的精确锚点读取。
"""
from dataclasses import dataclass
from typing import Mapping, Optional

from interaction_extensions.multi_participant.participants import Participant


class RelationshipBindingSchemaError(ValueError):
    """Human→NPC 关系绑定不符合最小合同。"""


def _required_text(value, label):
    text = str(value or "").strip()
    if not text:
        raise RelationshipBindingSchemaError("%s 不能为空" % label)
    return text


@dataclass(frozen=True)
class HumanNpcB0Binding:
    """一个不可变的 Human→NPC B₀ binding revision。

    ``b0_version=None`` 且 ``enabled=True`` 的含义是 identity 校正，而非缺失时猜测偏好。
    这个值只能由后续的显式 B₀ 提交产生的新 revision 替换，不能原地改写。
    """

    human_participant_id: str
    npc_participant_id: str
    enabled: bool = True
    b0_version: Optional[str] = None
    empty_binding_policy: str = "identity"
    revision: int = 1
    parent_revision: Optional[int] = None
    schema_version: int = 1

    def __post_init__(self):
        human_id = _required_text(self.human_participant_id, "human_participant_id")
        npc_id = _required_text(self.npc_participant_id, "npc_participant_id")
        if human_id == npc_id:
            raise RelationshipBindingSchemaError("Human 与 NPC 不能是同一 participant")
        if not isinstance(self.enabled, bool):
            raise RelationshipBindingSchemaError("enabled 必须为布尔值")
        if self.empty_binding_policy != "identity":
            raise RelationshipBindingSchemaError("空 B₀ binding 只能采用 identity 策略")
        version = None if self.b0_version is None else _required_text(self.b0_version, "b0_version")
        if not isinstance(self.revision, int) or self.revision < 1:
            raise RelationshipBindingSchemaError("revision 必须是正整数")
        parent = self.parent_revision
        if parent is not None:
            if not isinstance(parent, int) or parent < 1 or parent >= self.revision:
                raise RelationshipBindingSchemaError("parent_revision 必须指向更早 revision")
        if self.schema_version != 1:
            raise RelationshipBindingSchemaError("不支持的 relationship binding schema_version")
        object.__setattr__(self, "human_participant_id", human_id)
        object.__setattr__(self, "npc_participant_id", npc_id)
        object.__setattr__(self, "b0_version", version)

    @property
    def relation_key(self):
        return "%s::%s" % (self.human_participant_id, self.npc_participant_id)

    def as_record(self):
        return {
            "schema_version": self.schema_version,
            "human_participant_id": self.human_participant_id,
            "npc_participant_id": self.npc_participant_id,
            "enabled": self.enabled,
            "b0_version": self.b0_version,
            "empty_binding_policy": self.empty_binding_policy,
            "revision": self.revision,
            "parent_revision": self.parent_revision,
        }

    @classmethod
    def from_record(cls, raw):
        if not isinstance(raw, Mapping):
            raise RelationshipBindingSchemaError("relationship binding 必须为对象")
        required = {
            "schema_version", "human_participant_id", "npc_participant_id", "enabled",
            "b0_version", "empty_binding_policy", "revision", "parent_revision",
        }
        unknown = set(raw) - required
        missing = required - set(raw)
        if unknown or missing:
            details = []
            if unknown:
                details.append("未知字段：%s" % ", ".join(sorted(unknown)))
            if missing:
                details.append("缺少字段：%s" % ", ".join(sorted(missing)))
            raise RelationshipBindingSchemaError("relationship binding 合同错误（%s）" % "；".join(details))
        return cls(**{field: raw[field] for field in required})


def bind_human_to_npc(human: Participant, npc: Participant, *, enabled=True):
    """建立首个关系 revision；创建时不需要、也不允许传入 conversation。"""
    if not isinstance(human, Participant) or not isinstance(npc, Participant):
        raise RelationshipBindingSchemaError("关系绑定必须引用 Participant")
    if human.kind != "human" or npc.kind != "npc":
        raise RelationshipBindingSchemaError("关系绑定只能是 Human → NPC")
    return HumanNpcB0Binding(
        human_participant_id=human.participant_id,
        npc_participant_id=npc.participant_id,
        enabled=enabled,
    )


def revise_relation_b0(binding: HumanNpcB0Binding, *, b0_version, enabled=None):
    """为一次已获授权的显式 B₀ 版本创建新 revision，不改写原 binding。"""
    if not isinstance(binding, HumanNpcB0Binding):
        raise RelationshipBindingSchemaError("只能修订 HumanNpcB0Binding")
    return HumanNpcB0Binding(
        human_participant_id=binding.human_participant_id,
        npc_participant_id=binding.npc_participant_id,
        enabled=binding.enabled if enabled is None else enabled,
        b0_version=b0_version,
        empty_binding_policy=binding.empty_binding_policy,
        revision=binding.revision + 1,
        parent_revision=binding.revision,
    )


def resolve_relation_b0(binding: HumanNpcB0Binding):
    """纯读取 relation B₀ 的有效模式；不调用 F08、不修改 profile 或可见性。"""
    if not isinstance(binding, HumanNpcB0Binding):
        raise RelationshipBindingSchemaError("只能解析 HumanNpcB0Binding")
    if not binding.enabled:
        mode = "disabled"
    elif binding.b0_version is None:
        mode = "identity"
    else:
        mode = "explicit"
    return {
        "relation_key": binding.relation_key,
        "human_participant_id": binding.human_participant_id,
        "npc_participant_id": binding.npc_participant_id,
        "enabled": binding.enabled,
        "b0_version": binding.b0_version,
        "binding_revision": binding.revision,
        "mode": mode,
    }
