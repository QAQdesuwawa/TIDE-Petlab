"""每角色本地档案：原子快照 + append-only 事件流 + 可修复 JSONL。

一个 ``UserStore`` 对应 ``data/<char_id>/``。状态类文件以临时文件写入并
``os.replace``，避免异常关闭留下半截 JSON；事件流逐行 flush + fsync，读取时
会跳过并记录损坏行。多进程同时写同一角色目录仍不受支持。
"""
import json
import os
import shutil
import tempfile
import threading
import time


INVALID_ID_CHARS = frozenset('<>:"|?*')


def validate_store_id(value):
    """只接受一个安全目录名，阻止 ``../``、分隔符及 Windows 保留字符。"""
    identifier = str(value or "").strip()
    if not identifier or identifier in {".", ".."}:
        raise ValueError("角色 ID 不能为空，且不能是 . 或 ..")
    if any(ch in identifier for ch in ("/", "\\")):
        raise ValueError("角色 ID 不能包含路径分隔符")
    if any(ch in INVALID_ID_CHARS for ch in identifier) or "\x00" in identifier:
        raise ValueError("角色 ID 含有不安全的文件名字符")
    return identifier


def atomic_write_text(path, text, *, durable=True):
    """同目录临时文件 + replace 的原子文本写入。

    ``durable=False`` 只用于可丢弃的测试目录：仍会 flush、原子 replace，唯独不等待
    操作系统把每一条临时工件同步到物理介质。正式本地档案默认保持原有 fsync 合同。
    """
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", suffix=".tmp",
                               dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            if durable:
                os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def atomic_write_json(path, value, *, durable=True):
    """JSON 原子写入；输出格式保持旧版 UTF-8 / indent=2。"""
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2), durable=durable)


