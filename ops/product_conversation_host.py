"""P5e：已登记 direct session 的 UI 外产品宿主。

这个模块只把已存在的 session registry、source journal、bridge 和 exact F16 output
adapter 装配成一条明确的后端边界。它不创建角色／成员／B₀，不导入 Tk，也不拥有
墙钟、dynamic tick、Runner 状态、memory 或 group response 语义。
"""
from __future__ import annotations

import uuid

from ops.product_conversation_bridge import ProductConversationBridge
from ops.product_conversation_journal import ProductConversationSourceJournal
from ops.product_conversation_output_adapter import ProductConversationOutputAdapter
from interaction_extensions.multi_participant.sessions import (
    ProductConversationSessionRegistry,
    ProductDirectConversationSession,
)


class ProductDirectConversationHostError(ValueError):
    """direct host 无法证明其 immutable session / Actor 边界。"""


def _text(value, label):
    text = str(value or "").strip()
    if not text:
        raise ProductDirectConversationHostError("%s不能为空" % label)
    return text


class ProductDirectConversationHost:
    """一条显式登记 direct session 的后端提交/观察入口。

    UI 只需在用户按 Enter 后调用 :meth:`submit_committed_human_text`，随后只读展示
    Actor 的正常 output callback。NPC 已提交输出经 :meth:`observe_committed_actor_output`
    由既有 adapter 严格核验，再成为 source journal fact；不会自动投递给 human 或任何
    其他 NPC。
    """

    def __init__(self, *, registry, session, npc_actor, npc_store, event_id_factory=None):
        if not isinstance(registry, ProductConversationSessionRegistry):
            raise ProductDirectConversationHostError("host 必须使用 ProductConversationSessionRegistry")
        if not isinstance(session, ProductDirectConversationSession):
            raise ProductDirectConversationHostError("host 必须绑定 ProductDirectConversationSession")
        registered = registry.load_direct_session(session.conversation.conversation_id)
        if registered != session:
            raise ProductDirectConversationHostError("host session 必须先以完全相同 identity 登记")
        participants = session.participants_by_id
        human = [item for item in session.participants if item.kind == "human"]
        npc = [item for item in session.participants if item.kind == "npc"]
        if len(human) != 1 or len(npc) != 1:
            raise ProductDirectConversationHostError("direct host 必须有一名 human 与一名 npc")
        if not callable(getattr(
            npc_actor, "post_compat_received_participant_event", None
        )):
            raise ProductDirectConversationHostError(
                "NPC Actor 必须提供 compatibility received-event 入口")
        if not (callable(getattr(npc_store, "read_action_receipts", None))
                and callable(getattr(npc_store, "read_messages", None))):
            raise ProductDirectConversationHostError("NPC local Store 必须可供 exact F16 核验")
        if event_id_factory is not None and not callable(event_id_factory):
            raise ProductDirectConversationHostError("event_id_factory 必须可调用")

        self.registry = registry
        self.session = session
        self.human_participant_id = human[0].participant_id
        self.npc_participant_id = npc[0].participant_id
        self.npc_actor = npc_actor
        self.npc_store = npc_store
        self._event_id_factory = event_id_factory or self._new_event_id
        self._observer_attached = False
        self.source_journal = ProductConversationSourceJournal(
            registry=registry, conversation_id=session.conversation.conversation_id)
        self.bridge = ProductConversationBridge(
            conversation=session.conversation,
            participants=participants,
            npc_actors={self.npc_participant_id: npc_actor},
            source_journal=self.source_journal,
        )
        self.output_adapter = ProductConversationOutputAdapter(
            bridge=self.bridge,
            sender_participant_id=self.npc_participant_id,
            store=npc_store,
        )

    @staticmethod
    def _new_event_id():
        """只生成一次 source transaction ID，不基于文本、时间或 UI 状态推断。"""
        return "evt:human:" + uuid.uuid4().hex

    def submit_committed_human_text(self, text, *, committed_at_ms):
        """Enter 已提交的正文唯一进入产品 direct 线的入口。"""
        try:
            committed_at_ms = int(committed_at_ms)
        except (TypeError, ValueError) as exc:
            raise ProductDirectConversationHostError("committed_at_ms 必须为整数") from exc
        if committed_at_ms < 0:
            raise ProductDirectConversationHostError("committed_at_ms 必须为非负整数")
        event_id = _text(self._event_id_factory(), "event_id_factory 返回的 event_id")
        try:
            return self.bridge.submit_external_message(
                event_id=event_id,
                sender_participant_id=self.human_participant_id,
                text=_text(text, "已提交文本"),
                committed_at_ms=committed_at_ms,
            )
        except ValueError as exc:
            raise ProductDirectConversationHostError(str(exc)) from exc

    def observe_committed_actor_output(self, kind, payload):
        """只读转交已有 F16 projection；非 committed kind 由 adapter 明确零副作用忽略。"""
        return self.output_adapter.observe_actor_output(kind, payload)

    def attach_committed_output_observer(self):
        """将既有 Actor 的 F16-only observer 显式接给本 host；不会接 UI callback。"""
        if self._observer_attached:
            return False
        register = getattr(self.npc_actor, "add_committed_output_observer", None)
        if not callable(register):
            raise ProductDirectConversationHostError("NPC Actor 不支持 committed-output observer")
        registered = register(self.observe_committed_actor_output)
        if registered is False:
            # Actor 已有同一个 host 的 observer 仍表示该 host 可以安全工作；本地
            # 标志避免下一次 host 调用再重复登记。
            self._observer_attached = True
            return False
        self._observer_attached = True
        return True
