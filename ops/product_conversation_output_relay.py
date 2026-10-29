"""F16 committed output 的 conversation publication。

它不决定谁该说话、更不排回合：每个 Actor 的 F16 output 已经落入自己的 Store 后，
才可按 exact action 形成下一 source event。不同 Actor 可独立调用它并自然并发；候选、
草稿、typing 与模型生成中内容永远没有 publication 权限。
"""
from __future__ import annotations

from typing import Mapping

from interaction_extensions.multi_participant.events import (
    canonical_npc_output_event_id,
    commit_message_event,
)
from interaction_extensions.multi_participant.router import route_committed_event
from interaction_extensions.multi_participant.conversations import frame_for_member
from interaction_extensions.multi_participant.visibility_memory import (
    archive_visible_group_message,
)
from interaction_extensions.multi_participant.local_projection import project_delivery
from interaction_extensions.multi_participant.participants import Participant
from ops.product_conversation_journal import ProductConversationSourceJournal
from interaction_extensions.multi_participant.sessions import (
    ProductConversationSessionRegistry,
)
from interaction_extensions.multi_participant.received_events import (
    received_from_projection,
)


class ProductConversationOutputRelayError(ValueError):
    """显式 output relay 缺少同一已提交 action 的因果证据。"""


def _text(value, label):
    text = str(value or "").strip()
    if not text:
        raise ProductConversationOutputRelayError("%s不能为空" % label)
    return text


