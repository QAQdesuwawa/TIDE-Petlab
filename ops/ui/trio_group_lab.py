"""DIR-153 的三 NPC 群聊实验台。

该模块只有两个职责：把冻结 case 与 *已经提交* 的 source journal receipt 编译为只读
视图，以及用 Tk 呈现该视图。它没有 Router、Actor、Runner、tick 或 journal 写入权限。
"""
from __future__ import annotations

import time
import tkinter as tk
from typing import Mapping, Sequence

from ops.config import PALETTE
from ops.conversation_events import CommittedConversationEvent, ConversationEventSchemaError


AVATAR_COLORS = ("#5B5BD6", "#D65B8B", "#0F9C9C")


def _as_text(value: object) -> str:
    return str(value or "").strip()


def _format_time(at_ms: int) -> str:
    try:
        return time.strftime("%H:%M:%S", time.localtime(int(at_ms) / 1000))
    except (TypeError, ValueError, OverflowError):
        return "--:--:--"


def _committed_event_from_receipt(raw: object) -> CommittedConversationEvent | None:
    """只接受 journal 的严格 committed receipt；其它展示候选一律不可见。"""
    if not isinstance(raw, Mapping) or raw.get("kind") != "conversation_event_committed":
        return None
    try:
        return CommittedConversationEvent.from_record(raw.get("source_event"))
    except (ConversationEventSchemaError, TypeError):
        return None


def build_trio_group_lab_snapshot(case: Mapping[str, object],
                                  source_receipts: Sequence[Mapping[str, object]] = ()) -> dict:
    """编译一张不可变群聊展示快照，不写入任何测试或产品事实。"""
    if not isinstance(case, Mapping):
        raise ValueError("trio group lab 必须接收已规范化的 fixture case")
    conversation = case.get("conversation")
    participants = case.get("participants")
    opening = case.get("opening")
    if not isinstance(conversation, Mapping) or not isinstance(participants, Sequence):
        raise ValueError("trio group lab fixture 缺少 conversation/participants")
    if not isinstance(opening, Mapping):
        raise ValueError("trio group lab fixture 缺少 frozen opening")
    member_ids = tuple(conversation.get("active_member_ids") or ())
    if len(member_ids) != 3 or len(set(member_ids)) != 3:
        raise ValueError("trio group lab 只展示恰有三位成员的冻结群聊")
    names = {}
    members = []
    for index, raw in enumerate(participants):
        if not isinstance(raw, Mapping):
            raise ValueError("trio group lab participant 必须为对象")
        participant_id = _as_text(raw.get("participant_id"))
        display_name = _as_text(raw.get("display_name"))
        if participant_id not in member_ids or not display_name or participant_id in names:
            raise ValueError("trio group lab participant identity 不完整")
        names[participant_id] = display_name
        members.append({
            "participant_id": participant_id,
            "display_name": display_name,
            "avatar_text": display_name[0],
            "avatar_color": AVATAR_COLORS[index % len(AVATAR_COLORS)],
            "role_summary": _as_text(raw.get("test_role")),
        })
    if tuple(row["participant_id"] for row in members) != member_ids:
        raise ValueError("trio group lab 成员顺序必须匹配冻结 descriptor")

    opening_sender = _as_text(opening.get("sender_participant_id"))
    opening_addressed = tuple(opening.get("addressed_participant_ids") or ())
    if opening_sender not in names or any(participant_id not in names for participant_id in opening_addressed):
        raise ValueError("trio group lab frozen opening identity 无效")
    messages = [{
        "kind": "frozen_opening",
        "event_id": _as_text(opening.get("event_id")),
        "action_id": None,
        "sender_participant_id": opening_sender,
        "sender_name": names[opening_sender],
        "text": _as_text(opening.get("text")),
        "committed_at_ms": int(opening.get("committed_at_ms") or 0),
        "time_label": _format_time(int(opening.get("committed_at_ms") or 0)),
        "addressed_participant_ids": list(opening_addressed),
        "addressed_names": [names[participant_id] for participant_id in opening_addressed],
        "provenance_label": "冻结测试开场 · 已提交 source",
    }]
    expected_conversation_id = _as_text(conversation.get("conversation_id"))
    expected_version = int(conversation.get("membership_version") or 0)
    for raw in source_receipts or ():
        event = _committed_event_from_receipt(raw)
        # UI 永不猜测或修复 receipt：不同会话/成员版本、非 message、无 F16 identity
        # 都不是本次实验的可见 committed message。
        if (event is None or event.kind != "message" or not event.sender_action_id
                or event.conversation_id != expected_conversation_id
                or event.membership_version != expected_version
                or event.member_snapshot_ids != member_ids
                or event.sender_participant_id not in names
                or any(participant_id not in names for participant_id in event.addressed_participant_ids)):
            continue
        messages.append({
            "kind": "committed_f16",
            "event_id": event.event_id,
            "action_id": event.sender_action_id,
            "sender_participant_id": event.sender_participant_id,
            "sender_name": names[event.sender_participant_id],
            "text": event.text,
            "committed_at_ms": event.committed_at_ms,
            "time_label": _format_time(event.committed_at_ms),
            "addressed_participant_ids": list(event.addressed_participant_ids),
            "addressed_names": [names[participant_id] for participant_id in event.addressed_participant_ids],
            "provenance_label": "F16 已提交 · %s" % event.sender_action_id,
        })
    messages.sort(key=lambda row: (row["committed_at_ms"], row["event_id"]))
    return {
        "schema_version": 1,
        "read_only": True,
        "title": "三 NPC 群聊实验台",
        "subtitle": "只显示冻结开场与已提交 F16；草稿、typing、候选和 pending 均不可见",
        "conversation_id": expected_conversation_id,
        "membership_version": expected_version,
        "members": members,
        "messages": messages,
    }