class UserStore:
    """单角色持久化边界。线程锁只覆盖同一 Store 实例。"""

    _STREAM_NAMES = (
        "messages_path", "archive_path", "llm_calls_path", "breakpoints_path",
        "interrupts_path", "self_ledger_path", "ledger_recall_path", "events_path",
        "action_receipts_path", "outcomes_path", "cold_log_path", "constraint_path",
        "prompt_traces_path", "telemetry_path", "recall_evidence_path",
        "boundary_path", "math_trace_path", "memory_recall_events_path",
        "memory_influence_graph_path", "memory_product_transactions_path",
    )

    def __init__(self, user_id, base_dir="data", *, durable_writes=True):
        self.user_id = validate_store_id(user_id)
        self.base_dir = os.path.abspath(base_dir)
        self.user_dir = os.path.abspath(os.path.join(self.base_dir, self.user_id))
        # 双保险：即使后续放宽 ID 校验，store 也绝不能逃出 base_dir。
        if os.path.commonpath([self.base_dir, self.user_dir]) != self.base_dir:
            raise ValueError("角色 ID 必须位于数据目录内")
        self._lock = threading.RLock()
        # 生产默认永远要求文件系统同步；虚拟 tick 夹具可显式关闭，以避免把数小时
        # 的逻辑时间误测成数千次物理磁盘同步耗时。它不会改变 JSON、receipt 或状态机。
        self._durable_writes = bool(durable_writes)
        self._jsonl_issues = {}
        # 同一轮 Runner / GUI 会多次读 messages.jsonl；按 mtime+size 缓存，
        # 本实例写入立即失效，另一实例/进程写入则由文件签名自动失效。
        self._jsonl_cache = {}

        self.state_path = os.path.join(self.user_dir, "state.json")
        # 可由 state + 绝对时间重建的运行时调度投影；不是人格/记忆事实源。
        self.clock_plan_path = os.path.join(self.user_dir, "clock_plan.json")
        self.messages_path = os.path.join(self.user_dir, "messages.jsonl")
        self.archive_path = os.path.join(self.user_dir, "archive.jsonl")
        self.llm_calls_path = os.path.join(self.user_dir, "llm_calls.jsonl")
        self.breakpoints_path = os.path.join(self.user_dir, "breakpoints.jsonl")
        self.interrupts_path = os.path.join(self.user_dir, "interrupts.jsonl")
        self.self_ledger_path = os.path.join(self.user_dir, "self_ledger.jsonl")
        self.ledger_recall_path = os.path.join(self.user_dir, "ledger_recall.jsonl")
        self.events_path = os.path.join(self.user_dir, "events.jsonl")
        self.action_receipts_path = os.path.join(self.user_dir, "action_receipts.jsonl")
        self.outcomes_path = os.path.join(self.user_dir, "outcomes.jsonl")
        self.cold_log_path = os.path.join(self.user_dir, "cold_log.jsonl")
        self.constraint_path = os.path.join(self.user_dir, "constraint_log.jsonl")
        self.prompt_traces_path = os.path.join(self.user_dir, "prompt_traces.jsonl")
        # 召回候选的弱/强证据；仅审计与后续的可回放累计使用，绝不保存历史正文。
        self.recall_evidence_path = os.path.join(self.user_dir, "recall_evidence.jsonl")
        # 边界词元挂账（否认声明，提案 M3）：未证实前零压制，只观察；不含历史正文。
        self.boundary_path = os.path.join(self.user_dir, "boundary.jsonl")
        # Alpha 0.2 R0.5：提交后的公式数值观察流。它不是真实状态源，运行逻辑
        # 不得读取它作决策；正文事实仍在 messages/archive/receipt。
        self.math_trace_path = os.path.join(self.user_dir, "math_trace.jsonl")
        # Alpha 0.4 recall DAG 的独立 append-only 事实流。M1 只建立存储合同；
        # Runner 在 M7 之前不会读取或写入它，旧 archive recall 行也不会被搬移或覆盖。
        self.memory_recall_events_path = os.path.join(
            self.user_dir, "memory_recall_events.jsonl")
        # Alpha 0.4 C2-R：ID-only 的前向记忆影响图。每行是一笔已提交 recall
        # transaction，只保存 node/edge IDs、六根变化与 receipt refs，不复制正文。
        # 旧 memory_recall_events 流继续只供旧 family/迁移读取。
        self.memory_influence_graph_path = os.path.join(
            self.user_dir, "memory_influence_graph.jsonl")
        # Alpha 0.4 D1 产品事实流：一个 owner-local event 的 C1-C5 结果只以
        # 一条无正文 transaction 发布；forward edges 嵌在同一行，不能分段落盘。
        self.memory_product_transactions_path = os.path.join(
            self.user_dir, "memory_product_transactions.jsonl")
        # 只读可视化投影的 append-only 缓存。它不是状态或因果事实源，损坏/删除后可由
        # events、receipt、snapshot 重建，故绝不能被运行逻辑读取来作决定。
        self.telemetry_path = os.path.join(self.user_dir, "telemetry.jsonl")
        self.user_card_path = os.path.join(self.user_dir, "user_card.json")
        self.profile_path = os.path.join(self.user_dir, "profile.json")
        self.persona_path = os.path.join(self.user_dir, "persona_card.md")
        self.snapshots_dir = os.path.join(self.user_dir, "snapshots")
        os.makedirs(self.user_dir, exist_ok=True)

    # ── 原子文件 ──
    def write_json(self, path, value):
        """在本角色目录内原子写 JSON，拒绝写到外部路径。"""
        candidate = os.path.abspath(path)
        if os.path.commonpath([self.user_dir, candidate]) != self.user_dir:
            raise ValueError("只能写入当前角色的数据目录")
        with self._lock:
            atomic_write_json(candidate, value, durable=self._durable_writes)
        return candidate

    def write_text(self, path, text):
        """在本角色目录内原子写文本。"""
        candidate = os.path.abspath(path)
        if os.path.commonpath([self.user_dir, candidate]) != self.user_dir:
            raise ValueError("只能写入当前角色的数据目录")
        with self._lock:
            atomic_write_text(candidate, str(text), durable=self._durable_writes)
        return candidate

    def _load_json(self, path):
        if not os.path.exists(path):
            return None
        with self._lock:
            with open(path, "r", encoding="utf-8-sig") as f:
                return json.load(f)

    def read_json(self, path):
        """读取当前角色目录中的 JSON；供关联模块使用的公开接口。"""
        candidate = os.path.abspath(path)
        if os.path.commonpath([self.user_dir, candidate]) != self.user_dir:
            raise ValueError("只能读取当前角色的数据目录")
        return self._load_json(candidate)

    def stream_paths(self):
        """返回本角色全部 append-only 流路径，供诊断/修复工具遍历。"""
        return tuple(getattr(self, name) for name in self._STREAM_NAMES)

    # ── 档案 ──
    def save_profile(self, profile):
        return self.write_json(self.profile_path, profile)

    def load_profile(self):
        return self._load_json(self.profile_path)

    def save_persona_card(self, text):
        return self.write_text(self.persona_path, text)

    def load_persona_card(self):
        if not os.path.exists(self.persona_path):
            return None
        with self._lock:
            with open(self.persona_path, "r", encoding="utf-8") as f:
                return f.read()

    # ── 全量状态 ──
    def save_state(self, state):
        return self.write_json(self.state_path, state)

    def load_state(self):
        return self._load_json(self.state_path)

    # ── 墙钟计划（ops 层调度投影） ──
    def save_clock_plan(self, plan):
        return self.write_json(self.clock_plan_path, plan)

    def load_clock_plan(self):
        return self._load_json(self.clock_plan_path)

    # ── append-only 流 ──
    def append_jsonl(self, path, record):
        """追加一条完整 JSONL 并同步到 OS；行与行之间永不覆写。"""
        candidate = os.path.abspath(path)
        if os.path.commonpath([self.user_dir, candidate]) != self.user_dir:
            raise ValueError("只能写入当前角色的数据目录")
        line = json.dumps(record, ensure_ascii=False) + "\n"
        os.makedirs(os.path.dirname(candidate), exist_ok=True)
        with self._lock:
            with open(candidate, "a", encoding="utf-8", newline="") as f:
                f.write(line)
                f.flush()
                if self._durable_writes:
                    os.fsync(f.fileno())
            self._jsonl_cache.pop(candidate, None)

    def append_message(self, record):
        self.append_jsonl(self.messages_path, record)

    def append_archive(self, record):
        self.append_jsonl(self.archive_path, record)

    def append_llm_call(self, record):
        self.append_jsonl(self.llm_calls_path, record)

    def read_llm_calls(self):
        return self.read_jsonl(self.llm_calls_path)

    def append_breakpoint(self, record):
        self.append_jsonl(self.breakpoints_path, record)

    def read_breakpoints(self):
        return self.read_jsonl(self.breakpoints_path)

    def append_interrupt(self, record):
        self.append_jsonl(self.interrupts_path, record)

    def read_interrupts(self):
        return self.read_jsonl(self.interrupts_path)

    def append_self_ledger(self, record):
        self.append_jsonl(self.self_ledger_path, record)

    def read_self_ledger(self):
        return self.read_jsonl(self.self_ledger_path)

    def append_ledger_recall(self, record):
        self.append_jsonl(self.ledger_recall_path, record)

    def read_ledger_recalls(self):
        return self.read_jsonl(self.ledger_recall_path)

    def append_event(self, record):
        self.append_jsonl(self.events_path, record)

    def read_events(self):
        return self.read_jsonl(self.events_path)

    def append_telemetry(self, record):
        """追加一条提交后遥测投影；仅供观测与展示，不参与引擎决策。"""
        self.append_jsonl(self.telemetry_path, record)

    def read_telemetry(self):
        return self.read_jsonl(self.telemetry_path)

    def append_action_receipt(self, record):
        self.append_jsonl(self.action_receipts_path, record)

    def read_action_receipts(self):
        return self.read_jsonl(self.action_receipts_path)

    def reserve_npc_action_id(self, ts):
        """从 owner 的完整已提交 ledger 分配下一枚 action 身份。

        relation Runner 可以隔离 root state，却不能各自从 ``:1`` 开始向同一个
        owner action/message ledger 写入。该方法只分配身份，不写任何 output 或
        时间事实；Actor 的单写事务仍决定是否真正 F16 提交。
        """
        with self._lock:
            highest = 0
            rows = list(self.read_messages()) + list(self.read_action_receipts()) + list(self.read_events())
            for row in rows:
                action_id = str(row.get("action_id") or "")
                parts = action_id.rsplit(":", 1)
                if len(parts) != 2:
                    continue
                try:
                    highest = max(highest, int(parts[1]))
                except ValueError:
                    continue
            return "action:%d:%d" % (int(ts), highest + 1)

    def recover_npc_action_receipts(self):
        """补齐“消息已落盘、action receipt 尚未落盘”的单进程中断窗口。

        NPC message 自身带有 ``action_id``、来源、时间和参数版本，故它是已经
        发送这一事实的最低可恢复证据。若进程恰好在消息追加之后、receipt 追加
        之前中断，重启时可据此幂等补一条标注为 recovered 的收据，避免后续 action
        序号复用或用户反馈失去归因。

        这不伪造消息，也不重放输出后的状态回流/archive/ledger：那些没有可重建
        证据时保持“未结算”的保守状态，并由调用方的独立恢复策略处理。没有
        ``action_id`` 的旧档案同样不推测补写。
        """
        with self._lock:
            messages = self.read_messages()
            known = {str(row.get("action_id")) for row in self.read_action_receipts()
                     if row.get("action_id") is not None}
            recovered = []
            for message in messages:
                if message.get("role") != "npc":
                    continue
                action_id = message.get("action_id")
                if action_id is None or str(action_id) in known:
                    continue
                receipt = {
                    "schema_version": 1,
                    "action_id": str(action_id),
                    "ts": int(message.get("ts", 0) or 0),
                    "source": str(message.get("source") or "reply"),
                    "params_sha": str(message.get("params_sha") or "unknown"),
                    "recall_refs": [],
                    "recovery": {
                        "kind": "message_without_action_receipt",
                        "recovered_at": int(time.time() * 1000),
                        "missing_fields": ["recall_mode", "recall_refs",
                                           "recall_strategy", "state_snapshot_turn"],
                    },
                }
                for field in ("turn_id", "cause_id"):
                    if message.get(field) is not None:
                        receipt[field] = str(message[field])
                self.append_action_receipt(receipt)
                known.add(str(action_id))
                recovered.append(receipt)
            return recovered

    def append_outcome(self, record):
        self.append_jsonl(self.outcomes_path, record)

    def read_outcomes(self):
        return self.read_jsonl(self.outcomes_path)

    def append_cold(self, record):
        self.append_jsonl(self.cold_log_path, record)

    def read_cold_log(self):
        return self.read_jsonl(self.cold_log_path)

    def append_constraint(self, record):
        self.append_jsonl(self.constraint_path, record)

    def read_constraints(self):
        return self.read_jsonl(self.constraint_path)

    def append_prompt_trace(self, record):
        """仅本地的 responder 完整输入留痕；分享/盲评导出器不得读取此流。"""
        self.append_jsonl(self.prompt_traces_path, record)

    def read_prompt_traces(self):
        return self.read_jsonl(self.prompt_traces_path)

    def append_recall_evidence(self, record):
        """追加召回探测证据；调用方负责其无正文与字段合法性。"""
        self.append_jsonl(self.recall_evidence_path, record)

    def read_recall_evidence(self):
        return self.read_jsonl(self.recall_evidence_path)

    def append_boundary(self, record):
        """追加边界词元挂账行（否认声明，提案 M3）；未证实前零压制。"""
        self.append_jsonl(self.boundary_path, record)

    def read_boundary(self):
        return self.read_jsonl(self.boundary_path)

    def append_math_trace(self, record):
        """追加已提交事务的无正文数学 span；不得被运行决策回读。"""
        self.append_jsonl(self.math_trace_path, record)

    def read_math_trace(self):
        return self.read_jsonl(self.math_trace_path)

    def append_memory_recall_event(self, record):
        """追加一条已由 Alpha 0.4 合同验证的 recall DAG 事件。"""
        self.append_jsonl(self.memory_recall_events_path, record)

    def read_memory_recall_events(self):
        return self.read_jsonl(self.memory_recall_events_path)

    def append_memory_influence_transaction(self, record):
        """追加一笔已由 C2-R 合同验证的 ID-only recall graph transaction。"""
        self.append_jsonl(self.memory_influence_graph_path, record)

    def read_memory_influence_graph(self):
        return self.read_jsonl(self.memory_influence_graph_path)

    def append_memory_product_transaction(self, record):
        """幂等追加一条完整 Alpha 0.4 owner-local memory transaction。

        exact transaction ID 已存在且内容逐位一致时返回 already_committed；
        同 ID 内容冲突时闭锁。检查与单行 append 共用 Store 实例锁。
        """
        if not isinstance(record, dict):
            raise ValueError("memory product transaction 必须是对象")
        transaction_id = str(record.get("transaction_id") or "").strip()
        if not transaction_id:
            raise ValueError("memory product transaction_id 不能为空")
        with self._lock:
            existing = [row for row in self.read_jsonl(
                self.memory_product_transactions_path)
                if str(row.get("transaction_id") or "") == transaction_id]
            if len(existing) > 1:
                raise ValueError("memory product transaction identity 重复")
            if existing:
                if existing[0] != record:
                    raise ValueError("conflicting transaction identity")
                return "already_committed"
            self.append_jsonl(self.memory_product_transactions_path, record)
            return "committed"

    def read_memory_product_transactions(self):
        return self.read_jsonl(self.memory_product_transactions_path)

    def inspect_jsonl(self, path):
        """读取有效记录并返回损坏行元数据；不会修改文件。"""
        candidate = os.path.abspath(path)
        if not os.path.exists(candidate):
            return {"records": [], "invalid_lines": []}
        records, invalid = [], []
        with self._lock:
            with open(candidate, "r", encoding="utf-8-sig") as f:
                for number, raw in enumerate(f, start=1):
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError as exc:
                        invalid.append({"line": number, "reason": str(exc),
                                        "preview": line[:120]})
        return {"records": records, "invalid_lines": invalid}

    def read_jsonl(self, path):
        """容错读 JSONL：保留有效历史，跳过损坏行并保留诊断信息。"""
        candidate = os.path.abspath(path)
        with self._lock:
            signature = None
            if os.path.exists(candidate):
                stat = os.stat(candidate)
                signature = (stat.st_mtime_ns, stat.st_size)
            cached = self._jsonl_cache.get(candidate)
            if cached is not None and cached[0] == signature:
                self._jsonl_issues[candidate] = list(cached[2])
                return list(cached[1])
            report = self.inspect_jsonl(candidate)
            records, invalid = report["records"], report["invalid_lines"]
            self._jsonl_cache[candidate] = (signature, records, invalid)
            self._jsonl_issues[candidate] = invalid
            return list(records)

    def jsonl_issues(self, path=None):
        """最近一次读取发现的损坏行；可供 UI/健康检查提示。"""
        if path is None:
            return {k: list(v) for k, v in self._jsonl_issues.items() if v}
        return list(self._jsonl_issues.get(os.path.abspath(path), []))

    def repair_jsonl(self, path, backup=True):
        """仅以有效行重建 JSONL；默认先保留带时间戳的原始备份。"""
        candidate = os.path.abspath(path)
        if os.path.commonpath([self.user_dir, candidate]) != self.user_dir:
            raise ValueError("只能修复当前角色的数据目录")
        report = self.inspect_jsonl(candidate)
        invalid = report["invalid_lines"]
        if not invalid:
            return {"repaired": False, "valid_rows": len(report["records"]),
                    "invalid_rows": 0, "backup": None}
        backup_path = None
        with self._lock:
            if backup:
                stamp = time.strftime("%Y%m%d%H%M%S")
                backup_path = f"{candidate}.corrupt-{stamp}.bak"
                suffix = 1
                while os.path.exists(backup_path):
                    backup_path = f"{candidate}.corrupt-{stamp}-{suffix}.bak"
                    suffix += 1
                shutil.copy2(candidate, backup_path)
            content = "".join(json.dumps(row, ensure_ascii=False) + "\n"
                              for row in report["records"])
            atomic_write_text(candidate, content)
            self._jsonl_cache.pop(candidate, None)
        self._jsonl_issues[candidate] = []
        return {"repaired": True, "valid_rows": len(report["records"]),
                "invalid_rows": len(invalid), "backup": backup_path}

    def read_messages(self):
        return self.read_jsonl(self.messages_path)

    def read_recent_message_window(self, limit):
        """仅读取消息流末尾的有限完整记录，供产品首屏/历史分页使用。

        这不是 archive 的第二份缓存：它不写入 cache、不修复坏行，也不改变任何
        Runner 状态。末页到达后，UI 可以先进入会话；更早记录只在用户请求时按更大
        窗口重新读取。JSONL 一行只会在完整 append 后出现，因此只接受可独立解析的
        尾部行，跨 block 的首行残片一律忽略。
        """
        try:
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            raise ValueError("limit 必须是正整数") from None
        path = self.messages_path
        if not os.path.isfile(path):
            return {"records": [], "has_older": False}
        block_size = 16 * 1024
        with self._lock:
            try:
                with open(path, "rb") as handle:
                    handle.seek(0, os.SEEK_END)
                    position = handle.tell()
                    tail = b""
                    records = []
                    # 需要比 target 多一个有效行，才能诚实报告仍有历史可回填。
                    while position > 0:
                        size = min(block_size, position)
                        position -= size
                        handle.seek(position)
                        tail = handle.read(size) + tail
                        raw_rows = tail.splitlines()
                        if position > 0 and raw_rows:
                            raw_rows = raw_rows[1:]  # 文件中间的未完整首行
                        parsed = []
                        for raw in raw_rows:
                            if not raw.strip():
                                continue
                            try:
                                item = json.loads(raw.decode("utf-8-sig"))
                            except (UnicodeDecodeError, json.JSONDecodeError):
                                continue
                            if isinstance(item, dict):
                                parsed.append(item)
                        records = parsed
                        if len(records) > limit:
                            break
            except OSError:
                return {"records": [], "has_older": False}
        return {
            "records": records[-limit:],
            "has_older": bool(position > 0 or len(records) > limit),
        }

    def read_recent_messages(self, limit):
        """``read_recent_message_window`` 的只读 records 便捷视图。"""
        return self.read_recent_message_window(limit)["records"]

    def read_archive(self):
        return self.read_jsonl(self.archive_path)

    # ── 快照 ──
    def save_snapshot(self, state, turn_id):
        os.makedirs(self.snapshots_dir, exist_ok=True)
        path = os.path.join(self.snapshots_dir, f"snapshot_{int(turn_id):06d}.json")
        return self.write_json(path, state)

    def list_snapshots(self):
        if not os.path.exists(self.snapshots_dir):
            return []
        return sorted(name for name in os.listdir(self.snapshots_dir)
                      if name.endswith(".json"))
