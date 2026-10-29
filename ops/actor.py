"""单角色事件仲裁器：队列顺序、Runner 事务与 UI 输出之间的唯一边界。

Actor 自己不承载人格/时间算法；它只做三件事：
1. 串行仲裁事件和 Runner 状态写入；
2. 把可能很慢的 Runner 工作放到一个 worker；
3. worker 结果重新入队后，再发出 UI 输出和后续事件。

因此在 LLM 生成期间，新事件会按到达顺序暂存，绝不会与旧事务并发写入
Runner。``start=False`` 保留同步执行，便于确定性单元测试。
"""
import copy
from collections import deque
import inspect
import queue
import threading
import time


FORMAL_REQUIRED_REPLY_KIND = "formal_required_reply"
PUBLIC_KINDS = frozenset((
    "user_message", FORMAL_REQUIRED_REPLY_KIND, "catch_up", "release_pending", "opening_due",
    "advance_demo_clock",
))
FORMAL_PRODUCT_RECIPIENT_INGRESS_KIND = "formal_product_recipient_ingress"
FORMAL_PRODUCT_OBSERVATION_WAKE_KIND = "formal_product_observation_wake"
FORMAL_PRODUCT_AUTONOMOUS_ACTION_KIND = "formal_product_autonomous_action"
_WORK_KINDS = frozenset((
    *PUBLIC_KINDS,
    FORMAL_PRODUCT_RECIPIENT_INGRESS_KIND,
    FORMAL_PRODUCT_OBSERVATION_WAKE_KIND,
    FORMAL_PRODUCT_AUTONOMOUS_ACTION_KIND,
))
# 新/旧计划均由 Actor 从持久化 ``opening`` receipt 重建为 ``opening_due``；
# 旧续话任务一律从持久化 opening receipt 重建；不保留可 post 的旧 writer 名称。
_AUTONOMOUS_KINDS = frozenset(("catch_up", "opening_due"))
# ``release_pending`` 不是自主决策，却仍是一条尚未发出的 NPC 文本；用户提交时
# 它和自主任务一样必须失效，不能在新输入之前补发。
_UNSENT_NPC_KINDS = frozenset((*_AUTONOMOUS_KINDS, "release_pending"))
_DONE = "_task_done"
_FAILED = "_task_failed"
_STREAM_REPLAY = "_stream_replay"
_TURN_PREPARED = "_turn_prepared"
_STREAM_DRAFT = "_stream_draft"
_USER_DRAFT_DONE = "_user_draft_done"
_USER_DRAFT_FAILED = "_user_draft_failed"
_PRESENTATION_ACK = "_presentation_ack"
_STOP = "_stop"