class TrioGroupLab:
    """纯 Tk 渲染器；它没有输入框、控制按钮或任何引擎对象。"""

    def __init__(self, root: tk.Misc, snapshot: Mapping[str, object], *, use_root: bool = False):
        if not isinstance(snapshot, Mapping) or snapshot.get("read_only") is not True:
            raise ValueError("群聊实验台只接受 read_only snapshot")
        self.root = root
        self.snapshot = dict(snapshot)
        # 桌面主 UI 传入的 root 必须保留为主聊天窗口；实验台是独立、只读的
        # Toplevel。独立启动器才显式把新建的 Tk root 交给实验台自身使用。
        self.window = root if use_root else tk.Toplevel(root)
        self.window.title("TIDE · 三 NPC 群聊实验台")
        self.window.geometry("820x720")
        self.window.minsize(680, 560)
        self.window.configure(bg=PALETTE["bg"])
        self._build()

    def _build(self):
        top = tk.Frame(self.window, bg=PALETTE["panel"], highlightbackground=PALETTE["line"],
                       highlightthickness=1)
        top.pack(fill=tk.X)
        tk.Label(top, text=str(self.snapshot["title"]), bg=PALETTE["panel"], fg=PALETTE["ink"],
                 font=("微软雅黑", 16, "bold")).pack(anchor=tk.W, padx=20, pady=(15, 2))
        tk.Label(top, text=str(self.snapshot["subtitle"]), bg=PALETTE["panel"], fg=PALETTE["sub"],
                 font=("微软雅黑", 9)).pack(anchor=tk.W, padx=20, pady=(0, 14))

        roster = tk.Frame(self.window, bg=PALETTE["bg"])
        roster.pack(fill=tk.X, padx=18, pady=12)
        for member in self.snapshot.get("members", ()):
            self._render_member_card(roster, member)

        divider = tk.Frame(self.window, height=1, bg=PALETTE["line"])
        divider.pack(fill=tk.X, padx=18)
        content = tk.Frame(self.window, bg=PALETTE["chat_bg"])
        content.pack(fill=tk.BOTH, expand=True, padx=18, pady=(12, 18))
        canvas = tk.Canvas(content, bg=PALETTE["chat_bg"], highlightthickness=0)
        scroll = tk.Scrollbar(content, command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        rows = tk.Frame(canvas, bg=PALETTE["chat_bg"])
        item = canvas.create_window(0, 0, window=rows, anchor=tk.NW)
        rows.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(item, width=event.width))
        for message in self.snapshot.get("messages", ()):
            self._render_message(rows, message)
        canvas.yview_moveto(1.0)

    def _render_member_card(self, parent, member: Mapping[str, object]):
        card = tk.Frame(parent, bg=PALETTE["panel"], highlightbackground=PALETTE["line"],
                        highlightthickness=1)
        card.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        avatar = tk.Canvas(card, width=34, height=34, bg=PALETTE["panel"], highlightthickness=0)
        avatar.create_oval(2, 2, 32, 32, fill=str(member["avatar_color"]), outline="")
        avatar.create_text(17, 17, text=str(member["avatar_text"]), fill="white",
                           font=("微软雅黑", 11, "bold"))
        avatar.pack(side=tk.LEFT, padx=(10, 7), pady=10)
        details = tk.Frame(card, bg=PALETTE["panel"])
        details.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=8)
        tk.Label(details, text=str(member["display_name"]), bg=PALETTE["panel"], fg=PALETTE["ink"],
                 font=("微软雅黑", 10, "bold")).pack(anchor=tk.W)
        tk.Label(details, text=str(member.get("role_summary") or ""), bg=PALETTE["panel"],
                 fg=PALETTE["sub"], font=("微软雅黑", 8), justify=tk.LEFT,
                 wraplength=190).pack(anchor=tk.W)

    def _render_message(self, parent, message: Mapping[str, object]):
        sender_id = str(message["sender_participant_id"])
        member = next(row for row in self.snapshot["members"] if row["participant_id"] == sender_id)
        outer = tk.Frame(parent, bg=PALETTE["chat_bg"])
        outer.pack(fill=tk.X, padx=14, pady=8, anchor=tk.W)
        avatar = tk.Canvas(outer, width=34, height=34, bg=PALETTE["chat_bg"], highlightthickness=0)
        avatar.create_oval(2, 2, 32, 32, fill=str(member["avatar_color"]), outline="")
        avatar.create_text(17, 17, text=str(member["avatar_text"]), fill="white",
                           font=("微软雅黑", 11, "bold"))
        avatar.pack(side=tk.LEFT, anchor=tk.N, padx=(0, 8))
        body = tk.Frame(outer, bg=PALETTE["chat_bg"])
        body.pack(side=tk.LEFT, anchor=tk.W)
        addressed = "、".join(message.get("addressed_names") or ())
        tk.Label(body, text="%s · %s   对 %s" % (message["sender_name"], message["time_label"], addressed),
                 bg=PALETTE["chat_bg"], fg=PALETTE["sub"], font=("微软雅黑", 8)).pack(anchor=tk.W)
        bubble = tk.Label(body, text=str(message["text"]), bg=PALETTE["npc_bubble"], fg=PALETTE["ink"],
                          font=("微软雅黑", 11), justify=tk.LEFT, anchor=tk.W, wraplength=500,
                          padx=13, pady=9, highlightbackground=PALETTE["line"], highlightthickness=1)
        bubble.pack(anchor=tk.W, pady=(3, 3))
        tk.Label(body, text=str(message["provenance_label"]), bg=PALETTE["chat_bg"],
                 fg=PALETTE["sub"], font=("微软雅黑", 8)).pack(anchor=tk.W)