class ProductConversationOutputRelay:
    """把 exact F16 output 显式投递到同一会话的其他本地 NPC。

    ``npc_actors`` 与 ``npc_stores`` 仅描述已经具有 local Store/scheduler/writer 的
    active NPC。human 或其他无 local Store 的成员仍可见于 source event snapshot，但
    不会被伪装成 Runner 输入。此对象不持有等待队列、tick 或 output listener。
    """

    _OBSERVED_KINDS = frozenset(("released", "npc_msg"))

    def __init__(self, *, registry, conversation_id, npc_actors, npc_stores):
        if not isinstance(registry, ProductConversationSessionRegistry):
            raise ProductConversationOutputRelayError(
                "relay 必须使用 ProductConversationSessionRegistry")
        session = registry.load_session(conversation_id)
        if session is None:
            raise ProductConversationOutputRelayError("relay 只接受已登记 conversation-session identity")
        if not isinstance(npc_actors, Mapping) or not isinstance(npc_stores, Mapping):
            raise ProductConversationOutputRelayError("npc_actors 与 npc_stores 必须为映射")
        participants = session.participants_by_id
        actor_ids = set(npc_actors)
        store_ids = set(npc_stores)
        local_npc_ids = {
            participant_id
            for participant_id, participant in participants.items()
            if participant.kind == "npc" and participant.capabilities.has_local_store
        }
        if actor_ids != local_npc_ids or store_ids != local_npc_ids:
            raise ProductConversationOutputRelayError(
                "npc_actors 与 npc_stores 必须精确覆盖当前有 local Store 的 NPC")
        for participant_id in local_npc_ids:
            actor = npc_actors[participant_id]
            store = npc_stores[participant_id]
            if not callable(getattr(
                actor, "post_compat_received_participant_event", None
            )):
                raise ProductConversationOutputRelayError(
                    "NPC Actor 必须提供 compatibility received-event 入口")
            if not (callable(getattr(store, "read_action_receipts", None))
                    and callable(getattr(store, "read_messages", None))):
                raise ProductConversationOutputRelayError("NPC local Store 必须可供 exact F16 核验")
        self.registry = registry
        self.session = session
        self.conversation = session.conversation
        self.participants = participants
        self.npc_actors = dict(npc_actors)
        self.npc_stores = dict(npc_stores)
        self.source_journal = ProductConversationSourceJournal(
            registry=registry, conversation_id=self.conversation.conversation_id)
        # 只在 product host 明确 attach 后启用：旧的显式 transport relay 不得假装
        # 已具备三人群聊的 owner-local visible-memory 生命周期。
        self._group_visibility_runners = None

    @staticmethod
    def _payload_fields(kind, payload):
        if kind not in ProductConversationOutputRelay._OBSERVED_KINDS:
            return None
        if not isinstance(payload, Mapping):
            raise ProductConversationOutputRelayError("committed Actor output payload 必须为对象")
        action_id = _text(payload.get("action_id"), "action_id")
        reply = _text(payload.get("reply"), "reply")
        ts_key = "send_at" if kind == "released" else "ts"
        try:
            ts = int(payload[ts_key])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProductConversationOutputRelayError(
                "%s output 缺少 formal %s" % (kind, ts_key)) from exc
        if ts < 0:
            raise ProductConversationOutputRelayError("output ts 必须为非负整数")
        return action_id, reply, ts

    @staticmethod
    def _verify_exact_store_commit(store, *, action_id, reply, ts):
        exact_receipts = []
        for row in list(store.read_action_receipts() or ()):
            if str(row.get("action_id") or "") != action_id:
                continue
            try:
                row_ts = int(row.get("ts"))
            except (TypeError, ValueError):
                continue
            if row_ts == ts:
                exact_receipts.append(row)
        if not exact_receipts:
            raise ProductConversationOutputRelayError(
                "找不到同一 action_id/ts 的 F16 action receipt")
        exact_messages = []
        for row in list(store.read_messages() or ()):
            if row.get("role") != "npc" or str(row.get("action_id") or "") != action_id:
                continue
            try:
                row_ts = int(row.get("ts"))
            except (TypeError, ValueError):
                continue
            if row_ts == ts and str(row.get("text") or "") == reply:
                exact_messages.append(row)
        if not exact_messages:
            raise ProductConversationOutputRelayError(
                "找不到同一 action_id/ts/text 的已提交 NPC message")
        return {
            "schema_version": 1,
            "kind": "npc_output_committed",
            "action_id": action_id,
            "ts": ts,
        }, dict(exact_messages[-1])

    def _causal_address_scope(self, *, sender_id, message):
        """从同一 F16 message 保存的 received-event 因果事实取 reply target。

        不从正文、最近消息、成员顺序或 Actor 意图猜 target。没有 exact context 的输出
        可继续由显式测试/产品调用给 address，但 observer publication 必须 fail-closed。
        """
        context = message.get("conversation_context") if isinstance(message, Mapping) else None
        if not isinstance(context, Mapping):
            return None
        if str(context.get("conversation_id") or "") != self.conversation.conversation_id:
            return None
        try:
            membership_version = int(context.get("membership_version"))
        except (TypeError, ValueError):
            return None
        target = str(context.get("reply_to_participant_id") or "").strip()
        if (membership_version != self.conversation.membership_version
                or str(context.get("owner_participant_id") or "") != sender_id
                or target not in self.conversation.active_member_ids
                or target == sender_id):
            return None
        return (target,)

    def _observer_address_scope(self, *, sender_id, message):
        """由已提交 F16 的因果上下文决定下一 source 的 address。

        带 ``reply_to_participant_id`` 的直接因果回复严格复用既有 target。群聊自主
        opening 没有 received-event context；DIR-157 裁定它向冻结成员快照的其他成员
        广播，由每个 recipient-local F03 的语义/关系结算自行判断相关性与回应资格。
        本方法不读取正文、最近发言者、成员顺序或 Actor 运行态来猜目标。

        context 若存在却不合法，不能伪装成自主 opening，必须继续 fail-closed。
        """
        context = message.get("conversation_context") if isinstance(message, Mapping) else None
        if context is None:
            if self.conversation.kind != "group":
                return None
            return tuple(member for member in self.conversation.active_member_ids
                         if member != sender_id)
        return self._causal_address_scope(sender_id=sender_id, message=message)

    def _event_id_for(self, sender_participant_id, action_id):
        return canonical_npc_output_event_id(
            conversation_id=self.conversation.conversation_id,
            sender_participant_id=sender_participant_id,
            action_id=action_id,
        )

    def _existing_exact_source(self, *, event_id, sender_participant_id, action_id, reply, ts):
        matches = [
            row for row in self.source_journal.read_source_receipts()
            if row.get("sender_action_id") == action_id
            and (row.get("source_event") or {}).get("sender_participant_id")
            == sender_participant_id
        ]
        if not matches:
            return None
        if len(matches) != 1:
            raise ProductConversationOutputRelayError("source journal 的 sender/action_id identity 不唯一")
        source = matches[0]
        event = source.get("source_event") or {}
        if (source.get("event_id") != event_id
                or event.get("sender_participant_id") != sender_participant_id
                or event.get("sender_action_id") != action_id
                or event.get("text") != reply
                or int(event.get("committed_at_ms", -1) or -1) != ts):
            raise ProductConversationOutputRelayError("已归档 action 与当前 F16 payload 不一致")
        return source

    def _archive_group_observers(self, event):
        """为已提交 group source 的 observed 成员留可见记忆，绝不进入 F03。"""
        if self.conversation.kind != "group" or self._group_visibility_runners is None:
            return ()
        archived = []
        for delivery in route_committed_event(event):
            if delivery.delivery_scope != "observed":
                continue
            owner_id = delivery.recipient_participant_id
            report = archive_visible_group_message(
                runner=self._group_visibility_runners[owner_id], event=event,
                owner_participant_id=owner_id)
            archived.append({"participant_id": owner_id, "report": report})
        return tuple(archived)

    def relay_committed_output(self, *, kind, sender_participant_id, payload,
                               addressed_participant_ids=None,
                               derive_address_from_f16_context=False):
        """发布一条 exact F16 output；非 committed kind 保持零副作用。

        显式调用可提供 address scope；这是测试/产品操作者冻结的 event 事实。Actor
        observer 路径优先使用同一 F16 message 已保存的 `reply_to_participant_id`；无该
        context 的 group autonomous F16 则广播给冻结快照中的其他成员。两条路径都不读
        草稿、正文语义、最近发言者或其他 Actor 的运行态。
        """
        fields = self._payload_fields(kind, payload)
        if fields is None:
            return None
        sender_id = _text(sender_participant_id, "sender_participant_id")
        sender = self.participants.get(sender_id)
        if (not isinstance(sender, Participant) or sender.kind != "npc"
                or not sender.capabilities.has_local_store):
            raise ProductConversationOutputRelayError("sender 不是当前 conversation 的本地 NPC")
        action_id, reply, ts = fields
        f16_receipt, committed_message = self._verify_exact_store_commit(
            self.npc_stores[sender_id], action_id=action_id, reply=reply, ts=ts)
        if derive_address_from_f16_context:
            addressed_participant_ids = self._observer_address_scope(
                sender_id=sender_id, message=committed_message)
            if addressed_participant_ids is None:
                return {
                    "status": "not_publishable",
                    "reason": "committed F16 缺少当前 conversation 的合法 address context",
                    "deliveries": [],
                }
        elif addressed_participant_ids is None:
            # 兼容此前 P5f-2 的显式广播测试入口；它不是 observer 自动路径。
            addressed_participant_ids = tuple(
                member for member in self.conversation.active_member_ids if member != sender_id)
        event_id = self._event_id_for(sender_id, action_id)
        existing = self._existing_exact_source(
            event_id=event_id, sender_participant_id=sender_id,
            action_id=action_id, reply=reply, ts=ts)
        if existing is not None:
            return {"status": "already_committed", "source_receipt": existing, "deliveries": []}

        event = commit_message_event(
            self.conversation, event_id=event_id, sender_participant_id=sender_id,
            sender_action_id=action_id, text=reply, committed_at_ms=ts,
            addressed_participant_ids=tuple(addressed_participant_ids),
        )
        try:
            source_receipt = self.source_journal.append_source_event(event)
        except ValueError as exc:
            raise ProductConversationOutputRelayError(str(exc)) from exc
        observer_archives = self._archive_group_observers(event)

        deliveries = []
        for delivery in route_committed_event(event):
            # 可见不等于 received input：observer 的群聊记忆归档留给 group-memory
            # lane；这里的 F03 只允许 explicit addressed delivery 进入。
            if delivery.delivery_scope != "addressed":
                continue
            recipient_id = delivery.recipient_participant_id
            recipient = self.participants[recipient_id]
            if not recipient.capabilities.has_local_store:
                continue
            projection = project_delivery(event, delivery)
            received = received_from_projection(
                projection, response_policy="eligible",
                sender_participant_kind=sender.kind,
                conversation_frame=frame_for_member(
                    self.conversation, recipient_id,
                    addressed_participant_ids=event.addressed_participant_ids))
            self.npc_actors[recipient_id].post_compat_received_participant_event(
                received, now_ms=ts, delay_ms=None)
            deliveries.append({
                "schema_version": 1,
                "kind": "conversation_delivery_dispatched",
                "event_id": event.event_id,
                "conversation_id": event.conversation_id,
                "delivery_id": delivery.delivery_id,
                "membership_version": event.membership_version,
                "sender_participant_id": sender_id,
                "sender_participant_kind": sender.kind,
                "preference_evidence": False,
                "recipient_participant_id": recipient_id,
                "response_policy": "eligible",
            })
        return {
            "status": "committed", "source_receipt": source_receipt,
            "observer_archives": list(observer_archives), "deliveries": deliveries,
        }

    def attach_committed_output_observers(self):
        """把每个 Actor 已提交 F16 的只读投影接入 publication。

        这不是 coordinator：每个 Actor 自己提交后才独立调用自己的 observer；没有
        output、没有 event。observer 只接受 F16 projection，并使用该 action 自带的
        因果 reply target；无 target 时 fail-closed，不会广播或排队。
        """
        if self.conversation.kind == "group":
            visibility_runners = {}
            for participant_id, actor in self.npc_actors.items():
                runner = getattr(actor, "runner", None)
                if (runner is None or getattr(runner, "store", None) is None
                        or getattr(runner, "memory", None) is None):
                    raise ProductConversationOutputRelayError(
                        "group 的自动 F16 publication 需要每位本地 NPC 的 owner Runner")
                visibility_runners[participant_id] = runner
            self._group_visibility_runners = visibility_runners
        attached = []
        for sender_id, actor in self.npc_actors.items():
            register = getattr(actor, "add_committed_output_observer", None)
            if not callable(register):
                raise ProductConversationOutputRelayError(
                    "NPC Actor 必须支持 committed-output observer 才能自动发布")

            def publish(kind, payload, *, _sender_id=sender_id):
                return self.relay_committed_output(
                    kind=kind, sender_participant_id=_sender_id, payload=payload,
                    derive_address_from_f16_context=True)

            if register(publish):
                attached.append(sender_id)
        return tuple(attached)