class Actor:
    def __init__(self, runner, params=None, on_output=None, start=True,
                 async_tasks=None, on_telemetry=None,
                 hold_clock_until_startup_catch_up=False,
                 received_event_runner_resolver=None,
                 formal_required_reply_handler=None,
                 command_extensions=()):
        if received_event_runner_resolver is not None and not callable(received_event_runner_resolver):
            raise TypeError("received_event_runner_resolver 必须可调用")
        self.runner = runner
        # Formal v3 session/state belongs to the participant owner, not to a
        # legacy directed-relation view selected for an earlier delivery.
        # Keep that composition-root identity immutable for formal work.
        self._owner_runner = runner
        # 关系 Runner 只是本次 F03/F16 读取和写入哪条 directed state 的视图；
        # Actor 自己的 committed 事件顺序与 pending 候选回执始终归最初的 owner
        # Store。绝不能因稍后收到另一 relation delivery 而把前一条候选的谱系藏进
        # “当前 Runner”。
        self._actor_store = getattr(runner, "store", None)
        # 多 Participant 时，这个 resolver 只在本 Actor 已串行取出一个已验证的
        # ReceivedParticipantEvent 后读取 immutable sender identity，选本次 F03 的
        # relation Runner。它不参与 Router、UI、草稿、正文或任何 tick 排队；选中
        # runner 后仍由此同一 Actor 的唯一队列、墙钟与 action ledger 继续推进。
        self._received_event_runner_resolver = received_event_runner_resolver
        if (formal_required_reply_handler is not None
                and not callable(formal_required_reply_handler)):
            raise TypeError("formal_required_reply_handler 必须可调用或为 None")
        self._formal_required_reply_handler = formal_required_reply_handler
        self._formal_autonomous_action_handler = None
        self._command_extensions = tuple(command_extensions or ())
        self._command_extension_by_kind = {}
        for extension in self._command_extensions:
            kinds = frozenset(getattr(extension, "kinds", ()))
            if not kinds or not all(isinstance(kind, str) and kind for kind in kinds):
                raise TypeError("command extension 必须声明非空字符串 kinds")
            if not callable(getattr(extension, "execute", None)):
                raise TypeError("command extension 必须实现 execute")
            if not callable(getattr(extension, "consume_result", None)):
                raise TypeError("command extension 必须实现 consume_result")
            duplicates = kinds & self._command_extension_by_kind.keys()
            if duplicates:
                raise ValueError("command extension 事件重复: %s" % sorted(duplicates))
            for kind in kinds:
                self._command_extension_by_kind[kind] = extension
        self._public_kinds = frozenset((*PUBLIC_KINDS, *self._command_extension_by_kind))
        self._work_kinds = frozenset((*_WORK_KINDS, *self._command_extension_by_kind))
        if params is None:
            # Legacy Actor defaults belong to the reference product package.
            # Keep that compatibility import outside the package-neutral
            # formal path; a generic composition root passes its own mapping.
            from core.params import ACTOR
            self.p = dict(ACTOR)
        else:
            self.p = dict(params)
        self.on_output = on_output
        # 已提交 output 的后台观察者与 UI presentation callback 严格分开：前者只可
        # 看 F16 后的 projection，不能被误当作“UI 已展示”，也没有 Actor 写权。
        self._committed_output_observers = []
        self._committed_output_observers_lock = threading.RLock()
        # Formal observation plan changes are announced without carrying a
        # plan, coordinate, target, or due decision. A concrete host may use
        # this only to re-read the durable active pointer and arrange waiting.
        self._formal_observation_plan_observers = []
        self._formal_observation_plan_observers_lock = threading.RLock()
        # A transient I/O failure may rearm the same durable wake once. The
        # stable tick identity prevents a persistent storage fault from
        # becoming an unbounded due-now retry loop. This is transient Actor
        # bookkeeping only; the active plan remains the sole schedule fact.
        self._formal_observation_rearmed_tick_ids = set()
        # Product-owned background drivers must terminate even when an embedder
        # stops the Actor directly instead of first detaching the composition.
        self._stop_observers = []
        self._stop_observers_lock = threading.RLock()
        # 观测流独立于既有 UI 消息流：旧客户端不会因新增遥测事件而误判回复顺序。
        self.on_telemetry = on_telemetry
        self.inbox = queue.Queue()
        self._deferred = deque()
        self._deferred_lock = threading.Lock()
        self._inflight = None
        # R4-2：输入 prepare 仍是唯一 Actor writer；responder draft 则只读
        # PreparedTurn，允许它在飞时由下一条 Enter 取消而不占住输入写槽。
        # 条目直到 Actor 串行 commit/abort 后才移除，供 post() 与提交闸共享。
        self._user_drafts = {}
        self._task_seq = 0
        # 用户按 Enter 才推进 interaction epoch；输入框中的草稿不影响 NPC 墙钟。
        # 未发送 NPC 任务携带创建时 epoch，旧 epoch 永远不得开始／提交／释放。
        self._interaction_epoch = 0
        self._epoch_lock = threading.Lock()
        # 生产 Actor 异步，start=False 的纯单测仍走原同步语义。
        self.async_tasks = bool(start) if async_tasks is None else bool(async_tasks)
        # 仅用于只读演示监测：记录本 Actor 最近一次轻量观测的宿主时刻。
        # 它不参与墙钟公式、计划、消息资格或队列仲裁。
        self._last_observe_monotonic = None
        # tick 有自己独立的到期点。普通输入、输出完成或计划重编译可以令活动档
        # 更热、把下一观察提前，却不能把一个已排好的观察反复推迟；否则密集的
        # opening 事务会饿死 F08/F15 的真实墙钟观察。这是队列公平性，不是
        # 人格门限、冷却期或消息条数规则。
        self._next_tick_monotonic = None
        # 仅保存当前角色的可恢复数学计划；真实 timer 仍由本 Actor 线程的 timeout
        # 承担，计划版本由 Runner/ops 写入本地档案。
        self._clock_plan = None
        self._clock_due_monotonic = None
        # F16 给出的主回复发送目标也必须由 Actor 的同一时间仲裁器消费。
        # 它不是 UI after 回调、更不是第二套行为判定：到点先以 F16 的正式发送点
        # release，随后才结算事件循环晚到的 tick，避免向已结算时间线倒插输出。
        self._pending_release_at_ms = None
        self._pending_release_due_monotonic = None
        # UI 只能消费 ``released``，不能为了零延迟路径再 post 一次
        # ``release_pending``。这里保留延迟主回复的无正文元数据，等 Actor 自己
        # 到 F16 deadline 后与正式落盘文本一起发给展示订阅者。
        self._pending_release_meta = None
        # 主回复的因果发送边界先于它派生的 F14/F15 候选。延迟主回复等待 F16
        # 时暂存该候选，等 Actor 自己正式 release 后才入队；绝不读取 UI 动画。
        self._pending_release_opening = None
        # 一个 Actor 只有一个正在等待 F16 的槽位，却可以已经顺序消费多条 exact
        # delivery。队列保存的是各自已完成 F03 的 relation pending，并只让 head
        # 映射到上面的正式 release deadline；它不是第二条 clock/tick，也不制造
        # candidate。sequence 就是 Actor 消费 committed delivery 的顺序。
        self._pending_release_queue = []
        self._active_pending_candidate = None
        self._pending_candidate_sequence = 0
        # 进程恢复时，旧 plan 不能先按“新进程等待时长”抢跑，再与 GUI 显式
        # catch_up 解释同一段离线时间。hold 只收敛启动事务；它不把登录作为
        # 墙钟触发条件，正式追平完成后仍由同一份计划继续运行。
        self._startup_clock_hold = bool(hold_clock_until_startup_catch_up)
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self._rebuild_clock_plan()
        # start=False 是固定逻辑时间的纯同步测试/嵌入式驱动，不得拿宿主墙钟
        # 重标它的历史 pending；真实运行时才执行恢复。
        if start:
            self._restore_pending_release()
        self._arm_tick_deadline()
        if start:
            self.thread.start()

    # ── 投递 ──
    def post(self, kind, payload=None, priority=0):
        if kind not in self._public_kinds:
            raise ValueError(f"未知事件类型: {kind}")
        # 只有本地用户 Enter 才是抢占点。已经由 Router 验证的 received event
        # 是外部 committed 事实；同刻连续 delivery 必须保持 FIFO 并分别进入 F03，
        # 不能由后一条撤销前一条。两者仍复用同一 transport kind，F16/tick/clock
        # 仍完全由既有 Actor 单写事务决定。
        is_local_submission = bool(
            kind in {"user_message", FORMAL_REQUIRED_REPLY_KIND}
            and (payload or {}).get("received_event") is None
        )
        # Enter 是用户线唯一抢占点。它不会暂停用户输入前的 NPC 墙钟，但会让
        # 所有尚未发送的 NPC 候选（在飞、inbox、deferred、延迟释放）整体过期。
        # 已提交消息没有这个标签，仍作为共同历史保留。
        if is_local_submission:
            submission_epoch = self._advance_interaction_epoch()
            payload = dict(payload or {})
            payload["_submission_epoch"] = submission_epoch
            self._discard_stale_deferred()
            # 新 Enter 已经让旧 pending reply 失效；Actor 的本地 deadline 也要
            # 同步撤销，不能让 UI 外的旧定时释放穿过新的 interaction epoch。
            self._cancel_all_pending_release_candidates(
                now_ms=payload.get("now_ms"), reason="local_user_submit")
            active = self._inflight or {}
            # 主回复和自主候选都尚未发送给用户。二者的取消语义不同：自主任务
            # 已有 interaction epoch；主回复使用同一 cancel gate，在 Runner 的
            # 输出提交前被拒绝。绝不能把第二条输入并发写进 Runner。
            if (active.get("kind") in _UNSENT_NPC_KINDS
                    or (active.get("kind") == "user_message"
                        and active.get("main_reply_preemption"))):
                cancel = active.get("cancel_event")
                if active.get("kind") == "user_message":
                    # 与 Runner 的 ``_begin_commit`` 共用同一把极窄锁：新 Enter
                    # 先到则撤销旧稿；旧稿先取得提交权则成为既成历史，当前输入自然
                    # 排在其后。不要靠“再检查一次 Event”赌线程时序。
                    self._cancel_uncommitted_user_turn(active)
                elif cancel is not None:
                    cancel.set()
            # R4-2 draft 不再位于 ``_inflight``；它不能因而逃过新 Enter 的
            # 提交闸。取消只影响尚未提交的 NPC 草稿，用户 F03 事实永不回滚。
            for draft in tuple(self._user_drafts.values()):
                self._cancel_uncommitted_user_turn(draft)
        self._enqueue(kind, payload, priority)
        self._rearm_tick_observation()

    def submit_local_message(self, text, *, now_ms=None, delay_ms=None):
        """Submit one local Enter through the Actor-owned serial lane.

        A formal product composition has its own typed work kind so the UI
        cannot accidentally select the legacy Runner adapter.  An explicitly
        non-formal Actor keeps the old adapter behind this same narrow API.
        """
        payload = {"text": str(text)}
        if now_ms is not None:
            payload["now_ms"] = int(now_ms)
        if delay_ms is not None:
            payload["delay_ms"] = int(delay_ms)
        kind = (FORMAL_REQUIRED_REPLY_KIND
                if self._formal_required_reply_handler is not None
                else "user_message")
        self.post(kind, payload)

    def _post_compat_received_participant_event(
        self, received_event, *, now_ms=None, delay_ms=None,
    ):
        """Enqueue one canonical received event on the legacy serial lane."""
        from ops.received_participant_events import ReceivedParticipantEvent
        if not isinstance(received_event, ReceivedParticipantEvent):
            raise TypeError("Actor 只能接收 ReceivedParticipantEvent")
        payload = {"received_event": received_event, "text": received_event.text}
        if now_ms is not None:
            payload["now_ms"] = int(now_ms)
        if delay_ms is not None:
            payload["delay_ms"] = int(delay_ms)
        self.post("user_message", payload)

    def post_received_participant_event(self, received_event, *, now_ms=None, delay_ms=None):
        """历史公开 adapter；新产品模块不得以它代表 formal recipient work。"""
        return self._post_compat_received_participant_event(
            received_event, now_ms=now_ms, delay_ms=delay_ms)

    def post_compat_received_participant_event(
        self, received_event, *, now_ms=None, delay_ms=None,
    ):
        """把早期 conversation bridge 的 canonical fact 交给旧串行入口。

        queue 内仍复用 ``user_message`` 这个历史 transport kind，以继承同一条 Enter
        抢占／prepare／F16 仲裁事务；Runner 入口仍是 canonical received-event，
        不伪造 formal product recipient work。
        """
        return self._post_compat_received_participant_event(
            received_event, now_ms=now_ms, delay_ms=delay_ms)

    def post_formal_product_recipient_ingress(self, work, *, priority=0):
        """Queue one already-prepared formal ingress on the owner serial lane.

        This narrow method is intentionally separate from ``post``: UI/public
        callers cannot manufacture formal authority from a string event kind.
        The upstream composition root must provide the exact typed evidence.
        """
        from ops.runtime_product_recipient_work import (
            FormalProductRecipientIngressWork,
        )
        if not isinstance(work, FormalProductRecipientIngressWork):
            raise TypeError(
                "Actor formal ingress requires FormalProductRecipientIngressWork")
        self._enqueue(
            FORMAL_PRODUCT_RECIPIENT_INGRESS_KIND,
            {"work": work},
            priority,
        )

    def post_formal_product_observation_wake(self, work, *, priority=0):
        """Queue one coordinate sample on the immutable owner serial lane.

        This is deliberately not a public string event.  The caller supplies
        no target, model payload, timer, or send instruction; the Actor first
        asks the owner Runner to prove the persisted active plan due.
        """
        from ops.runtime_product_observation_work import (
            FormalProductObservationWakeWork,
        )
        if not isinstance(work, FormalProductObservationWakeWork):
            raise TypeError(
                "Actor formal observation wake requires typed work")
        self._enqueue(
            FORMAL_PRODUCT_OBSERVATION_WAKE_KIND,
            {"work": work},
            priority,
        )

    def bind_formal_product_autonomous_action_handler(self, handler):
        """Bind product execution without exposing a public string event."""
        if not callable(handler):
            raise TypeError("formal autonomous action handler must be callable")
        current = self._formal_autonomous_action_handler
        if current is not None and current is not handler:
            raise ValueError("formal autonomous action handler already differs")
        self._formal_autonomous_action_handler = handler
        return handler

    def post_formal_product_autonomous_action(self, work, *, priority=0):
        """Queue one typed already-authorized grant on the owner Actor."""
        from ops.runtime_product_autonomous_work import (
            FormalProductAutonomousActionWork,
        )
        if not isinstance(work, FormalProductAutonomousActionWork):
            raise TypeError(
                "Actor formal autonomous action requires typed work")
        self._enqueue(
            FORMAL_PRODUCT_AUTONOMOUS_ACTION_KIND,
            {"work": work},
            priority,
        )

    def unbind_formal_product_autonomous_action_handler(self, handler=None):
        current = self._formal_autonomous_action_handler
        if handler is not None and current is not handler:
            return False
        self._formal_autonomous_action_handler = None
        return current is not None

    def owns_formal_runner(self, runner):
        """Read-only composition check for this Actor's immutable owner."""
        return runner is self._owner_runner

    def is_stopped(self):
        """Expose lifecycle state without granting queue or Runner authority."""
        return self._stop.is_set()

    def add_formal_observation_plan_observer(self, observer):
        if not callable(observer):
            raise TypeError("formal observation plan observer must be callable")
        with self._formal_observation_plan_observers_lock:
            if observer not in self._formal_observation_plan_observers:
                self._formal_observation_plan_observers.append(observer)
        return observer

    def remove_formal_observation_plan_observer(self, observer):
        with self._formal_observation_plan_observers_lock:
            if observer not in self._formal_observation_plan_observers:
                return False
            self._formal_observation_plan_observers.remove(observer)
            return True

    def add_stop_observer(self, observer):
        if not callable(observer):
            raise TypeError("Actor stop observer must be callable")
        with self._stop_observers_lock:
            if self._stop.is_set():
                raise RuntimeError("cannot bind a stopped Actor")
            if observer not in self._stop_observers:
                self._stop_observers.append(observer)
        return observer

    def remove_stop_observer(self, observer):
        with self._stop_observers_lock:
            if observer not in self._stop_observers:
                return False
            self._stop_observers.remove(observer)
            return True

    def _notify_formal_observation_plan_changed(self):
        with self._formal_observation_plan_observers_lock:
            observers = tuple(self._formal_observation_plan_observers)
        for observer in observers:
            try:
                observer()
            except Exception:
                # The Runner transaction is already committed. A host wake-up
                # failure cannot roll it back or become UI output authority.
                continue

    def _runner_for_received_event(self, received_event):
        """在 Actor 单写事务内，按已冻结 sender identity 选择 relation state 视图。"""
        resolver = self._received_event_runner_resolver
        if resolver is None:
            return self.runner
        selected = resolver(received_event)
        if selected is None or not callable(getattr(selected, "on_received_participant_event", None)):
            raise TypeError("received-event resolver 必须返回 canonical Runner")
        return selected

    def add_committed_output_observer(self, observer):
        """登记一名 F16 output 的只读后台观察者。

        观察者只会收到 Actor 已经投影的 ``released``／``npc_msg``。它不能替代 UI
        展示 callback，不能订阅 draft/reply_result，也不取得 Runner、tick 或消息写权。
        同一 callable 重复登记时保持幂等，便于 host 重开时安全地重新 attach。
        """
        if not callable(observer):
            raise TypeError("committed output observer 必须可调用")
        with self._committed_output_observers_lock:
            if observer in self._committed_output_observers:
                return False
            self._committed_output_observers.append(observer)
            return True

    @staticmethod
    def _is_committed_output_projection(kind, payload):
        """区分 F16 投影与仅供展示的 release/result envelope。

        exact Store 核验仍由 receiver/relay 执行；Actor 在这里仅拒绝明显没有 action
        identity 的 outcome，避免把“本轮没有已提交输出”错送到 publication 边界。
        """
        if kind not in {"released", "npc_msg"} or not isinstance(payload, dict):
            return False
        if not str(payload.get("action_id") or "").strip():
            return False
        if not str(payload.get("reply") or "").strip():
            return False
        time_field = "send_at" if kind == "released" else "ts"
        try:
            return int(payload[time_field]) >= 0
        except (KeyError, TypeError, ValueError):
            return False

    def _released_f16_projection(self, release_payload, *, pending_meta=None):
        """把已释放的 pending reply 还原为可核验的 F16 投影。

        ``Runner.release_pending`` 为兼容既有调用只返回正文；Actor 不能因此把
        pending 元数据当成已经提交的身份。这里只从同一 owner Store 的 action receipt
        与 NPC message 取 ``(action_id, send_at, reply)`` 的唯一交集；找不到或不唯一
        就保持无身份 projection，交给 AUD-042 gate fail-closed。
        """
        if not isinstance(pending_meta, dict) or not release_payload:
            return release_payload
        payload = dict(pending_meta)
        payload["reply"] = release_payload
        try:
            send_at = int(payload.get("send_at"))
        except (TypeError, ValueError):
            return payload
        store = getattr(self.runner, "store", None)
        read_actions = getattr(store, "read_action_receipts", None)
        read_messages = getattr(store, "read_messages", None)
        if not callable(read_actions) or not callable(read_messages):
            return payload
        try:
            action_ids = {
                str(row.get("action_id") or "").strip()
                for row in read_actions()
                if str(row.get("action_id") or "").strip()
                and int(row.get("ts")) == send_at
            }
            message_ids = {
                str(row.get("action_id") or "").strip()
                for row in read_messages()
                if row.get("role") == "npc"
                and str(row.get("action_id") or "").strip()
                and int(row.get("ts")) == send_at
                and str(row.get("text") or "") == str(release_payload)
            }
        except (TypeError, ValueError):
            return payload
        exact_ids = action_ids & message_ids
        if len(exact_ids) == 1:
            payload["action_id"] = exact_ids.pop()
        return payload

    def _enqueue(self, kind, payload=None, priority=0):
        # queue.Queue 保持既有 FIFO 行为；priority 仅保留接口兼容，不能假装生效。
        data = dict(payload or {})
        if kind in _UNSENT_NPC_KINDS and "_interaction_epoch" not in data:
            data["_interaction_epoch"] = self._current_interaction_epoch()
        self.inbox.put((priority, time.time(), kind, data))

    def acknowledge_presentation(self, payload=None):
        """由 Tk 报告“已开始播放”——仅记录展示回执，不改变控制状态。"""
        self._enqueue(_PRESENTATION_ACK, payload, priority=0)

    # ── 用户线 epoch：事务正确性，不参与人格／节律决策 ──
    def _current_interaction_epoch(self):
        with self._epoch_lock:
            return self._interaction_epoch

    def _advance_interaction_epoch(self):
        with self._epoch_lock:
            self._interaction_epoch += 1
            return self._interaction_epoch

    def _is_stale_unsent_npc(self, kind, payload):
        if kind not in _UNSENT_NPC_KINDS:
            return False
        epoch = (payload or {}).get("_interaction_epoch")
        # 直接调用 _handle 的既有同步单测没有调度 epoch，保持原有纯函数语义。
        return epoch is not None and int(epoch) != self._current_interaction_epoch()

    def _main_reply_preemption_available(self):
        """只对显式接受提交闸的 Runner 开启主回复抢占。"""
        if self._formal_required_reply_handler is not None:
            # W2 host commits source/lifecycle/attempt/action as one serial
            # Actor work item.  It has no legacy draft/preemption protocol.
            return False
        if not getattr(self.runner, "main_reply_preemption_supported", False):
            return False
        try:
            params = inspect.signature(self.runner.on_user_message).parameters.values()
            names = {item.name for item in params}
            accepts_kwargs = any(item.kind is inspect.Parameter.VAR_KEYWORD for item in params)
            return accepts_kwargs or {"_should_continue", "_begin_commit"} <= names
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _cancel_uncommitted_user_turn(active):
        """在 Enter 与 Actor output commit 间建立唯一线性化点。"""
        if not active or active.get("kind") != "user_message":
            return False
        cancel = active.get("cancel_event")
        lock = active.get("main_commit_lock")
        if lock is not None:
            with lock:
                if active.get("main_commit_started") or cancel is None:
                    return False
                cancel.set()
                return True
        if cancel is not None:
            cancel.set()
            return True
        return False

    def _has_live_user_draft(self):
        """未撤销 draft 仍持有冻结快照时，tick 只能做只读投影。"""
        return any(
            not row.get("cancel_event", threading.Event()).is_set()
            for row in self._user_drafts.values())

    def _catch_up_accepts_audit_context(self):
        """仅向支持该私有参数的正式 Runner 传来源；兼容旧测试桩。"""
        try:
            params = inspect.signature(self.runner.catch_up).parameters.values()
            return any(item.name == "_audit_context"
                       or item.kind is inspect.Parameter.VAR_KEYWORD
                       for item in params)
        except (TypeError, ValueError, AttributeError):
            return False

    def _catch_up_accepts_cancel_gate(self):
        """取消闸是可选事务能力，不能让窄 Runner 因未知参数失效。"""
        try:
            params = inspect.signature(self.runner.catch_up).parameters.values()
            return any(item.name == "_should_continue"
                       or item.kind is inspect.Parameter.VAR_KEYWORD
                       for item in params)
        except (TypeError, ValueError, AttributeError):
            return False

    def _is_stale_user_submission(self, kind, payload):
        if kind != "user_message" or not self._main_reply_preemption_available():
            return False
        epoch = (payload or {}).get("_submission_epoch")
        return epoch is not None and int(epoch) != self._current_interaction_epoch()

    def _discard_stale_deferred(self):
        """Enter 后仅移除未发送 NPC 工作；用户事务和内部完成事件不重排。"""
        with self._deferred_lock:
            self._deferred = deque(
                item for item in self._deferred
                if not self._is_stale_unsent_npc(item[2], item[3]))

    def stop(self):
        first_stop = not self._stop.is_set()
        self._stop.set()
        if first_stop:
            with self._stop_observers_lock:
                observers = tuple(self._stop_observers)
            for observer in observers:
                try:
                    observer()
                except Exception:
                    continue
        self._enqueue(_STOP, {}, priority=99)
        # 关闭方（GUI 销毁、测试临时目录清理）需要一个明确的“Actor 已不再写档案”
        # 边界。不能在 Actor 自己的线程 join 自己；其余调用方有界等待，避免 Windows
        # 文件句柄尚未释放就删除本地会话目录。这里不等待或取消在飞 LLM worker，业务
        # 提交语义仍由既有单在飞/迟到结果丢弃规则决定。
        if threading.current_thread() is not self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)

    # ── 活跃度 / tick ──
    def _rearm_tick_observation(self):
        # 这里不再按“最近消息数”把角色塞进 hot/warm/cold 档。正式 C/X/e
        # 已提交时，下一次 arm 会从同一份根量读数得到连续观察密度。
        self._arm_tick_deadline()

    def _tick_readout(self):
        if self._formal_runtime_active():
            # A v3 formal session has its own TemporalDomain observation
            # lifecycle.  Until that formal observation is wired below this
            # Actor, the legacy P3/F08 scheduler must stay off rather than read
            # old six/seven profile fields and create a second time authority.
            return {
                "version": "tick-formal-observation-pending-v1",
                "density": None,
                "interval_ms": None,
                "reason": "formal_runtime_owns_temporal_progress",
            }
        # P3 已提交 receipt 是唯一的正式观察来源。计划可以是 idle（没有 F16
        # at_ms），所以不能把它误丢成 None；否则 Actor 会在同一会话里退回旧矩阵。
        plan = self._clock_plan if isinstance(self._clock_plan, dict) else None
        authority = (plan or {}).get("opening_authority_receipt")
        if isinstance(authority, dict):
            formulas = authority.get("formula_versions") or {}
            if (str(authority.get("version") or "").startswith("opening-authority-receipt-v1")
                    and str(formulas.get("schedule_vector") or "").startswith(
                        "schedule-vector-authority-v")
                    # P3 参数包可版本化升级；Actor 只消费同一 authority receipt
                    # 已提交的 Q_tick，不能因 v2 的投影基而静默退回 legacy root
                    # tick 矩阵。历史 v1 仍可回放，未来 vN 也需走同一 receipt 合同。
                    and str(formulas.get("opening_projection") or "").startswith(
                        "opening-from-schedule-vector-authority-v")):
                try:
                    density = float(authority["Q_tick"])
                    interval = int(authority["h_next_ms"])
                except (KeyError, TypeError, ValueError):
                    # 损坏 receipt 不得静默做成 P3；下面保留明确标记的旧档兼容读取。
                    pass
                else:
                    return {
                        "version": "tick-from-opening-authority-v1",
                        "density": density, "interval_ms": interval,
                        "effective_constants_version": str(
                            authority.get("effective_constants_version") or ""),
                        "authority_receipt_version": str(authority.get("version")),
                        "authority_event": dict(authority.get("event") or {}),
                        "bounds_ms": {"min": int(self.p["tick_min_ms"]),
                                      "max": int(self.p["tick_max_ms"])},
                    }
        profile = getattr(self.runner, "profile", {}) or {}
        # 产品 Runner 的观察密度与 F08/F14/F15 必须读取同一会话绑定；没有该
        # 纯读取适配器的窄 Actor stub 才保留其夹具显式 constants。
        binding_reader = getattr(self.runner, "_effective_time_binding", None)
        if callable(binding_reader):
            # 产品 Runner 没有 P3 不能用旧 tick 矩阵补一个“看起来合理”的观察。
            # 新会话在首次 F03 前本就没有墙钟资格；旧档案必须先完成显式 startup
            # catch-up/迁移。返回 unavailable 只暂停 tick，不发送、也不改变状态。
            return {
                "version": "tick-authority-unavailable-v1",
                "density": None, "interval_ms": None,
                "reason": "missing_opening_authority_receipt",
                "effective_constants_version": str(
                    binding_reader().get("effective_constants_version") or ""),
            }
        constants = profile.get("constants") or {}
        version = None
        # 仅没有产品时间绑定能力的窄 stub 允许保留旧矩阵，供历史夹具验证。
        from ops.tick_cadence import tick_readout
        readout = tick_readout(
            getattr(self.runner, "state", {}) or {}, constants, self._runtime_now_ms(), self.p)
        readout["compatibility"] = "legacy_tick_root_vector"
        if version:
            readout["effective_constants_version"] = str(version)
        return readout

    def _tick_ms(self):
        interval = self._tick_readout().get("interval_ms")
        return None if interval is None else int(interval)

    def _arm_tick_deadline(self):
        """安装下一次观察；新活动只能缩短，不得推迟既有 deadline。"""
        interval = self._tick_ms()
        if interval is None:
            self._next_tick_monotonic = None
            return None
        candidate = time.monotonic() + interval / 1000.0
        current = self._next_tick_monotonic
        if current is None or candidate < current:
            self._next_tick_monotonic = candidate
        return self._next_tick_monotonic

    def _formal_runtime_active(self):
        """Whether the immutable owner Runner already has a formal v3 session."""
        return getattr(
            self._owner_runner, "formal_runtime_session", None
        ) is not None

    def tick_status_snapshot(self):
        """返回动态轮询的只读状态，供演示层显示实际调度节律。"""
        now = time.monotonic()
        last = self._last_observe_monotonic
        readout = self._tick_readout()
        return {
            "band": "root_vector",  # 旧 UI 字段名兼容；不再代表离散人格档位。
            "interval_ms": (None if readout.get("interval_ms") is None
                            else int(readout["interval_ms"])),
            "density": (None if readout.get("density") is None
                        else float(readout["density"])),
            "readout": readout,
            "since_observe_ms": (None if last is None else max(0, int((now - last) * 1000))),
            "due_in_ms": (None if self._next_tick_monotonic is None else max(
                0, int((self._next_tick_monotonic - now) * 1000))),
            "inflight": bool(self._inflight),
            "inflight_kind": str((self._inflight or {}).get("kind") or ""),
        }

    # ── 输出和审计（只在 Actor 队列线程执行） ──
    def _emit(self, kind, payload):
        if self.on_output is not None:
            try:
                self.on_output(kind, payload)
                # ``F16 已提交`` 只说明档案已经有了文本；它不等于桌面端已经
                # 收到这条输出。这里记录的是 Actor 成功交给 UI bridge 的事实，
                # 供演示和排障区分 Runner、Actor 与 Tk 三段链路。它绝不参与 F15。
                if kind == "npc_msg" and isinstance(payload, dict):
                    self._log("presentation_enqueued", {
                        "action_id": payload.get("action_id"),
                        "source": payload.get("source"),
                        "ts": payload.get("ts"),
                    })
            except Exception as exc:
                # 过去这里静默吞掉 callback 故障，导致“已落盘但前台未出现”无法
                # 与正常展示区分。仍不让展示故障回滚 F16，只留下无正文审计。
                self._log("presentation_enqueue_failed", {
                    "kind": str(kind), "error": str(exc)[:160],
                    "action_id": (payload or {}).get("action_id")
                    if isinstance(payload, dict) else None,
                })
        # P5 direct host 只能在 F16 已经形成可见 output projection 后归档 source
        # event。它与 UI callback 完全独立：UI 是否成功渲染不会影响 journal，也不
        # 能以任意展示事件触发 observer。adapter 自身的失败同样只记审计，绝不回滚
        # 已提交的 F16 或阻断其他 observer。
        if self._is_committed_output_projection(kind, payload):
            with self._committed_output_observers_lock:
                observers = tuple(self._committed_output_observers)
            for observer in observers:
                try:
                    observer(kind, payload)
                except Exception as exc:
                    self._log("committed_output_observer_failed", {
                        "kind": str(kind), "error": str(exc)[:160],
                        "action_id": (payload or {}).get("action_id")
                        if isinstance(payload, dict) else None,
                    })

    def _emit_telemetry(self, payload):
        if self.on_telemetry is not None:
            try:
                self.on_telemetry(payload)
            except Exception:
                pass

    def _emit_opening_transition(self, event, ts=None, opening=None):
        """把已完成 Actor 事件的 F15 读数追加给只读观测订阅者。

        不推进 state、不重编译 plan、不发送消息；它只是数学总线完成一次真实状态
        转换后留下的投影点。Tk 只能画这些点，不能自行预演下一段曲线。
        """
        try:
            from ops.telemetry import project_opening_transition
            plan = self._clock_plan if isinstance(self._clock_plan, dict) else {}
            authority = (dict(opening) if isinstance(opening, dict) else
                         dict(plan.get("opening_authority_receipt") or {}))
            point = project_opening_transition(
                state=copy.deepcopy(getattr(self.runner, "state", {}) or {}),
                ts=(self._runtime_now_ms() if ts is None else ts), event=event,
                authority_receipt=authority)
            self._emit_telemetry(point)
        except Exception:
            # 观测永远不能阻断正式消息和时间调度。
            pass

    def _consume_turn_prepared(self, payload):
        """消费已落盘的 F03 prepare 事实；仅 Actor 可把它发布为展示起点。"""
        active = self._inflight or {}
        if not (active.get("id") == payload.get("id")
                and active.get("kind") == "user_message"):
            return False
        # F03/input 已在 Runner 的准备阶段正式提交。此后 worker 只是在生成 NPC
        # 草稿；Actor 可以恢复墙钟观察，但不得并发生成输出。
        if payload.get("measurement_committed"):
            active["measurement_committed"] = True
            self._set_clock_plan(payload.get("clock_plan"))
            self._rearm_tick_observation()
            # F03 已在 prepare 阶段落盘。它必须成为演示轨迹的起点：不是把
            # “正在生成”误算为沉默，也不是等 writer 完成后再倒灌一个起点。
            if not active.get("input_transition_emitted"):
                active["input_transition_emitted"] = True
                self._emit_opening_transition("input_committed", ts=payload.get("ts"))
        if not active.get("cancel_event", threading.Event()).is_set():
            # 对 UI 保持既有的纯展示 envelope；调度字段已经在上面由 Actor
            # 消费，不能顺手泄到展示消费者并诱导其参与时钟。
            self._emit("turn_prepared", {
                "presentation": payload.get("presentation") or {},
            })
        return True

    # ── 连续墙钟计划（时间数学在 temporal，Actor 只消费已持久化计划） ──
    def _set_clock_plan(self, plan):
        if isinstance(plan, dict):
            # legacy 字段只能由 WallClock 的持久化迁移器读取和改写。Actor 既不
            # 解释、也不就地转换嵌入宿主传来的旧 plan；否则它会重新成为第二个
            # migration/runtime 语义入口。下次正式 rebuild 会从 Store 完成迁移。
            if ("continuation" in plan
                    or "continuation_due" in (plan.get("reasons") or ())):
                self._clock_plan = None
                self._clock_due_monotonic = None
                return
            if plan.get("at_ms") is None:
                # 旧 idle 计划仍按原语义清空缓存；只有 P3 receipt 的 idle plan 需要
                # 留给下一次观察读取 Q_tick，不能因此扩大其他计划的存活范围。
                if not isinstance(plan.get("opening_authority_receipt"), dict):
                    self._clock_plan = None
                    self._clock_due_monotonic = None
                    return
                self._clock_plan = dict(plan)
                self._clock_due_monotonic = None
                return
            self._clock_plan = dict(plan)
            if self._startup_clock_hold:
                self._clock_due_monotonic = None
                return
            # Runner 的时间轴也服务于离线回放，不能把其中的历史 ts 和宿主 Unix
            # 时间直接相减。以本次计划的 computed_at → at 的逻辑时长换算为进程等待
            # 时长；在线会话中二者天然相同，回放也不会被误判为“立刻到期”。
            base = int(plan.get("computed_at_ms", plan["at_ms"]))
            delay_s = max(0.0, (int(plan["at_ms"]) - base) / 1000.0)
            self._clock_due_monotonic = time.monotonic() + delay_s
        else:
            self._clock_plan = None
            self._clock_due_monotonic = None

    def _set_pending_release(self, send_at_ms):
        """把 F16 的已持久化目标挂到 Actor 时间线，UI 不得提交它。"""
        try:
            at = int(send_at_ms)
        except (TypeError, ValueError):
            self._pending_release_at_ms = None
            self._pending_release_due_monotonic = None
            return
        now = self._runtime_now_ms()
        self._pending_release_at_ms = at
        self._pending_release_due_monotonic = time.monotonic() + max(0.0, (at - now) / 1000.0)

    def _clear_pending_release(self):
        self._pending_release_at_ms = None
        self._pending_release_due_monotonic = None

    def _append_pending_candidate_receipt(self, candidate, status, *, now_ms=None, reason=None):
        """记录 Actor 单线候选的无正文生命周期事实。"""
        candidate["status"] = str(status)
        detail = {
            "sequence": int(candidate["sequence"]),
            "send_at": int(candidate["send_at"]),
            "turn_id": candidate.get("turn_id"),
            "delivery_id": candidate.get("delivery_id"),
            "source_event_id": candidate.get("source_event_id"),
            "counterparty_participant_id": candidate.get("counterparty_participant_id"),
        }
        if reason is not None:
            detail["reason"] = str(reason)
        append = getattr(self._actor_store, "append_event", None)
        if callable(append):
            append({
                "schema_version": 1,
                "kind": "actor_pending_candidate_%s" % status,
                "ts": int(self._runtime_now_ms() if now_ms is None else now_ms),
                "detail": detail,
            })

    @staticmethod
    def _candidate_identity(candidate):
        return (candidate.get("delivery_id")
                or ("turn:" + str(candidate.get("turn_id") or ""))
                or ("sequence:" + str(candidate.get("sequence"))))

    def _queue_pending_release(self, runner, pending_meta, clock_plan, payload):
        """把已形成的 relation pending 排进 owner Actor 的唯一 F16 生命线。"""
        try:
            send_at = int(pending_meta.get("send_at"))
        except (AttributeError, TypeError, ValueError):
            return False
        received = payload.get("received_event") if isinstance(payload, dict) else None
        reference = received.receipt_reference() if received is not None else {}
        self._pending_candidate_sequence += 1
        candidate = {
            "sequence": self._pending_candidate_sequence,
            "runner": runner,
            "send_at": send_at,
            "meta": dict(pending_meta),
            "opening": (clock_plan, dict(pending_meta)),
            "delivery_id": reference.get("delivery_id"),
            "source_event_id": reference.get("event_id"),
            "counterparty_participant_id": reference.get("sender_participant_id"),
            "turn_id": pending_meta.get("turn_id"),
            "status": "queued",
        }
        self._pending_release_queue.append(candidate)
        self._append_pending_candidate_receipt(candidate, "queued", now_ms=send_at)
        self._activate_next_pending_release()
        # `_run_work` 刚刚必须切到本条 relation 才能完成其 F03；但如果已有较早
        # 的 Actor 队首，之后的 deadline/plan 仍属于队首，而不是最后一条 delivery。
        if self._active_pending_candidate is not None:
            self.runner = self._active_pending_candidate["runner"]
        return True

    def _activate_next_pending_release(self):
        """只把队首恢复为 Actor 的正式 F16 deadline，不改变其既有 send_at。"""
        if self._active_pending_candidate is not None:
            return False
        if not self._pending_release_queue:
            return False
        self._pending_release_queue.sort(
            key=lambda row: (int(row["send_at"]), int(row["sequence"])))
        candidate = self._pending_release_queue.pop(0)
        self._active_pending_candidate = candidate
        self.runner = candidate["runner"]
        self._pending_release_meta = dict(candidate["meta"])
        self._pending_release_opening = candidate["opening"]
        self._set_pending_release(candidate["send_at"])
        self._append_pending_candidate_receipt(candidate, "selected")
        return True

    def _finish_active_pending_candidate(self, *, now_ms=None, released=False, reason=None):
        candidate = self._active_pending_candidate
        self._active_pending_candidate = None
        if candidate is not None:
            self._append_pending_candidate_receipt(
                candidate, "released" if released else "deferred",
                now_ms=now_ms, reason=reason)
        self._activate_next_pending_release()
        return candidate

    def _cancel_all_pending_release_candidates(self, *, now_ms=None, reason):
        """本地 Enter 的窄抢占：取消所有仍未成为 F16 的 relation pending。"""
        candidates = list(self._pending_release_queue)
        if self._active_pending_candidate is not None:
            candidates.insert(0, self._active_pending_candidate)
        self._pending_release_queue.clear()
        self._active_pending_candidate = None
        self._clear_pending_release()
        self._pending_release_meta = None
        self._pending_release_opening = None
        for candidate in candidates:
            discard = getattr(candidate.get("runner"), "discard_pending_reply", None)
            if callable(discard):
                discard(now_ms)
            self._append_pending_candidate_receipt(
                candidate, "cancelled", now_ms=now_ms, reason=reason)

    def pending_candidate_receipts(self):
        """供只读 lifecycle audit 取得每个候选的最新 committed 终态。"""
        read = getattr(self._actor_store, "read_events", None)
        rows = read() if callable(read) else ()
        latest = {}
        for row in rows:
            kind = str(row.get("kind") or "")
            if not kind.startswith("actor_pending_candidate_"):
                continue
            detail = dict(row.get("detail") or {})
            key = detail.get("delivery_id") or ("turn:" + str(detail.get("turn_id") or ""))
            latest[key] = {
                "owner_participant_id": getattr(self._actor_store, "user_id", None),
                "status": kind.removeprefix("actor_pending_candidate_"),
                **detail,
            }
        return sorted(latest.values(), key=lambda row: int(row.get("sequence", 0)))

    def _restore_pending_release(self):
        """进程恢复时重新挂载已持久化、但尚未发送的 F16 输出。

        pending reply 是未发生的输出，不会靠 UI 或登录凭空生成；但它已经是 Runner
        持久化的待投递计划。重启后若不恢复 Actor deadline，消息会永久悬挂。恢复前
        以当前 Actor 观察时刻重标 ready/send_at，仍遵守单调时间合同。
        """
        if self._formal_runtime_active():
            return False
        state = getattr(self.runner, "state", {}) or {}
        pending = state.get("pending_reply") if isinstance(state, dict) else None
        if not isinstance(pending, dict) or pending.get("send_at") is None:
            return False
        now = self._runtime_now_ms()
        settle_ready = getattr(self.runner, "settle_pending_reply_ready", None)
        timeline = None
        if callable(settle_ready):
            try:
                timeline = settle_ready(now, turn_id=pending.get("turn_id"))
            except Exception:
                timeline = None
        send_at = int((timeline or {}).get("t_sent", pending.get("send_at")))
        restored = dict(pending)
        restored["send_at"] = send_at
        self._pending_release_meta = restored
        self._pending_release_opening = None
        self._set_pending_release(send_at)
        return True

    def _load_clock_plan(self):
        try:
            load = getattr(self.runner.store, "load_clock_plan", None)
            plan = load() if callable(load) else None
            return dict(plan) if isinstance(plan, dict) else None
        except Exception:
            return None

    @staticmethod
    def _opening_due(plan):
        """读取唯一、已规范化的开口候选。"""
        return bool(isinstance(plan, dict) and "opening_due" in (plan.get("reasons") or ()))

    def _opening_task(self, plan):
        """从已持久化的开口计划构造投递任务，但绝不提前投递。"""
        if not self._opening_due(plan):
            return None
        opening = (plan.get("opening") if isinstance(plan, dict) else {})
        opening = opening if isinstance(opening, dict) else {}
        sources = (opening.get("sources")
                   if isinstance(opening.get("sources"), dict) else {})
        delay = (opening.get("delay_receipt")
                 if isinstance(opening.get("delay_receipt"), dict) else {})
        delay_readout = delay.get("readout") if isinstance(delay.get("readout"), dict) else {}
        # 已持久化 F14/F16 的无正文展示副本：只让 UI 的输入节律和同一候选对齐，
        # 不改变任务是否入队，也不能成为一条未发送消息或新的状态。
        task = {"clock_version": plan.get("version"), "now_ms": plan.get("at_ms"),
                "opening_candidate": dict(opening),
                "opening_edge": dict(plan.get("opening_edge") or {})}
        exact_opening = opening.get("opening")
        task["opening_receipt"] = (dict(exact_opening) if isinstance(exact_opening, dict)
                                   else dict(sources))
        formula_version = str(sources.get("formula_version") or "")
        if (formula_version.startswith("opening-root-vector-")
                or formula_version.startswith("f14-root-vector-")
                or formula_version.startswith("opening-authority-receipt-v1")):
            presentation_receipt = {
                "version": "typing-receipt-v2",
                "event_delta": dict(sources.get("event_delta") or {}),
                "opening": {key: sources.get(key) for key in ("impulse", "gate", "margin")},
                "f16": dict(delay_readout),
            }
            if formula_version.startswith("f14-root-vector-"):
                presentation_receipt["f14"] = dict(presentation_receipt["opening"])
            task["presentation_receipt"] = presentation_receipt
        return task

    def _arm_opening(self, plan, carrier, *, defer_until_release=False):
        """安装唯一 Actor 到期计划；不得把 F14 提前塞进 inbox。"""
        del carrier, defer_until_release
        if not self._opening_due(plan):
            return False
        # UI 表现节律不参与调度。唯一消费者是 _on_tick，到 ``at_ms`` 后会用同
        # 一份计划构造 task、复核资格并进入正式 output 事务。
        self._set_clock_plan(plan)
        return True

    def _rebuild_clock_plan(self):
        if self._formal_runtime_active():
            self._clock_plan = None
            self._clock_due_monotonic = None
            return
        rebuild = getattr(self.runner, "rebuild_wall_clock_plan", None)
        if not callable(rebuild):
            return
        try:
            self._set_clock_plan(rebuild(self._runtime_now_ms()))
        except Exception:
            # 墙钟计划可以从快照重建；恢复失败不能阻断用户主动发来的下一条消息。
            self._clock_plan = None
            self._clock_due_monotonic = None

    def _runtime_now_ms(self):
        """演示会话可提供单调的展示时钟；普通 Runner 仍是实际墙钟。"""
        host_now = int(time.time() * 1000)
        resolve = getattr(self.runner, "runtime_now_ms", None)
        if callable(resolve):
            try:
                return int(resolve(host_now))
            except Exception:
                pass
        return host_now

    def _log(self, kind, detail):
        try:
            self.runner.store.append_event(
                {"ts": int(time.time() * 1000), "kind": kind, "detail": detail})
        except Exception:
            pass

    def _npc_message_payload(self, source, text, presentation_receipt=None):
        """把 Runner 已正式落盘的自主输出原样交给 UI。

        UI 只能消费档案中的真实 ``ts/action_id``，不能用 Actor 收到 worker 结果的
        宿主时间伪造一份第二时间线。测试桩未落盘时才退回当前时刻。
        """
        # 正式 Runner 已将本轮 ``message`` 原样返回。优先使用它的 action_id/ts，
        # 不能再以 ``source + text`` 反查 Store：连续追发恰好同句时，反查可能把
        # 后一条送到前一条的视觉身份上。
        if isinstance(text, dict):
            row = text
            result = {
                "ts": int(row.get("ts", self._runtime_now_ms())),
                "reply": str(row.get("reply") or row.get("text") or ""),
                "action_id": row.get("action_id"),
                "source": str(row.get("source") or source),
            }
            if isinstance(presentation_receipt, dict):
                result["presentation_receipt"] = dict(presentation_receipt)
            return result
        try:
            for row in reversed(self.runner.store.read_messages()):
                if (row.get("role") == "npc" and row.get("source") == source
                        and row.get("text") == text):
                    result = {"ts": int(row.get("ts", self._runtime_now_ms())),
                              "reply": str(row.get("text") or ""),
                              "action_id": row.get("action_id"), "source": source}
                    if isinstance(presentation_receipt, dict):
                        result["presentation_receipt"] = dict(presentation_receipt)
                    return result
        except Exception:
            pass
        result = {"ts": self._runtime_now_ms(), "reply": str(text or ""),
                  "source": source}
        if isinstance(presentation_receipt, dict):
            result["presentation_receipt"] = dict(presentation_receipt)
        return result

    def _online_proactive_presentation_receipt(self, plan):
        """从已持久化的统一开口计划复制无正文 UI 回执。

        Actor 不重算资格、不修改计划；展示读数必须与同一 ``opening_due`` 完全一致，
        不能在这里偷偷重新套一份 F15。
        """
        try:
            opening = plan.get("opening") if isinstance(plan, dict) else None
            sources = opening.get("sources") if isinstance(opening, dict) else None
            if not isinstance(sources, dict):
                # 旧存档只允许展示它已经持久化的读数；Actor 绝不能为了补 UI
                # 又从 state/profile 重新计算一次 F15。缺这份 receipt 就不显示
                # typing 读数，迁移层也不能据此新建计划或输出。
                if not isinstance(opening, dict):
                    return None
                return {"version": "typing-receipt-legacy-v1", "event_delta": {},
                        "f15": {key: opening.get(key) for key in ("impulse", "gate", "margin")}}
            return {
                "version": "typing-receipt-v2",
                "event_delta": dict(sources.get("event_delta") or {}),
                "opening": {key: sources.get(key) for key in ("impulse", "gate", "margin")},
            }
        except (AttributeError, TypeError, ValueError):
            return None

    # ── Runner 事务：worker 仅执行，Actor 消费结果并决定后续事件 ──
    def _run_work(self, kind, payload, on_replay=None, *, defer_user_commit=False,
                  user_stage=None):
        """唯一允许调用 Runner 业务方法的位置；不触碰 UI 或 Actor 队列。"""
        if kind == FORMAL_PRODUCT_AUTONOMOUS_ACTION_KIND:
            from ops.runtime_product_autonomous_work import (
                FormalProductAutonomousActionWork,
            )
            work = payload.get("work")
            if not isinstance(work, FormalProductAutonomousActionWork):
                raise TypeError(
                    "formal autonomous action requires typed work")
            handler = self._formal_autonomous_action_handler
            if not callable(handler):
                raise RuntimeError(
                    "formal autonomous action handler is unavailable")
            return handler(work.tick)
        if kind == FORMAL_PRODUCT_OBSERVATION_WAKE_KIND:
            from ops.runtime_product_observation_work import (
                FormalProductObservationWakeWork,
            )
            work = payload.get("work")
            if not isinstance(work, FormalProductObservationWakeWork):
                raise TypeError(
                    "formal observation wake requires typed work")
            if work.expected_plan_id is not None:
                read_active = getattr(
                    self._owner_runner,
                    "read_formal_active_observation_plan",
                    None,
                )
                if not callable(read_active):
                    raise TypeError(
                        "owner Runner lacks formal active-plan reader")
                active_plan = read_active()
                if active_plan.plan_id != work.expected_plan_id:
                    # A replacement committed before this queued wake reached
                    # the owner lane.  This is normal cancellation, not an
                    # error and never a reason to probe/consume the new plan.
                    return {
                        "probe": None,
                        "tick": None,
                        "stale_plan": True,
                    }
            probe_due = getattr(
                self._owner_runner,
                "probe_formal_product_observation_due",
                None,
            )
            if not callable(probe_due):
                raise TypeError(
                    "owner Runner lacks formal observation due probe")
            probe = probe_due(target_coordinate=work.target_coordinate)
            due_proof = getattr(probe, "due_proof", None)
            if due_proof is None or not isinstance(
                getattr(due_proof, "due", None), bool
            ):
                raise TypeError(
                    "owner Runner returned an invalid observation due probe")
            if not due_proof.due:
                return {"probe": probe, "tick": None}
            commit_tick = getattr(
                self._owner_runner,
                "commit_due_formal_product_observation",
                None,
            )
            if not callable(commit_tick):
                raise TypeError(
                    "owner Runner lacks formal observation tick commit")
            committed = commit_tick(
                target_coordinate=work.target_coordinate,
                participant_id=work.participant_id,
                tick_event_id=work.tick_event_id,
            )
            return {"probe": probe, "tick": committed}
        if kind == FORMAL_PRODUCT_RECIPIENT_INGRESS_KIND:
            from ops.runtime_product_recipient_work import (
                FormalProductRecipientIngressWork,
            )
            work = payload.get("work")
            if not isinstance(work, FormalProductRecipientIngressWork):
                raise TypeError(
                    "formal recipient ingress requires typed work")
            commit = getattr(
                self._owner_runner,
                "commit_formal_product_recipient_ingress",
                None,
            )
            if not callable(commit):
                raise TypeError(
                    "owner Runner lacks formal product recipient ingress")
            ingress = commit(
                route=work.route,
                conversation_registry=work.conversation_registry,
                recipient_participant_id=work.recipient_participant_id,
                semantic_projection=work.semantic_projection,
                measurement_evidence=work.measurement_evidence,
            )
            if ingress.projection.delivery_scope == "observed":
                return {"ingress": ingress, "lifecycle": None}
            if ingress.projection.delivery_scope != "addressed":
                raise ValueError(
                    "formal recipient ingress returned an invalid delivery scope")
            recover = getattr(
                self._owner_runner,
                "recover_formal_product_recipient_lifecycle",
                None,
            )
            if not callable(recover):
                raise TypeError(
                    "owner Runner lacks formal product recipient lifecycle recovery")
            lifecycle = recover(delivery_id=ingress.delivery_id)
            if lifecycle.evidence.response_policy != "eligible":
                return {
                    "ingress": ingress,
                    "lifecycle": lifecycle,
                    "observation_plan": None,
                }
            if work.observation_planning_payload is None:
                return {
                    "ingress": ingress,
                    "lifecycle": lifecycle,
                    "observation_plan": None,
                }
            plan_observation = getattr(
                self._owner_runner,
                "plan_formal_product_recipient_observation",
                None,
            )
            if not callable(plan_observation):
                raise TypeError(
                    "owner Runner lacks formal recipient observation planning")
            observation_plan = plan_observation(
                delivery_id=ingress.delivery_id,
                planning_payload=work.observation_planning_payload,
            )
            return {
                "ingress": ingress,
                "lifecycle": lifecycle,
                "observation_plan": observation_plan,
            }
        if kind == FORMAL_REQUIRED_REPLY_KIND:
            if self._formal_required_reply_handler is None:
                raise RuntimeError("formal required handler is unavailable")
            if payload.get("received_event") is not None:
                raise ValueError("formal required work rejects received_event")
            if payload.get("delay_ms") is not None:
                raise ValueError("formal required work rejects legacy delay_ms")
            return self._formal_required_reply_handler(
                str(payload.get("text", "")),
                now_ms=payload.get("now_ms"),
            )
        if kind == "user_message":
            received_event = payload.get("received_event")
            if received_event is None and self._formal_required_reply_handler is not None:
                raise RuntimeError(
                    "formal local input must use typed required work")
            if received_event is not None:
                from ops.received_participant_events import ReceivedParticipantEvent
                if not isinstance(received_event, ReceivedParticipantEvent):
                    raise TypeError("received_event 必须为 ReceivedParticipantEvent")
                # 不改变 Actor 的队列/时钟身份：只在本条已提交 delivery 的事务开始
                # 时，以 exact sender 选择 relation state。之后所有 F03/F16/P3 读写
                # 仍经 self.runner，因而物理 ledger/clock 保持 Actor 单线。
                self.runner = self._runner_for_received_event(received_event)
                runner_entry = getattr(self.runner, "on_received_participant_event", None)
                runner_prepare = getattr(self.runner, "prepare_received_participant_event", None)
                entry_name = "on_received_participant_event"
            else:
                runner_entry = getattr(self.runner, "on_user_message", None)
                runner_prepare = getattr(self.runner, "prepare_user_message", None)
                entry_name = "on_user_message"
            if not callable(runner_entry):
                raise TypeError("Runner 缺少 %s" % entry_name)
            # 延迟主回复也是尚未发送的 NPC 文本。正式 Runner 提供该窄接口；测试桩
            # 可不实现，避免把 Actor 的提交合同耦合进每个桩的函数签名。
            discard_pending = getattr(self.runner, "discard_pending_reply", None)
            if callable(discard_pending):
                discard_pending(payload.get("now_ms"))
            kwargs = {}
            prepared = payload.get("_on_prepared")
            # 仅正式 Runner 声明支持这份临时展示订阅；测试桩/第三方 Runner 无需
            # 扩展签名，也不能因 UI 辅助能力丢失而改变用户事务。
            if callable(prepared) and getattr(self.runner, "presentation_preview_supported", False):
                # 适配层可包装 Runner；只有真实调用签名接受该可选参数时才订阅，
                # 不能以捕获 TypeError 后重跑整个用户事务的方式兼容。
                try:
                    params = inspect.signature(runner_entry).parameters.values()
                    accepts_preview = any(
                        item.name == "_on_prepared"
                        or item.kind is inspect.Parameter.VAR_KEYWORD
                        for item in params)
                except (TypeError, ValueError):
                    accepts_preview = False
                if accepts_preview:
                    kwargs["_on_prepared"] = prepared
            draft = payload.get("_on_draft")
            # 草稿和已结算展示快照一样，必须由 Runner 主动声明并显式接受回调；
            # 不能为了兼容测试桩重跑完整用户事务。
            if callable(draft) and getattr(self.runner, "stream_draft_supported", False):
                try:
                    params = inspect.signature(runner_entry).parameters.values()
                    accepts_draft = any(
                        item.name == "_on_draft"
                        or item.kind is inspect.Parameter.VAR_KEYWORD
                        for item in params)
                except (TypeError, ValueError):
                    accepts_draft = False
                if accepts_draft:
                    kwargs["_on_draft"] = draft
            # 可选提交闸保持对旧 Runner/测试桩的签名兼容。正式 Runner 会在
            # responder 返回后、任何 message/state/output receipt 前复核它。
            try:
                params = inspect.signature(runner_entry).parameters.values()
                names = {item.name for item in params}
                accepts_kwargs = any(item.kind is inspect.Parameter.VAR_KEYWORD for item in params)
                accepts_gate = accepts_kwargs or "_should_continue" in names
                accepts_begin = accepts_kwargs or "_begin_commit" in names
                accepts_ready_clock = accepts_kwargs or "_ready_clock" in names
            except (TypeError, ValueError):
                accepts_gate = accepts_begin = accepts_ready_clock = False
            if accepts_gate:
                gate = payload.get("_should_continue")
                if callable(gate):
                    kwargs["_should_continue"] = gate
            if accepts_begin:
                begin = payload.get("_begin_commit")
                if callable(begin):
                    kwargs["_begin_commit"] = begin
            if accepts_ready_clock:
                # t_ready 必须在 responder 真正定稿后取样；用户输入的逻辑接收
                # 时刻只属于 t_received。这个只读回调不改变事件顺序或任何数学量。
                # 冻结测试/离线重放会传入远离宿主墙钟的逻辑时间，不能把真实
                # 2026 时间灌回它的可复现轨迹。桌面 live/demo 的接收时钟则与
                # 运行时钟同域，允许一个小的 worker 调度窗口。
                submitted_now = payload.get("now_ms")
                current_now = self._runtime_now_ms()
                try:
                    same_runtime_clock = abs(int(submitted_now) - current_now) <= 60_000
                except (TypeError, ValueError):
                    same_runtime_clock = False
                if same_runtime_clock:
                    kwargs["_ready_clock"] = self._runtime_now_ms
            # R4：异步桌面路径在 responder 草稿之后停在 Runner generator 的第二
            # 暂停点。这样网络 worker 不再写 F16/output/state；Actor 收到完成事件
            # 后才恢复 generator，成为这部分事务的唯一 writer。旧 Runner/同步
            # 单测没有三阶段 API 时保留原入口。
            # 若宿主/测试显式包裹了 ``on_user_message``，其包装器本身就是既有
            # 事务契约（例如 provider adapter、故障注入或审计 hook）。不能为
            # 了内部拆分绕过它；仅未被替换的正式 Runner 使用三阶段 API。
            original_entry = getattr(type(self.runner), entry_name, None)
            bound_entry = getattr(runner_entry,
                                  "__func__", None)
            staged_api = (defer_user_commit and bound_entry is original_entry
                          and callable(runner_prepare)
                          and callable(getattr(self.runner, "generate_prepared_user_message", None))
                          and callable(getattr(self.runner, "complete_prepared_user_message", None)))
            if staged_api:
                prepared = runner_prepare(
                    received_event if received_event is not None else str(payload.get("text", "")),
                    now_ms=payload.get("now_ms"),
                    delay_ms=payload.get("delay_ms"), **kwargs)
                if user_stage == "prepare":
                    return {"__actor_prepared_turn__": prepared}
                if isinstance(prepared, dict) and not prepared.get("complete"):
                    prepared = self.runner.generate_prepared_user_message(prepared)
                    return {"__actor_prepared_turn__": prepared}
                return prepared.get("result") if isinstance(prepared, dict) else prepared
            return runner_entry(
                received_event if received_event is not None else str(payload.get("text", "")),
                now_ms=payload.get("now_ms"),
                delay_ms=payload.get("delay_ms"), **kwargs)
        if kind == "catch_up":
            if self._formal_runtime_active():
                # W6 will install the only owner-bound observation driver.
                # Until then startup/poll must remain zero-write and cannot
                # borrow the legacy catch-up clock or autonomous writer.
                return {
                    "sent": False,
                    "messages": [],
                    "reason": "formal_runtime_pending_w6",
                }
            # 非主动的在线数学边界只结算状态再重建计划，不能
            # 被误当作“用户离线后重新登录”的历史回放。保留旧桩兼容：没有该窄
            # 接口的测试 Runner 继续走 catch_up。
            if payload.get("_boundary_settle"):
                settle = getattr(self.runner, "settle_wall_clock", None)
                if callable(settle):
                    return settle(payload.get("now_ms"))
            # 只有 GUI 首次加载会显式请求这个窄模式：离线时的多个候选开口并发生成，
            # 但 Runner 仍按逻辑时间顺序提交。正常 tick、在线互动与演示推进均走原
            # catch_up 默认语义，绝不削弱它们的前文／自我表达连续性。
            context = None
            if self._catch_up_accepts_audit_context():
                context = {
                    "origin": ("startup" if payload.get("startup_stream") else
                               "timer" if payload.get("_tick") else "event"),
                    "clock_version": payload.get("clock_version"),
                }
            if payload.get("startup_stream"):
                kwargs = {"now_ms": payload.get("now_ms"), "parallel": True,
                          "on_message": on_replay, "_startup_parallel": True,
                          "_should_continue": payload.get("_should_continue")}
                if context is not None:
                    kwargs["_audit_context"] = context
                return self.runner.catch_up(**kwargs)
            cancel_gate = payload.get("_should_continue")
            if callable(cancel_gate):
                kwargs = {"now_ms": payload.get("now_ms"), "parallel": True,
                          "_should_continue": cancel_gate}
                if context is not None:
                    kwargs["_audit_context"] = context
                return self.runner.catch_up(**kwargs)
            kwargs = {"now_ms": payload.get("now_ms"), "parallel": True}
            if context is not None:
                kwargs["_audit_context"] = context
            return self.runner.catch_up(**kwargs)
        if kind == "release_pending":
            return self.runner.release_pending(payload.get("now_ms"))
        if kind == "opening_due":
            dispatch = getattr(self.runner, "dispatch_opening_action", None)
            if callable(dispatch):
                return dispatch(
                    payload.get("opening_receipt") or {},
                    now_ms=payload.get("now_ms"),
                    clock_version=payload.get("clock_version"),
                    opening_edge=payload.get("opening_edge") or {},
                    opening_candidate=payload.get("opening_candidate") or {},
                    audit_source="actor_tick",
                    _should_continue=payload.get("_should_continue"),
                )
            return {"sent": False, "messages": [],
                    "reason": {"rule": "runner_missing_opening_dispatch"}}
        extension = self._command_extension_by_kind.get(kind)
        if extension is not None:
            return extension.execute(self.runner, kind, payload)
        if kind == "advance_demo_clock":
            return self.runner.advance_demo_clock(payload.get("delta_ms"),
                                                  host_now_ms=payload.get("host_now_ms"))
        raise ValueError(f"未知工作事件: {kind}")

    def _consume_work(self, kind, payload, result, *, task_id=None):
        """事务提交后的唯一副作用点：输出、事件日志和后续事件。"""
        if kind in {
            FORMAL_PRODUCT_RECIPIENT_INGRESS_KIND,
            FORMAL_PRODUCT_OBSERVATION_WAKE_KIND,
            FORMAL_PRODUCT_AUTONOMOUS_ACTION_KIND,
        }:
            # The Runner transaction already persisted its complete receipt.
            # Formal ingress/wake is not a reply, draft, presentation event,
            # legacy opening, or UI scheduling instruction.
            if kind == FORMAL_PRODUCT_OBSERVATION_WAKE_KIND:
                work = payload.get("work")
                tick_event_id = getattr(work, "tick_event_id", None)
                if tick_event_id:
                    self._formal_observation_rearmed_tick_ids.discard(
                        tick_event_id)
            committed_plan_change = bool(
                kind == FORMAL_PRODUCT_RECIPIENT_INGRESS_KIND
                and isinstance(result, dict)
                and result.get("observation_plan") is not None
            ) or bool(
                kind == FORMAL_PRODUCT_OBSERVATION_WAKE_KIND
                and isinstance(result, dict)
                and result.get("tick") is not None
            ) or bool(
                kind == FORMAL_PRODUCT_AUTONOMOUS_ACTION_KIND
                and getattr(result, "observation_plan", None) is not None
            )
            if committed_plan_change:
                self._notify_formal_observation_plan_changed()
            if (
                kind == FORMAL_PRODUCT_OBSERVATION_WAKE_KIND
                and isinstance(result, dict)
                and result.get("tick") is not None
            ):
                tick = result["tick"]
                grant = getattr(tick, "committed_grant", None)
                if (
                    grant is not None
                    and grant.authorization is not None
                    and grant.assessment.eligible
                ):
                    from ops.runtime_product_autonomous_work import (
                        FormalProductAutonomousActionWork,
                    )
                    self.post_formal_product_autonomous_action(
                        FormalProductAutonomousActionWork(tick), priority=0)
            return
        if kind in {"user_message", FORMAL_REQUIRED_REPLY_KIND}:
            res = result or {}
            clock_plan = res.get("clock_plan")
            effective_clock_plan = clock_plan
            # 延迟主回复只是一份已经持久化的 pending output；F16 决定其目标
            # 时间，但只有 Actor 的 deadline 能实际 release。这里绝不要求 UI
            # 计时或把结果再 post 回来。
            delayed_release = (int(res.get("delay_sec") or 0) > 0
                               and res.get("send_at") is not None)
            if delayed_release:
                self._queue_pending_release(self.runner, res, clock_plan, payload)
                # 后一条 relation F03 只更新它自己的 state/pending；若已有队首，
                # Actor 的单一 deadline 继续读取队首已持久化的 authority plan。
                if self._active_pending_candidate is not None:
                    effective_clock_plan = self._load_clock_plan()
            else:
                # external delivery 的即时 F16 不能清掉先前 relation 已资格化的
                # delayed pending；只有本地 Enter 已在 post() 的抢占边界整体取消。
                if payload.get("received_event") is None:
                    self._cancel_all_pending_release_candidates(
                        now_ms=payload.get("now_ms"), reason="local_direct_reply")
            self._set_clock_plan(effective_clock_plan)
            if res.get("telemetry"):
                self._emit_telemetry(res["telemetry"])
            # 延迟主回复尚未经过 F16 时，不能把它画成 output receipt 段的起点。
            # 否则展示会把“正在等 direct reply”误当成已提交输出，并在其后把补偿
            # 时间伪画成 dynamic tick。
            if not delayed_release:
                self._emit_opening_transition("reply_receipt", ts=res.get("send_at"))
            self._emit("reply_result", res)
            # 零延迟也必须由 Actor 发出正式 release 事实。过去让 UI 用
            # ``after(0)`` 再投递 ``release_pending``，虽然不改公式，却让展示层
            # 成为了主回复可见／释放的第二入口。
            if not delayed_release:
                self._emit("released", dict(res))
                # 主回复已被 Actor 正式 release；现在才允许派生候选进入其队列。
                # 这是消息因果顺序，不是“等 UI 播放完”。
                self._arm_opening(clock_plan, res)
            self._log("user_message", {"text": str(payload.get("text", ""))[:40]})
            for extension in self._command_extensions:
                follow_up = getattr(extension, "follow_up_commands", None)
                if not callable(follow_up):
                    continue
                for follow_kind, follow_payload, follow_priority in follow_up(
                    parent_kind=kind, result=res,
                ):
                    self.post(
                        follow_kind,
                        follow_payload,
                        priority=follow_priority,
                    )
        elif kind == "catch_up":
            res = result or {"sent": False, "messages": []}
            # 启动的唯一追平事务已经结算；现在才允许旧/新计划重新挂到进程 timer。
            if payload.get("startup_stream"):
                self._startup_clock_hold = False
            self._set_clock_plan(res.get("clock_plan"))
            # 异步启动流已在每条正式提交后投递 `_STREAM_REPLAY`；这里只保留默认
            # 路径的批量发射，避免重复气泡。自主在线输出绝不可从 catch_up 旁路。
            if not (payload.get("startup_stream") and self.async_tasks):
                for message in res.get("messages") or []:
                    self._emit("replay_msg", message)
            self._emit("catch_up_done", res)
            if res.get("sent") and not res.get("interrupted_by_user"):
                self._log("catch_up", {"n": len(res.get("messages", []))})
            if payload.get("_tick"):
                self._after_tick()
        elif kind == "release_pending":
            self._clear_pending_release()
            release_payload = result
            meta = None
            clock_plan = None
            if result:
                meta = self._pending_release_meta
                self._pending_release_meta = None
                pending_opening = self._pending_release_opening
                self._pending_release_opening = None
                clock_plan = self._load_clock_plan()
                self._set_clock_plan(clock_plan)
                # 测试桩／旧嵌入式 Runner 可能没有上一阶段的元数据；保持其
                # ``released -> str`` 兼容。正式 Runner 一律保留原 F03/F16
                # receipt，UI 因而不必向 Actor 反查或自行 release。
                if isinstance(meta, dict):
                    release_payload = self._released_f16_projection(
                        result, pending_meta=meta)
            self._emit("released", release_payload)
            # release 是计划中的正式输出边界。迟到量不能在这里被伪造成一次
            # dynamic tick：只有下一次已经重臂的正式观察才能执行 F08→F15。
            # 让下一 tick 从 F16 action 的根状态一次性结算 Delta_t，既不会丢时间，
            # 也不会把必答回复错误地变成自主开口的采样入口。
            if result:
                # output P3 是下一观察密度的唯一读取源；此前基于 input/P3 的 deadline
                # 在 F16 边界后失效，不能以“只提前、不推迟”的公平性规则继续遗留。
                self._next_tick_monotonic = None
                self._arm_tick_deadline()
                self._emit_opening_transition(
                    "reply_receipt", ts=(meta or {}).get("send_at") or payload.get("now_ms"))
            if result and pending_opening is not None:
                _planned_clock, carrier = pending_opening
                # 用 release 后的正式 plan 重新取候选；若 Runner 已更新版本，旧计划
                # 不得穿透。UI 不参与这个顺序。
                self._arm_opening(clock_plan, carrier)
            if result:
                self._log("release_pending", {})
                self._finish_active_pending_candidate(
                    now_ms=(meta or {}).get("send_at") or payload.get("now_ms"), released=True)
            elif self._active_pending_candidate is not None:
                # release 无提交并不等于候选已经消失；保持同一 relation 的 pending
                # 作为队首，留下明确 deferred receipt，不能悄悄换到后项。
                candidate = self._active_pending_candidate
                self._active_pending_candidate = None
                self._pending_release_queue.insert(0, candidate)
                self._append_pending_candidate_receipt(
                    candidate, "deferred", now_ms=payload.get("now_ms"),
                    reason="release_not_committed")
                self._activate_next_pending_release()
        elif kind == "opening_due":
            if isinstance(result, dict):
                # Writer 的两种历史语境可不同，但它们都必须经同一 receipt 校验入口
                # 返回标准化提交事实；Actor 只负责把已提交事实投影到 UI。
                messages = list(result.get("messages") or ())
                text = str(result.get("reply") or "")
                if not messages and text:
                    messages = [{"reply": text, "source": result.get("source", "opening")}]
                if messages:
                    clock_plan = self._load_clock_plan()
                    self._set_clock_plan(clock_plan)
                    for message in messages:
                        source = str(message.get("source") or "opening")
                        self._emit("npc_msg", self._npc_message_payload(
                            source, message, payload.get("presentation_receipt")))
                    self._arm_opening(clock_plan, messages[-1], defer_until_release=True)
                    self._emit_opening_transition("opening_receipt")
                    self._log("opening_due", {"n": len(messages)})
                elif task_id is not None:
                    self._emit("autonomous_cancelled", {
                        "task_id": task_id, "kind": "opening_due", "reason": "no_output"})
                return
        elif kind in self._command_extension_by_kind:
            self._command_extension_by_kind[kind].consume_result(
                kind=kind,
                result=result,
                emit=self._emit,
                log=self._log,
            )
        elif kind == "advance_demo_clock":
            res = result or {"sent": False, "messages": []}
            self._set_clock_plan(res.get("clock_plan"))
            for message in res.get("messages") or []:
                self._emit("replay_msg", message)
            if res.get("sent"):
                self._log("advance_demo_clock", {"n": len(res.get("messages", []))})
            self._emit("demo_clock_advanced", res.get("demo_clock") or {})

    def _start_work(self, kind, payload):
        if self._inflight is not None:
            raise RuntimeError("Actor 已有未完成事务")
        if self._is_stale_unsent_npc(kind, payload):
            return
        self._task_seq += 1
        task_id = self._task_seq
        cancel_event = threading.Event()
        # 能力标记还不够：测试/宿主可能把 on_user_message 包一层而丢失
        # _should_continue。此时只能维持既有串行提交，不能假装已经可抢占。
        main_reply_preemption = bool(
            kind == "user_message" and self._main_reply_preemption_available())
        self._inflight = {"id": task_id, "kind": kind, "payload": dict(payload or {}),
                          "startup_stream": bool(payload.get("startup_stream")),
                          "interaction_epoch": payload.get("_interaction_epoch"),
                          "cancel_event": cancel_event,
                          "main_reply_preemption": main_reply_preemption,
                          "main_commit_lock": (threading.Lock() if main_reply_preemption else None),
                          "main_commit_started": False}
        if kind == "opening_due":
            # 这是无正文的调度事实：自主输出已开始生成，但尚未定稿、更未发送。
            # UI 仅据此显示等待态，绝不能把它当消息或记忆写入。
            self._emit("autonomous_started", {
                "task_id": task_id, "kind": kind,
                "presentation_receipt": dict(payload.get("presentation_receipt") or {}),
            })
            # 计划已经由上一轮正式 wall-clock / opening 结算得出；这里记录
            # 它真正进入生成事务的观察点，而不是凭 UI 预测一处“未来交叉”。
            self._emit_opening_transition(
                "opening_plan_reply", ts=payload.get("now_ms"),
                opening=payload.get("opening_receipt"))

        def worker():
            try:
                on_replay = None
                on_prepared = None
                on_draft = None
                if kind == "catch_up" and payload.get("startup_stream"):
                    def on_replay(message):
                        # Runner 回调发生在 worker；只回投 Actor inbox，绝不直接触碰 UI。
                        if not cancel_event.is_set():
                            self._enqueue(_STREAM_REPLAY, {
                                "id": task_id, "message": dict(message or {}),
                                # 回调只在 Runner 已正式提交一条 replay 后触发；即使
                                # 用户随后到达，它也不能被误当作“未发生的候选”抹掉。
                                "committed_before_cancel": True})
                if kind == "user_message" and main_reply_preemption:
                    def on_prepared(snapshot):
                        # Runner worker 只能回投一份无正文展示快照；Actor 仍在
                        # 自己的串行循环里确认它属于当前事务后才通知 UI。
                        # ``measurement_committed`` 与 ``clock_plan`` 是 Actor 的
                        # 后端调度契约，不能藏在 presentation 里让 UI 或 Actor 猜。
                        # presentation 则保持无正文、只读，供展示层消费。
                        prepared = dict(snapshot or {})
                        self._enqueue(_TURN_PREPARED, {
                            "id": task_id, "kind": kind,
                            "ts": prepared.get("ts"),
                            "measurement_committed": bool(
                                prepared.get("measurement_committed")),
                            "clock_plan": dict(prepared.get("clock_plan") or {}),
                            "presentation": dict(prepared.get("presentation") or {}),
                        })
                    def on_draft(snapshot):
                        # Raw SSE / 未闭合 JSON 绝不能越过 Runner 的稳定句段提取器。
                        # 此处只搬运最新可撤销展示文本；Actor loop 仍会确认 task id。
                        if not cancel_event.is_set():
                            self._enqueue(_STREAM_DRAFT, {
                                "id": task_id, "kind": kind,
                                "draft": dict(snapshot or {}),
                            })
                work_payload = dict(payload)
                if on_prepared is not None:
                    work_payload["_on_prepared"] = on_prepared
                if on_draft is not None:
                    work_payload["_on_draft"] = on_draft
                if kind == "user_message":
                    # 主回复与自主输出一样，只将“仍可提交吗”交给 Runner；Runner
                    # 不反向控制 Actor 队列，也不触碰 UI。
                    work_payload["_should_continue"] = lambda: not cancel_event.is_set()
                    if main_reply_preemption:
                        def begin_commit():
                            # ``post(user_message)`` 与这里对同一锁竞逐，给这条
                            # 用户线一个明确的顺序点，而不是让持久化前的极窄空档
                            # 决定谁覆盖谁。
                            active = self._inflight
                            if active is None or active.get("id") != task_id:
                                active = self._user_drafts.get(task_id)
                            if active is None or active.get("id") != task_id:
                                return False
                            lock = active.get("main_commit_lock")
                            if lock is None:
                                return not cancel_event.is_set()
                            with lock:
                                if cancel_event.is_set():
                                    return False
                                active["main_commit_started"] = True
                                return True
                        work_payload["_begin_commit"] = begin_commit
                if kind in _AUTONOMOUS_KINDS:
                    task_epoch = payload.get("_interaction_epoch")
                    gate = lambda: (  # noqa: E731 -- 短生命周期事务闭包
                        not cancel_event.is_set()
                        and (task_epoch is None
                             or int(task_epoch) == self._current_interaction_epoch()))
                    # 自主输出只经 opening_due；catch_up 还须兼容只实现公共
                    # now/parallel 合同的宿主/测试 Runner。
                    if kind != "catch_up" or self._catch_up_accepts_cancel_gate():
                        work_payload["_should_continue"] = gate
                result = self._run_work(
                    kind, work_payload, on_replay=on_replay,
                    defer_user_commit=(kind == "user_message" and self.async_tasks),
                    user_stage=("prepare" if kind == "user_message" and self.async_tasks
                                else None))
            except Exception as exc:  # noqa: BLE001 — 错误也必须回队列由 Actor 结算
                self._enqueue(_FAILED, {"id": task_id, "kind": kind,
                                        "payload": payload,
                                        "error": str(exc)[:160],
                                        "error_type": type(exc).__name__,
                                        "retryable_io": isinstance(exc, OSError)})
                return
            self._enqueue(_DONE, {"id": task_id, "kind": kind,
                                  "payload": payload, "result": result})

        threading.Thread(target=worker, daemon=True).start()

    def _start_user_draft(self, active, prepared):
        """启动只读 responder worker；它没有任何 state/output 写权限。"""
        task_id = active["id"]
        self._user_drafts[task_id] = active

        def worker():
            try:
                ticket = self.runner.generate_prepared_user_message(prepared)
            except Exception as exc:  # noqa: BLE001
                self._enqueue(_USER_DRAFT_FAILED, {
                    "id": task_id, "payload": active.get("payload") or {},
                    "error": str(exc)[:160]})
                return
            self._enqueue(_USER_DRAFT_DONE, {
                "id": task_id, "payload": active.get("payload") or {},
                "prepared": ticket})

        threading.Thread(target=worker, daemon=True).start()

    def _finish_user_turn(self, active, payload, result):
        """Actor 单写者下的用户输出收口：合并 tick、取消或正式提交。"""
        # R3：此刻 draft 已不再拥有任何写权限；先把慢网络期间的只读墙钟
        # 投影正式结算，再让结果使用合并后的 clock plan。
        # 若新 Enter 已经进入新 epoch，旧草稿即使刚刚返回也只能留下其取消
        # receipt；不能再用旧快照结算墙钟，否则会反向作用到较新的 F03。
        same_epoch = (active.get("interaction_epoch") is None
                      or int(active.get("interaction_epoch")) == self._current_interaction_epoch())
        if active.get("measurement_committed") and same_epoch:
            observed_now = self._runtime_now_ms()
            self._observe_tick(observed_now)
            if isinstance(result, dict):
                result = dict(result)
                # ``ready`` 的正式含义是 Actor 单写者看见定稿，而不是网络线程
                # 先一步返回的瞬间。把未发送 F16 时间线压到已结算锚之后，防止
                # 下一 tick 把它误判成“倒插旧消息”；不重算人格、门限或词元。
                if result.get("response_deferred"):
                    settle_ready = getattr(self.runner, "settle_pending_reply_ready", None)
                    if callable(settle_ready):
                        settled = settle_ready(observed_now, turn_id=result.get("turn_id"))
                        if isinstance(settled, dict):
                            result["send_at"] = int(settled["t_sent"])
                result["clock_plan"] = copy.deepcopy(self._clock_plan)
        if (active.get("main_reply_preemption")
                and (active.get("cancel_event", threading.Event()).is_set()
                     or bool((result or {}).get("aborted")))):
            self._log("user_message_cancelled", {"reason": "user_submit_preempt"})
            self._emit("reply_cancelled", {
                "task_id": active.get("id"), "reason": "user_submit_preempt"})
            return
        self._consume_work("user_message", payload, result)

    def _complete_user_draft(self, payload):
        """draft worker 返回后，仅由 Actor 恢复 generator 并提交/丢弃。"""
        task_id = payload.get("id")
        # ``complete_prepared_user_message`` 内部的 _begin_commit 会从同一
        # draft 表读取提交闸，因此直到它返回前都不能移除此条目。
        active = self._user_drafts.get(task_id)
        if active is None:
            return
        try:
            result = self.runner.complete_prepared_user_message(
                payload.get("prepared") or {})
        except Exception as exc:  # noqa: BLE001
            self._user_drafts.pop(task_id, None)
            # F03 已提交，失败也要在单写者边界合并墙钟；随后只通知回复失败。
            if active.get("measurement_committed"):
                self._observe_tick()
            self._log("error", {"kind": "user_message", "error": str(exc)[:160]})
            self._emit("reply_failed", {"error": str(exc)[:160]})
            return
        self._user_drafts.pop(task_id, None)
        self._finish_user_turn(active, payload.get("payload") or {}, result)

    def _fail_user_draft(self, payload):
        active = self._user_drafts.pop(payload.get("id"), None)
        if active is None:
            return
        same_epoch = (active.get("interaction_epoch") is None
                      or int(active.get("interaction_epoch")) == self._current_interaction_epoch())
        if active.get("measurement_committed") and same_epoch:
            self._observe_tick()
        if active.get("cancel_event", threading.Event()).is_set():
            self._log("user_message_cancelled", {"reason": "user_submit_preempt"})
            self._emit("reply_cancelled", {
                "task_id": active.get("id"), "reason": "user_submit_preempt"})
            return
        self._log("error", {"kind": "user_message", "error": payload.get("error", "")})
        self._emit("reply_failed", {"error": payload.get("error", "回复生成失败")})

    def _complete_task(self, payload):
        active = self._inflight
        if active is None or payload.get("id") != active.get("id"):
            return  # 已停止/过期 worker 的迟到结果，绝不二次提交
        result = payload.get("result")
        # 只有 Actor 队列线程可以越过 R4 的 draft → output 暂停点。此时模型
        # 已经返回，但 F16、output receipt、pending reply、F06 和下一计划均未
        # 发生；因此新 Enter 若先设置 cancel gate，会在 generator 的既有提交闸
        # 被识别为 aborted，而不是让网络线程偷偷落盘旧 state。
        if (active.get("kind") == "user_message" and isinstance(result, dict)
                and "__actor_prepared_turn__" in result):
            prepared = result["__actor_prepared_turn__"]
            # R4-2：prepare 已写 F03 后释放输入 writer；draft 在独立只读 worker
            # 中运行。新的 Enter 此后可取消它并立即开始自己的 prepare。
            if isinstance(prepared, dict) and not prepared.get("complete"):
                self._inflight = None
                self._start_user_draft(active, prepared)
                return
            try:
                result = self.runner.complete_prepared_user_message(
                    prepared)
            except Exception as exc:  # noqa: BLE001 -- 仍由同一 Actor 失败出口结算
                self._fail_task({"id": active.get("id"), "kind": "user_message",
                                 "payload": active.get("payload") or {},
                                 "error": str(exc)[:160]})
                return
        self._inflight = None
        # R3 收口：慢 responder 在飞期间只允许 F08/F14/F15 的只读投影，避免
        # worker 持有的冻结快照与 tick 并发写入。worker 结束（包括被新 Enter
        # 抢占后正常返回）时，Actor 已重新成为唯一 writer，必须立刻把这段真实
        # 墙钟正式结算一次；否则 UI 看见过的时间趋势从未进入状态/下一计划。
        #
        # 这里刻意不把投影逐点回放成第二套状态机：``settle_wall_clock`` 以当前
        # 已提交状态和当前时刻作正式 F08 结算，随后重编 F14/F15。若 worker
        # 产出的旧 plan 仍被传给 _consume_work，就会覆盖这个新 plan，因此成功
        # 结果只替换其计划快照，不重写任何已提交的回复/receipt。
        if active.get("kind") == "user_message":
            self._finish_user_turn(active, payload.get("payload") or {}, result)
            return
        if (active.get("kind") in _UNSENT_NPC_KINDS
                and (active.get("cancel_event", threading.Event()).is_set()
                     or self._is_stale_unsent_npc(
                         active.get("kind"),
                         {"_interaction_epoch": active.get("interaction_epoch")}))):
            self._log("autonomous_output_cancelled", {"kind": active.get("kind")})
            self._emit("autonomous_cancelled", {
                "task_id": active.get("id"), "kind": active.get("kind")})
            return
        self._consume_work(payload["kind"], payload.get("payload") or {}, result,
                           task_id=active.get("id"))

    def _fail_task(self, payload):
        active = self._inflight
        if active is None or payload.get("id") != active.get("id"):
            return
        self._inflight = None
        kind = payload.get("kind", "unknown")
        # 失败也不能把已经提交的 F03 输入留在“只读投影”状态。没有输出可提交，
        # 因而此处正是 Actor 安全重获单写者资格后的正式墙钟合并点。
        if (kind == "user_message" and active.get("measurement_committed")):
            self._observe_tick()
        self._log("error", {"kind": kind, "error": payload.get("error", "")})
        if kind in {
            FORMAL_PRODUCT_RECIPIENT_INGRESS_KIND,
            FORMAL_PRODUCT_OBSERVATION_WAKE_KIND,
            FORMAL_PRODUCT_AUTONOMOUS_ACTION_KIND,
        }:
            if (
                kind == FORMAL_PRODUCT_OBSERVATION_WAKE_KIND
                and payload.get("retryable_io") is True
            ):
                work = (active.get("payload") or {}).get("work")
                tick_event_id = getattr(work, "tick_event_id", None)
                if (
                    tick_event_id
                    and tick_event_id
                    not in self._formal_observation_rearmed_tick_ids
                ):
                    self._formal_observation_rearmed_tick_ids.add(
                        tick_event_id)
                    # Body-free notification only. The unique driver re-reads
                    # the still-authoritative active plan and derives its
                    # schedule; this callback carries no time/action data.
                    self._notify_formal_observation_plan_changed()
            # Formal owner work has no presentation contract.  Its durable
            # error journal is observable by the host; projecting task_failed
            # into the UI would make presentation a second control surface.
            return
        if kind in {"user_message", FORMAL_REQUIRED_REPLY_KIND}:
            self._emit("reply_failed", {"error": payload.get("error", "回复生成失败")})
        elif kind == "catch_up":
            store = getattr(self.runner, "store", None)
            append = getattr(store, "append_event", None)
            if callable(append):
                try:
                    active_payload = active.get("payload") or {}
                    append({"schema_version": 1, "kind": "wall_clock_catchup_failed",
                            "ts": self._runtime_now_ms(),
                            "detail": {
                                "origin": ("startup" if active.get("startup_stream") else
                                           "timer" if active_payload.get("_tick") else "event"),
                                "clock_version": active_payload.get("clock_version"),
                                "error_kind": "worker_exception",
                            }})
                except Exception:
                    pass
            if active.get("startup_stream"):
                self._startup_clock_hold = False
                self._rebuild_clock_plan()
            # GUI 启动需要一个终止信号，失败不能让加载页永久等待。
            self._emit("catch_up_done", {"sent": False, "messages": [], "failed": True})
        elif kind == "opening_due":
            store = getattr(self.runner, "store", None)
            append = getattr(store, "append_event", None)
            if callable(append):
                try:
                    append({"kind": "opening_dispatch_failed", "ts": self._runtime_now_ms(),
                            "detail": {"error_kind": "worker_exception"}})
                except Exception:
                    pass
            self._emit("autonomous_cancelled", {
                "task_id": active.get("id"), "kind": kind, "failed": True})
        else:
            self._emit("task_failed", {"kind": kind, "error": payload.get("error", "")})

    def _handle(self, kind, payload):
        """同步测试直接结算；运行态则只发起一个受序列保护的 worker 事务。"""
        if kind not in self._work_kinds:
            raise ValueError(f"未知事件类型: {kind}")
        if (self._is_stale_unsent_npc(kind, payload)
                or self._is_stale_user_submission(kind, payload)):
            return
        if self.async_tasks:
            self._start_work(kind, payload)
            return
        result = self._run_work(kind, payload)
        self._consume_work(kind, payload, result)

    def _after_tick(self):
        """只在一项 Actor 事务提交后重启观察，保证不与 worker 并发写 Runner。"""
        self._observe_tick()

    def _record_wall_clock_settle_failure(self, now_ms, exc):
        """留存 F08 失败的最小 receipt；不把失败伪装成一次普通 tick。"""
        store = getattr(self.runner, "store", None)
        append = getattr(store, "append_event", None)
        if callable(append):
            try:
                append({"schema_version": 1, "kind": "wall_clock_settle_failed",
                        "ts": int(now_ms),
                        "detail": {"error_type": type(exc).__name__}})
            except Exception:
                pass
        self._emit_telemetry({"kind": "wall_clock_tick_failed", "ts": int(now_ms),
                              "error_type": type(exc).__name__})

    def _observe_tick(self, now_ms=None, *, readonly_inflight=False):
        """动态 tick 的正式墙钟观察；不直接触发 LLM。

        观察频率由 C/X/e/Δt 的连续根向量投影决定。每个 tick 先走 Runner 已有的
        ``settle_wall_clock``：F08 推进真实根状态，随后以当下
        ``I15-Theta15`` 重建计划。这里绝不拼 prompt、也不把 tick 当消息资格；若
        margin 已过线，只会得到一份立即到期的版本化计划，仍由同一 Actor 的
        ``opening_due → dispatch_opening_action`` 事务消费。
        """
        if self._formal_runtime_active():
            # Formal observation is an explicit TemporalDomain transaction;
            # never fall through to legacy settle_wall_clock.
            return False
        self._last_observe_monotonic = time.monotonic()
        now = self._runtime_now_ms() if now_ms is None else int(now_ms)
        if readonly_inflight:
            # 主回复 worker 仍持有冻结 state；这里绝不调用 settle、rhythm、outcome
            # observer 或写入 Store。它们都会与 worker 的最终提交构成双 writer。
            # 只读投影仍由同一 F08/F14/F15 纯函数得出，供 UI 连续展示时间趋势；
            # 正式计划与消息资格仍等待主事务收口后的 Actor tick。
            preview = None
            try:
                project = getattr(self.runner, "preview_wall_clock", None)
                if callable(project):
                    preview = project(now)
            except Exception:
                preview = None
            self._emit_telemetry({
                "kind": "wall_clock_tick",
                "ts": int(now),
                "state": copy.deepcopy((preview or {}).get("state")
                                       or getattr(self.runner, "state", {}) or {}),
                "clock_plan": copy.deepcopy((preview or {}).get("clock_plan")),
                "tick": self.tick_status_snapshot(),
                "projection": str((preview or {}).get("projection")
                                  or "readonly_inflight"),
            })
            return

        settled = None
        try:
            settle = getattr(self.runner, "settle_wall_clock", None)
            if callable(settle):
                settled = settle(now)
                if isinstance(settled, dict):
                    self._set_clock_plan(settled.get("clock_plan"))
        except Exception as exc:
            # 观察失败不能阻断用户队列，但也不能被伪装成“本 tick 正常未越线”。
            # 只记脱敏失败 receipt；不继续 outcome、P3、telemetry readout 或发送。
            self._record_wall_clock_settle_failure(now, exc)
            return False
        try:
            self.runner.observe_user_line(now)
        except Exception:
            pass
        try:
            self.runner.observe_action_outcomes(now)
        except Exception:
            pass
        # 正式 tick 的根量读数单独留存。它不写 X、不影响 F15，只让之后的测试和
        # 演示能从 interval → C/X/e 项逐轴回溯，而不是只看到一个 UI 倒计时。
        tick_receipt = self._tick_readout()
        try:
            store = getattr(self.runner, "store", None)
            append = getattr(store, "append_event", None)
            if callable(append):
                record = {"kind": "tick_observation_root_vector", "ts": int(now),
                          "detail": dict(tick_receipt)}
                if isinstance(settled, dict) and settled.get("tick_id"):
                    # Exact Actor observation proof for the already-settled Runner
                    # tick. No formula, clock, candidate or send decision is rerun.
                    record.update({
                        "tick_id": settled["tick_id"],
                        "participant_id": settled.get("participant_id"),
                        "plan_version": (settled.get("clock_plan") or {}).get("version"),
                        "params_sha": settled.get("params_sha"),
                    })
                append(record)
        except Exception:
            pass
        self._emit_opening_transition("tick", ts=now)
        # 动态 tick 已实际发生时，额外发送一条只读观测给可选展示消费者。
        # 它携带本 tick 的真实时间和结算后快照，不写入 store、不参与下一计划。
        self._emit_telemetry({
            "kind": "wall_clock_tick",
            "ts": int(now),
            "state": copy.deepcopy(getattr(self.runner, "state", {}) or {}),
            "clock_plan": copy.deepcopy(self._clock_plan) if self._clock_plan else None,
            "tick": {**self.tick_status_snapshot(), "readout": tick_receipt},
        })
        return True

    def _on_tick(self):
        if self._inflight is not None:
            active = self._inflight
            if (active.get("kind") == "user_message"
                    and active.get("measurement_committed")):
                # responder 正在读取冻结 prompt。这里做同公式的只读投影来维持
                # 墙钟展示连续；正式 F08/F15 写入必须等 worker 收口，避免其旧
                # 快照覆盖 tick 的新状态，更不能并发生成第二条 NPC 输出。
                self._next_tick_monotonic = None
                self._observe_tick(readonly_inflight=True)
                self._arm_tick_deadline()
            return
        if self._has_live_user_draft():
            # R4-2 的 draft 只读，但仍持有旧 state 的 generator frame。它尚未被
            # 新 Enter 撤销时，正式 tick 不能写入新状态，否则将来 output commit
            # 会拿旧快照覆盖它；继续发同公式只读投影。已撤销 draft 不再有写权，
            # 随后的 prepare/tick 可以正常结算。
            self._next_tick_monotonic = None
            self._observe_tick(readonly_inflight=True)
            self._arm_tick_deadline()
            return
        now = self._runtime_now_ms()
        # pending 主回复是用户事件之后的正式时间边界。先按 F16 的发送点提交
        # 已准备输出，再推进事件循环晚到的 F08/F15；这样不会把消息倒插到已结算
        # 锚之前，UI 动画也不会推进真实状态。
        if (self._pending_release_due_monotonic is not None
                and time.monotonic() >= self._pending_release_due_monotonic):
            planned_at = int(self._pending_release_at_ms or now)
            late_ms = int(max(0.0, time.monotonic() - self._pending_release_due_monotonic) * 1000)
            logical_now = planned_at + late_ms
            self._clear_pending_release()
            self._rearm_tick_observation()
            # 不能先把墙钟结算到 logical_now 再回头提交 planned_at：那会违反
            # F16 的单调时间合同。release 成功后才结算这段迟到时间。
            self._handle("release_pending", {
                "now_ms": planned_at,
                "_settle_after_ms": logical_now,
                "_interaction_epoch": self._current_interaction_epoch(),
            })
            return
        plan = self._clock_plan
        if plan is not None:
            # 计划不是第二个 timer。只有本次正式动态 tick 才能读取/消费 F15
            # 候选；墙钟的 ``at_ms`` 只是候选属于哪个真实时刻的 receipt。
            if (plan.get("at_ms") is not None
                    and self._clock_due_monotonic is not None
                    and time.monotonic() >= self._clock_due_monotonic):
                # 先摘下进程内计划，避免 worker 运行期间因 timeout=0 自旋；提交后
                # catch_up 会返回同版本的刷新计划，或用户事件以新版本替换它。
                self._clock_plan = None
                # 逻辑时间轴可为虚拟时钟；迟到量只能由该 plan 的进程等待时长
                # 推得，不能拿宿主 Unix 时间减历史/演示 ts。
                late_ms = int(max(0.0, time.monotonic() - self._clock_due_monotonic) * 1000)
                self._clock_due_monotonic = None
                logical_now = int(plan["at_ms"]) + late_ms
                if self._opening_due(plan):
                    # F15 crossing 已在 ``qualified_at_ms`` 形成一条可追溯的离散
                    # 待投递事件。这里仍先把根状态推进到 F16 的实际发送时刻，但
                    # 不能拿已经自然衰减的另一帧 F15 反向否决它：那会让“越线→
                    # 等待几秒可见输出”永远被事件残留的正常衰退吞掉。新的用户
                    # Enter 会经 interaction epoch 取消这条未发送任务；输出提交后
                    # 才从新 X 开始下一次开口计算。
                    if isinstance(plan.get("opening"), dict):
                        try:
                            settle = getattr(self.runner, "settle_wall_clock", None)
                            if callable(settle):
                                settle(logical_now)
                        except Exception as exc:
                            # F08 追平失败不应把已经持久化的 crossing 伪装成取消；
                            # crossing 已由此前 P3 提交，不重算、不改资格；但失败
                            # 必须可见，不能因为随后投递而掩盖。
                            self._record_wall_clock_settle_failure(logical_now, exc)
                        # settle_wall_clock 会刷新存储里的“下一观察”；当前 crossing
                        # 已进入唯一输出槽，不能让那份临时计划在 worker 期间抢跑。
                        self._clock_plan = None
                        self._clock_due_monotonic = None
                    opening = plan.get("opening") if isinstance(plan.get("opening"), dict) else {}
                    observation_source = str(opening.get("observation_source") or "reply_receipt")
                    task = self._opening_task(plan) or {
                        "clock_version": plan.get("version"), "now_ms": logical_now}
                    task["now_ms"] = logical_now
                    task["_interaction_epoch"] = self._current_interaction_epoch()
                    task["presentation_receipt"] = (
                        task.get("presentation_receipt")
                        if observation_source == "reply_receipt"
                        else self._online_proactive_presentation_receipt(plan))
                    self._handle("opening_due", task)
                    return
                else:
                    self._handle("catch_up", {"_tick": True, "now_ms": logical_now,
                                               "clock_version": plan.get("version"),
                                               "_boundary_settle": True,
                                               "presentation_receipt": None,
                                               "_interaction_epoch": self._current_interaction_epoch()})
            else:
                # 已经取得 F15 crossing 的 F16 等待期不是新的资格观察窗口。维持
                # Actor deadline 即可；否则每个普通 tick 都会用衰减后的读数覆写
                # 已排队计划，造成面板出现交点但没有消息。
                if self._opening_due(plan):
                    self._next_tick_monotonic = None
                    self._arm_tick_deadline()
                    return
                self._next_tick_monotonic = None
                self._observe_tick()
                # 本 tick 刚发现的 crossing 属于同一次采样，不必人为多等一轮。
                refreshed = self._clock_plan
                if (isinstance(refreshed, dict) and refreshed.get("at_ms") is not None
                        and self._clock_due_monotonic is not None
                        and time.monotonic() >= self._clock_due_monotonic):
                    self._on_tick()
                    return
                self._arm_tick_deadline()
            return
        # 无当前计划不意味着角色刚刚经历了“离线缺席区间”。正式在线 tick 仍
        # 只做一次 F08/OPENING 观察，由 `_observe_tick` 结算并重建下一计划；
        # 不能为了旧 Runner/空 plan 回退到 catch_up 的历史回放 writer。
        self._next_tick_monotonic = None
        self._observe_tick(now)
        self._arm_tick_deadline()

    def _next_event(self):
        # 有在飞事务时不能反复弹出 deferred，否则会自旋并饿死完成事件。
        if self._inflight is None:
            with self._deferred_lock:
                while self._deferred:
                    event = self._deferred.popleft()
                    if not self._is_stale_unsent_npc(event[2], event[3]):
                        return event
        if self._next_tick_monotonic is None:
            self._arm_tick_deadline()
        if self._next_tick_monotonic is None:
            # 未有已提交 P3 receipt 的产品会话只等外部正式事件；不得为让线程
            # 醒来而自造 legacy tick。F16 已存在时仍由同一 Actor deadline 唤醒。
            if self._inflight is None and self._pending_release_due_monotonic is not None:
                timeout_s = max(0.0, self._pending_release_due_monotonic - time.monotonic())
                return self.inbox.get(timeout=timeout_s)
            return self.inbox.get()
        timeout_s = max(0.0, self._next_tick_monotonic - time.monotonic())
        if self._inflight is None and self._pending_release_due_monotonic is not None:
            pending_in_s = max(0.0, self._pending_release_due_monotonic - time.monotonic())
            timeout_s = min(timeout_s, pending_in_s)
        # ``_clock_due_monotonic`` 不得进入 wait timeout：自主输出只能由
        # ``_next_tick_monotonic`` 的正式观察唤醒。
        return self.inbox.get(timeout=timeout_s)

    def _loop(self):
        while not self._stop.is_set():
            try:
                _prio, _seq, kind, payload = self._next_event()
            except queue.Empty:
                if (self._inflight is None
                        or (self._inflight.get("kind") == "user_message"
                            and self._inflight.get("measurement_committed"))):
                    try:
                        self._on_tick()
                    except Exception:
                        pass
                continue
            if kind == _STOP:
                return
            if kind == _DONE:
                self._complete_task(payload)
                continue
            if kind == _FAILED:
                self._fail_task(payload)
                continue
            if kind == _USER_DRAFT_DONE:
                # prepare 是唯一 state writer。取消草稿虽然最终只会写取消 receipt，
                # 也不能和下一条输入的 F03 同时碰 Runner/Store。
                if self._inflight is not None:
                    with self._deferred_lock:
                        self._deferred.append((_prio, _seq, kind, payload))
                    continue
                self._complete_user_draft(payload)
                continue
            if kind == _USER_DRAFT_FAILED:
                if self._inflight is not None:
                    with self._deferred_lock:
                        self._deferred.append((_prio, _seq, kind, payload))
                    continue
                self._fail_user_draft(payload)
                continue
            if kind == _PRESENTATION_ACK:
                self._log("presentation_started", {
                    "action_id": payload.get("action_id"),
                    "source": payload.get("source"),
                    "ts": payload.get("ts"),
                    "stage": payload.get("stage") or "visual_started",
                })
                continue
            if kind == _STREAM_REPLAY:
                # 只接受当前启动事务的正式提交；停止／过期 worker 的迟到消息不泄漏到 UI。
                active = self._inflight or {}
                if (active.get("id") == payload.get("id")
                        and active.get("kind") == "catch_up"
                        and (payload.get("committed_before_cancel")
                             or not active.get("cancel_event", threading.Event()).is_set())):
                    self._emit("replay_msg", payload.get("message") or {})
                continue
            if kind == _TURN_PREPARED:
                self._consume_turn_prepared(payload)
                continue
            if kind == _STREAM_DRAFT:
                # 草稿只在同一条在飞用户事务仍有效时转发。新的 Enter、Actor 停止
                # 或旧 worker 的迟到回调都会在这里自然失效。
                active = self._inflight or {}
                if active.get("id") != payload.get("id"):
                    active = self._user_drafts.get(payload.get("id"), {})
                if (active.get("id") == payload.get("id")
                        and active.get("kind") == "user_message"
                        and not active.get("cancel_event", threading.Event()).is_set()):
                    self._emit("reply_draft", payload.get("draft") or {})
                continue
            if (self._is_stale_unsent_npc(kind, payload)
                    or self._is_stale_user_submission(kind, payload)):
                continue
            if self._inflight is not None:
                with self._deferred_lock:
                    self._deferred.append((_prio, _seq, kind, payload))
                continue
            try:
                self._handle(kind, payload)
            except Exception as exc:  # noqa: BLE001
                self._log("error", {"kind": kind, "error": str(exc)[:160]})
