# ops/app.py
"""T.I.D.E. 的本地聊天界面。

界面只订阅每个角色 Actor 的输出；用户输入、墙钟到期、延迟释放、追发和分享均
经 Actor 串行到 Runner。GUI 不直接批量回放、轮询或写入角色状态。
"""
import os
import json
import copy
import math
import queue
import random
import threading
import time
import tkinter as tk
import traceback
from tkinter import ttk
from pathlib import Path

from agents.labor import parse_labor
from agents.demo_client import demo_mode_enabled
from agents.llm_client import parse_api_keys
from core.params import UI
from core.schema_matrix import SchemaMatrix
from ops.action_trace import build_action_trace
from ops.config import DEMO_PRESETS_DIR, MAX_CHAT_LINES, PALETTE, PRESETS_DIR
from ops.export import export_session
from ops.ui.theme import apply_theme
from ops.ui.demo_observatory import (build_demo_observatory_snapshot, opening_trace_time_page,
                                     opening_trace_delta_rows, opening_trace_viewport_details)
from ops.ui.formal_diagnostic_window import FormalDiagnosticWindow
from ops.ui.session_summary import build_session_summary
from ops.ui.conversation_entry import build_conversation_entry_snapshot
from ops.ui.trio_group_lab import TrioGroupLab, build_trio_group_lab_snapshot
from ops.ui.history_window import select_history_window
from ops.ui.message_timing import markers_for_message
from ops.fixture_provenance import is_non_dialogue_seed
from ops.demo_scene import (HIGH_TENSION_SCENE_ID, get_scene, is_demo_profile,
                            prepare_competition_scene, scene_catalog)
from ops.stream_draft import sanitize_draft_text
from ops.typing_presentation import presentation_snapshot, typing_presentation_plan

P = PALETTE


# 创建页是角色设定的编辑器，而不是把七常量误做成七个运行时旋钮。
# 短标签只服务 UI；矩阵与常量语义的权威来源仍是 SchemaMatrix。
ROLE_WEIGHT_LABELS = (
    ("Counter_Phobic", "嘴硬"),
    ("Mirroring", "虚荣"),
    ("Avoidance", "抽离"),
    ("Trauma_Recon", "黏连"),
    ("Pragmatism", "理性"),
)


def rebalance_role_weights(raw_weights, changed_key):
    """把创建页的五个百分比维持在一个 100% 配比盘中。

    用户拖动 ``changed_key`` 时，其他项按拖动前的相对比例共同让出或补足；
    这只是创建页输入手感，不新增算法变量，Runner 仍会按同一五维重新结算七常量。
    """
    keys = [key for key, _ in ROLE_WEIGHT_LABELS]
    if changed_key not in keys:
        raise KeyError(f"未知角色维度：{changed_key}")
    values = {key: max(0, min(100, int(round(float((raw_weights or {}).get(key, 0))))))
              for key in keys}
    other_keys = [key for key in keys if key != changed_key]
    remaining = 100 - values[changed_key]
    previous_total = sum(values[key] for key in other_keys)
    if remaining <= 0:
        for key in other_keys:
            values[key] = 0
        return values
    if previous_total <= 0:
        # 没有既有比例时才均分，避免凭空偏爱某个维度。
        for index, key in enumerate(other_keys):
            values[key] = remaining // len(other_keys) + (1 if index < remaining % len(other_keys) else 0)
        return values
    allocated = 0
    for key in other_keys[:-1]:
        value = int(round(values[key] / previous_total * remaining))
        value = max(0, min(remaining - allocated, value))
        values[key] = value
        allocated += value
    values[other_keys[-1]] = remaining - allocated
    return values


def _persona_identity(persona_card, limit=150):
    """从角色卡提取适合创建页展示的身份段，绝不把整张 prompt 铺进 UI。"""
    text = str(persona_card or "").strip()
    marker = "## 身份"
    if marker in text:
        text = text.split(marker, 1)[1]
        next_section = text.find("\n## ")
        if next_section >= 0:
            text = text[:next_section]
    text = " ".join(text.replace("#", "").split())
    return (text[:limit].rstrip() + "…") if len(text) > limit else text


def build_role_creation_preview(card, raw_weights=None):
    """五维 → 七常量的只读创建期预览；创建时仍由 Runner 重新结算。"""
    source = dict(card or {})
    if raw_weights is not None:
        source["weights"] = dict(raw_weights)
    parsed = parse_labor(json.dumps(source, ensure_ascii=False))
    matrix = SchemaMatrix()
    # 文件允许省略 0 权重，但创建 UI 必须持续展示完整五维，避免“只剩一个模板”
    # 被误解为矩阵没有其余输入维。
    weights = {key: float(parsed["weights"].get(key, 0.0))
               for key in matrix.get_available_schemas()}
    return {
        "description": str(source.get("_description") or "").strip(),
        "identity": _persona_identity(parsed["persona_card"]),
        "weights": weights,
        "constants": matrix.generate_constants(weights),
        "constant_meanings": dict(matrix.PARAM_DESCRIPTIONS),
    }


def scan_presets(presets_dir=None):
    """用户创建角色时可选择的正式人格卡。"""
    d = presets_dir or PRESETS_DIR
    if not os.path.exists(d):
        return []
    return sorted(f for f in os.listdir(d) if f.endswith(".json"))


def scan_demo_presets(presets_dir=None, demo_presets_dir=None):
    """比赛演示入口的卡：可复用正式卡，加上不进入日常创建器的演示卡。"""
    user_dir = presets_dir or PRESETS_DIR
    demo_dir = demo_presets_dir or DEMO_PRESETS_DIR
    names = []
    for directory in (user_dir, demo_dir):
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            if name.endswith(".json") and name not in names:
                names.append(name)
    return names


def resolve_demo_preset(name, presets_dir=None, demo_presets_dir=None):
    """按演示入口的白名单解析卡路径；测试 fixture 永远不在查找范围内。"""
    if not name:
        return None
    user_dir = presets_dir or PRESETS_DIR
    demo_dir = demo_presets_dir or DEMO_PRESETS_DIR
    for directory in (user_dir, demo_dir):
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            return path
    return None


def scan_live_sessions(base_dir="data"):
    """产品登录页的只读会话计数，不把旧 profile 当作可继续聊天的会话。"""
    from ops.guild import is_visible_live_profile
    import json

    try:
        names = os.listdir(base_dir)
    except OSError:
        return []
    result = []
    for name in names:
        profile_path = os.path.join(base_dir, name, "profile.json")
        try:
            with open(profile_path, "r", encoding="utf-8-sig") as handle:
                profile = json.load(handle)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if is_visible_live_profile(profile):
            result.append(name)
    return sorted(result)


def _fmt_time(ts_ms):
    try:
        return time.strftime("%H:%M", time.localtime(ts_ms / 1000))
    except Exception:
        return "--:--"


class TideUI:
    def __init__(self, root, guild, presets_dir=None, async_startup=True,
                 auto_login=False):
        self.root = root
        # guild 可为对象（冒烟/测试）或工厂 callable（真机：登录拿到密钥后才建公会）
        if callable(guild):
            self._guild_factory = guild
            self.guild = None
        else:
            self._guild_factory = None
            self.guild = guild
        self.presets_dir = presets_dir or PRESETS_DIR
        self.demo_presets_dir = DEMO_PRESETS_DIR
        # 比赛录屏工具不是日常产品入口；仅在显式开关下露出，避免普通用户会话
        # 混入“演示”和“+90 分钟”等开发控件。
        self._competition_tools_enabled = str(
            os.getenv("TIDE_COMPETITION_DEMO_UI", "")).strip().lower() in {
                "1", "true", "yes", "on"}
        # 录屏时只留下观察与真实墙钟；开发阶段仍可显式保留时间推进工具。
        self._realtime_demo_capture = str(
            os.getenv("TIDE_REALTIME_DEMO_CAPTURE", "")).strip().lower() in {
                "1", "true", "yes", "on"}
        self.async_startup = async_startup
        self.active_char = None
        self.unread = {}
        self.busy = {}          # cid -> 该角色是否在等回复（每角色独立，互不锁死）
        self.typing = {}        # cid -> 延迟晾人挂起中（显示"正在输入"）
        self._typing_visible = {}  # cid -> 当前等待态是否正处于可见“输入中”窗口
        self._typing_cycle = {}    # cid -> 表现层节律代次，过期 after 回调不能复活旧状态
        self._typing_plans = {}    # cid -> 已结算互动读数的只读展示投影
        self._last_typing_presentation = {}  # cid -> 最近实际展示过的只读计划
        # 已验证的流式稳定句段：仅供输入框上方的临时展示，绝不是聊天消息，也不落盘。
        self._reply_drafts = {}
        self.pending_user = {}  # cid -> 发送后、落盘前的乐观气泡
        self.hidden_npc = {}    # cid -> 打字停留期间暂不显示的 npc 消息 ts
        self._reveal_queue = [] # 回放揭示队列（固定节奏逐条冒泡）
        self._revealing = False
        # 启动回放在 worker 内可先后正式落盘；UI 不能因为下一条已经写入 JSONL，就
        # 在前一条的视觉揭示之前把它们全部画出来。这里仅保存已存在／已揭示的
        # output action 引用，是显示闸，不是第二条消息总线或运行时状态。
        self._startup_replay_gate = set()
        self._startup_replay_baseline = {}
        self._startup_replay_pending = {}
        self._startup_replay_visible = {}
        self._partial = {}      # cid -> 连发揭示进度 {ts, parts, shown}
        self._reveal_text = {}  # cid -> 待揭示的回复正文（分句连发用）
        self._reveal_send_at = {}  # cid -> 待释放回复的真实发送时刻（含延迟回复）
        self._pending_visual_reveal = set()  # Actor 已 release、Tk 尚未开始揭示的角色
        # Actor 的 release 已将消息正式落盘，但 Tk 仍在保留“输入中”空窗时，
        # 这张表是该已提交消息的可见性闸。它只防止重绘把全文抢先画出来，绝不
        # 参与 Actor、墙钟或任何数值计算。
        self._pending_visual_ts = {}  # cid -> 已提交但尚未开始分段揭示的 npc ts
        # 已正式落盘、但前一条仍在视觉揭示中的 NPC 输出。它是本地播放队列，不是
        # 行为队列：Actor 不读取它，Store/墙钟/追发资格也绝不因此等待。
        self._committed_npc_visual_queue = []
        self._queued_npc_visual_keys = {}
        self._autonomous_typing_task = {}  # cid -> 尚未定稿的主动 Actor 任务 ID
        self.history_limit = {} # cid -> 当前 UI 历史窗口大小（只影响渲染）
        self.history_more_btn = None
        self._pref_dialog_open = False
        # 比赛演示的数值面板只是已结算 state/plan/telemetry 的读取投影；它不占用
        # Actor 队列，也不保存第二份状态或改变任何公式。
        self._demo_observatory = None
        self._demo_observatory_token = 0
        self._formal_diagnostic_window = None
        self._formal_diagnostic_cid = None
        # 实际 Actor tick 的只读快照。它只给比赛监测显示离散结算点，不落盘、
        # 不进入角色状态或提示词。
        self._demo_tick_views = {}
        # 只保留 Actor 真正完成的 tick 快照，供演示趋势图按真实观察点绘制；它不
        # 落盘、更不反写 Runner，且窗口关闭后也不参与任何业务决策。
        self._demo_tick_history = {}
        # 主图只消费 Actor 已结算的 opening_state_transition；不是预测线，也不
        # 是 UI 自己按刷新时间补点。每个角色独立保留最近的演示窗口。
        self._demo_opening_trace = {}
        self.actors = {}        # cid -> Actor（T7-1：事件入队，GUI 不再直接摸 runner）
        self._catch_up_done = set()
        self._ui_rng = random.Random()   # 仅视觉节律，不触碰引擎 rng 或 Actor 队列
        self._typing_widget = None
        self.loading_done = False
        self._loading_target = 0.0
        self._loading_progress = tk.DoubleVar(value=0.0)
        self._loading_progress_running = False
        self.char_widgets = {}
        # Tk 只能由主线程触碰。Actor / 启动 worker 只把不可变结果投进这个队列；
        # 主线程定时排空后才更新气泡、加载页和列表。
        self._ui_events = queue.Queue()
        self._closed = False

        self.root.title("TIDE · Local Interaction")
        self.root.geometry("980x700")
        self.root.minsize(800, 560)

        self.style = ttk.Style()
        if 'clam' in self.style.theme_names():
            self.style.theme_use('clam')
        apply_theme(self.style, self.root)

        self._build_layout()
        self._build_loading()
        self._build_login()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(25, self._drain_ui_events)
        if auto_login:
            self._login()
        else:
            self._show_login(True)

    # ------------------------------------------------------------------
    # 布局：左会话列表 / 右聊天窗
    # ------------------------------------------------------------------
    def _build_layout(self):
        main = ttk.Frame(self.root)
        main.pack(fill=tk.BOTH, expand=True)

        self.left = tk.Frame(main, width=250, bg=P["panel"],
                             highlightbackground=P["line"], highlightthickness=1)
        self.left.pack(side=tk.LEFT, fill=tk.Y)
        self.left.pack_propagate(False)
        header = tk.Frame(self.left, bg=P["panel"])
        header.pack(fill=tk.X, padx=12, pady=10)
        brand = tk.Frame(header, bg=P["panel"])
        brand.pack(side=tk.LEFT)
        tk.Label(brand, text="TIDE", bg=P["panel"], fg=P["pink_dark"],
                 font=("微软雅黑", 15, "bold")).pack(anchor=tk.W)
        tk.Label(brand, text="LOCAL INTERACTION", bg=P["panel"], fg=P["sub"],
                 font=("微软雅黑", 7, "bold")).pack(anchor=tk.W)
        self.new_btn = tk.Button(header, text="新建", bg=P["pink"], fg="white",
                                 relief="flat", font=("微软雅黑", 13, "bold"),
                                 padx=9, command=self._new_dialog)
        self.new_btn.pack(side=tk.RIGHT)
        self.demo_btn = tk.Button(header, text="演示", bg=P["pink_soft"], fg=P["pink_dark"],
                                  relief="flat", font=("微软雅黑", 10, "bold"),
                                  padx=7, command=self._new_demo_dialog)
        if self._competition_tools_enabled:
            self.demo_btn.pack(side=tk.RIGHT, padx=(0, 6))
        # DIR-154：将实验入口放在左侧导航，不与右上工具栏争夺宽度。该按钮只
        # 打开已提交 source 的只读视图，绝不启动 Router/Actor/tick/relay。
        self.trio_lab_btn = tk.Button(
            self.left, text="三人群聊实验", bg=P["blue_soft"], fg=P["blue_dark"],
            relief="flat", font=("微软雅黑", 10, "bold"), pady=7,
            command=self._open_trio_group_lab)
        if self._competition_tools_enabled:
            self.trio_lab_btn.pack(fill=tk.X, padx=12, pady=(0, 6))
        self.char_list = tk.Frame(self.left, bg=P["panel"])
        self.char_list.pack(fill=tk.BOTH, expand=True)
        self.empty_label = tk.Label(self.char_list, text="点右上角 ＋ 创建第一个角色",
                                    bg=P["panel"], fg=P["sub"], font=("微软雅黑", 10))
        self.empty_label.pack(pady=24)

        right = ttk.Frame(main)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        topbar = tk.Frame(right, bg=P["bg"])
        topbar.pack(fill=tk.X, padx=16, pady=(12, 6))
        self.active_label = tk.Label(topbar, text="", bg=P["bg"], fg=P["pink_dark"],
                                     font=("微软雅黑", 13, "bold"))
        self.active_label.pack(side=tk.LEFT)
        self.timeline_var = tk.StringVar(value="")
        self.timeline_label = tk.Label(topbar, textvariable=self.timeline_var,
                                       bg=P["pink_soft"], fg=P["pink_dark"],
                                       font=("微软雅黑", 9, "bold"), padx=8, pady=3)
        self.timeline_label.pack(side=tk.LEFT, padx=(8, 0))
        self.status_var = tk.StringVar(value="")
        ttk.Label(topbar, textvariable=self.status_var,
                  style="Subtitle.TLabel").pack(side=tk.RIGHT)
        self.pref_btn = tk.Button(topbar, text="互动偏好", bg=P["bg"], fg=P["pink_dark"],
                                  relief="flat", font=("微软雅黑", 10),
                                  command=lambda: self._ask_preference_dialog(
                                      self.active_char) if self.active_char else None)
        self.pref_btn.pack(side=tk.RIGHT, padx=(0, 8))
        self.overview_btn = tk.Button(topbar, text="互动概览", bg=P["bg"], fg=P["pink_dark"],
                                      relief="flat", font=("微软雅黑", 10),
                                      command=self._open_session_overview)
        self.overview_btn.pack(side=tk.RIGHT, padx=(0, 8))
        self.trace_btn = tk.Button(topbar, text="回执追溯", bg=P["bg"], fg=P["pink_dark"],
                                   relief="flat", font=("微软雅黑", 10),
                                   command=self._open_action_trace)
        self.trace_btn.pack(side=tk.RIGHT, padx=(0, 8))
        self.feedback_btn = tk.Button(topbar, text="反馈", bg=P["bg"], fg=P["pink_dark"],
                                      relief="flat", font=("微软雅黑", 10),
                                      command=self._open_feedback_dialog)
        self.feedback_btn.pack(side=tk.RIGHT, padx=(0, 8))
        self.demo_advance_btn = tk.Button(
            topbar, text="演示推进 +90 分钟", bg=P["pink_soft"], fg=P["pink_dark"],
            relief="flat", font=("微软雅黑", 10, "bold"), command=self._advance_demo_clock)
        self.demo_observatory_btn = tk.Button(
            topbar, text="演示监测", bg=P["pink_soft"], fg=P["pink_dark"],
            relief="flat", font=("微软雅黑", 10, "bold"), command=self._open_demo_observatory)
        self.formal_diagnostic_btn = tk.Button(
            topbar, text="模型诊断", bg=P["blue_soft"], fg=P["blue_dark"],
            relief="flat", font=("微软雅黑", 10, "bold"),
            command=self._open_formal_diagnostics)
        mid = ttk.Frame(right)
        mid.pack(fill=tk.BOTH, expand=True, padx=16)
        # 气泡聊天窗：Canvas + 内嵌 Frame，每条消息一个 Label 气泡
        # （tk.Text 的 tag 只能整段对齐，无法"文字左对齐 + 气泡贴右"，弃用）
        self.chat_canvas = tk.Canvas(mid, bg=P["chat_bg"], highlightthickness=1,
                                     highlightbackground=P["line"])
        scroll = ttk.Scrollbar(mid, command=self.chat_canvas.yview)
        self.chat_canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.chat_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.bubbles = tk.Frame(self.chat_canvas, bg=P["chat_bg"])
        self._bubble_win = self.chat_canvas.create_window(
            0, 0, window=self.bubbles, anchor="nw")
        self.bubbles.bind("<Configure>",
                          lambda e: self.chat_canvas.configure(
                              scrollregion=self.chat_canvas.bbox("all")))
        self.chat_canvas.bind("<Configure>",
                              lambda e: self.chat_canvas.itemconfigure(
                                  self._bubble_win, width=e.width))
        self.chat_canvas.bind_all("<MouseWheel>",
                                  lambda e: self.chat_canvas.yview_scroll(
                                      int(-e.delta / 120), "units"))
        self._chat_texts = []        # 内容镜像（冒烟断言用）：逐条气泡文本

        # 输入状态固定在输入框上方，不混进对方的聊天气泡；它反映的是“本地正在
        # 等待回复／对方正在组织文字”，不是一条已发送消息。
        self.typing_box = tk.Frame(right, bg=P["bg"])
        self.typing_box.pack(fill=tk.X, padx=20, pady=(4, 0))
        self.typing_status_var = tk.StringVar(value="")
        self.typing_status = tk.Label(self.typing_box, textvariable=self.typing_status_var,
                                      bg=P["bg"], fg=P["sub"], anchor="w",
                                      font=("微软雅黑", 9), height=1)
        self.typing_status.pack(fill=tk.X)
        # 流式草稿是内部可撤销信号，不是用户可见对白。这里固定留一行只为
        # 避免输入区在状态切换时跳动，绝不能把“角色将要说的话”泄露出来。
        self.typing_draft_var = tk.StringVar(value="")
        self.typing_draft = tk.Label(self.typing_box, textvariable=self.typing_draft_var,
                                     bg=P["bg"], fg=P["sub"], anchor="w", justify=tk.LEFT,
                                     font=("微软雅黑", 9), wraplength=720, height=1)
        # 固定占一行：流式第一句到达、取消、重连或定稿时都只改文字，不重排
        # 输入框和发送按钮，避免发送区上下跳动/闪烁。
        self.typing_draft.pack(fill=tk.X, pady=(1, 0))
        self._typing_widget = self.typing_status

        bottom = ttk.Frame(right)
        bottom.pack(fill=tk.X, padx=16, pady=(4, 14))
        self.input_entry = ttk.Entry(bottom, font=("微软雅黑", 11))
        self.input_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        self.input_entry.bind("<Return>", self._send)
        self.send_btn = ttk.Button(bottom, text="发送", style="Primary.TButton",
                                   command=self._send)
        self.send_btn.pack(side=tk.RIGHT)

    # ------------------------------------------------------------------
    # 加载页（登录瞬间：追上每个角色的时间）
    # ------------------------------------------------------------------
    def _build_loading(self):
        self.loading_frame = tk.Frame(self.root, bg=P["pink_soft"])
        self.loading_title = tk.Label(self.loading_frame, text="正在同步互动时间线…",
                                      bg=P["pink_soft"], fg=P["pink_dark"],
                                      font=("微软雅黑", 14, "bold"))
        self.loading_title.place(relx=0.5, rely=0.47, anchor="center")
        self.loading_sub = tk.Label(self.loading_frame, text="", bg=P["pink_soft"],
                                    fg=P["sub"], font=("微软雅黑", 10))
        self.loading_sub.place(relx=0.5, rely=0.54, anchor="center")
        self.loading_bar = ttk.Progressbar(
            self.loading_frame, orient=tk.HORIZONTAL, length=320, mode="determinate",
            maximum=100, variable=self._loading_progress,
            style="Loading.Horizontal.TProgressbar")
        self.loading_bar.place(relx=0.5, rely=0.60, anchor="center")
        self.loading_spin = tk.Label(self.loading_frame, text="|", bg=P["pink_soft"],
                                     fg=P["pink"], font=("微软雅黑", 14, "bold"))
        self.loading_spin.place(relx=0.5, rely=0.67, anchor="center")

    def _show_loading(self, on):
        if on:
            if not self.loading_frame.place_info():
                self._loading_progress.set(0.0)
                self._loading_target = 8.0
                self.loading_sub.config(text="正在准备会话…")
            self.loading_frame.place(relx=0, rely=0, relwidth=1, relheight=1)
            self._tick_loading_spin()
            self._tick_loading_progress()
        else:
            self.loading_frame.place_forget()

    def _set_loading_stage(self, text, progress):
        """加载页的阶段反馈：百分比只推进到已完成的真实步骤，不假装模型已返回。"""
        if not self.loading_frame.place_info():
            return
        self.loading_sub.config(text=str(text))
        self._loading_target = max(self._loading_target, min(100.0, float(progress)))

    def _tick_loading_progress(self):
        """平滑推进加载感知；模型等待期缓慢爬行但永远不提前满格。"""
        if not self.loading_frame.place_info():
            self._loading_progress_running = False
            return
        if self._loading_progress_running:
            return
        self._loading_progress_running = True

        def tick():
            if not self.loading_frame.place_info():
                self._loading_progress_running = False
                return
            current = float(self._loading_progress.get())
            if current < self._loading_target:
                self._loading_progress.set(min(self._loading_target, current + 1.5))
            elif 70.0 <= current < 92.0 and self._loading_target < 96.0:
                # 模型首 token／生成期没有可可靠量化的百分比；停死在 70% 比缓慢
                # 接近“快完成了”更焦虑。这里仅做等待感知动画，92% 是硬上限，
                # 真正收到 replay 或结算完成才由真实事件推进到 96/100。
                self._loading_progress.set(min(92.0, current + 0.08))
            self.root.after(70, tick)

        tick()

    def _post_ui_event(self, kind, payload=None):
        """供任意后台线程安全投递；这里绝不调用 Tk。"""
        if not self._closed:
            self._ui_events.put((kind, payload))

    def _drain_ui_events(self):
        """唯一的后台→Tk 桥：在主线程消费 Actor 和启动 worker 的输出。"""
        if self._closed:
            return
        try:
            while True:
                kind, payload = self._ui_events.get_nowait()
                if kind == "actor":
                    cid, output_kind, output = payload
                    self._on_actor_output(cid, output_kind, output)
                elif kind == "actor_telemetry":
                    cid, telemetry = payload
                    self._on_actor_telemetry(cid, telemetry)
                elif kind == "startup_error":
                    self._show_startup_error(str(payload))
                elif kind == "loading_stage":
                    stage = payload or {}
                    self._set_loading_stage(stage.get("text", "正在加载…"),
                                            stage.get("progress", 0))
                elif kind == "bootstrap_done":
                    self._finish_actor_bootstrap()
                elif kind == "callback":
                    callback, result, error = payload
                    callback(result, error)
        except queue.Empty:
            pass
        finally:
            if not self._closed:
                self.root.after(25, self._drain_ui_events)

    def _tick_loading_spin(self):
        """加载页旋转符（加载页不再呆呆静止）。"""
        if not self.loading_frame.place_info():
            return
        spins = ["|", "/", "—", "\\"]
        i = getattr(self, "_spin_i", 0) + 1
        self._spin_i = i
        self.loading_spin.config(text=spins[i % 4])
        self.root.after(200, self._tick_loading_spin)

    # ------------------------------------------------------------------
    # 登录页（只做真实连接检查；进入后由 Actor 追平时间）
    # ------------------------------------------------------------------
    def _build_login(self):
        self.login_frame = tk.Frame(self.root, bg=P["pink_soft"])
        tk.Label(self.login_frame, text="TIDE", bg=P["pink_soft"],
                 fg=P["pink_dark"], font=("微软雅黑", 24, "bold")).place(
            relx=0.5, rely=0.30, anchor="center")
        self.login_sub = tk.Label(self.login_frame, text="", bg=P["pink_soft"],
                                  fg=P["sub"], font=("微软雅黑", 11))
        self.login_sub.place(relx=0.5, rely=0.42, anchor="center")
        self.key_entry = tk.Entry(self.login_frame, show="•", width=42,
                                  font=("微软雅黑", 10), justify="center",
                                  bg=P["panel"], fg=P["ink"], relief="flat",
                                  highlightthickness=1,
                                  highlightbackground=P["line"])
        self.key_entry.place(relx=0.5, rely=0.51, anchor="center")
        self.key_hint = tk.Label(self.login_frame, text="", bg=P["pink_soft"],
                                 fg=P["sub"], font=("微软雅黑", 9))
        self.key_hint.place(relx=0.5, rely=0.56, anchor="center")
        self.login_status = tk.Label(self.login_frame, text="", bg=P["pink_soft"],
                                     fg=P["pink_dark"], font=("微软雅黑", 10))
        self.login_status.place(relx=0.5, rely=0.62, anchor="center")
        self.login_btn = tk.Button(self.login_frame, text="进入会话",
                                   bg=P["pink"], fg="white", relief="flat",
                                   font=("微软雅黑", 13, "bold"), padx=30, pady=10,
                                   command=self._login)
        self.login_btn.place(relx=0.5, rely=0.70, anchor="center")

    def _char_count(self):
        """角色数：优先公会扫描；公会未建（等密钥）时直接扫 data/ 目录。"""
        if self.guild is not None:
            try:
                self.guild.discover()
                return len(self.guild.char_ids)
            except Exception:
                pass
        return len(scan_live_sessions())

    def _show_login(self, on):
        if on:
            n = self._char_count()
            self.login_sub.config(
                text=f"{n} 个角色在等你——看看她们昨晚说了什么" if n
                else "还没有角色，进入后点 ＋ 创建第一个")
            demo = demo_mode_enabled()
            has_env = bool(parse_api_keys(os.environ))
            self.key_hint.config(
                text=("（离线演示模式：不调用模型，可直接进入）" if demo
                      else ("（已检测到 .env 密钥，可留空直接进入）" if has_env
                            else "（粘贴你的 API 密钥后进入）")))
            self.key_entry.configure(state="disabled" if demo else "normal")
            self.login_frame.place(relx=0, rely=0, relwidth=1, relheight=1)
        else:
            self.login_frame.place_forget()

    def _set_login_status(self, text):
        self.login_status.config(text=text)

    def _save_key(self, key):
        """新密钥存入 .env 的 API_KEYS（保序去重），并注入当前进程环境。"""
        env_path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), ".env")
        keys = []
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
        except FileNotFoundError:
            lines = []
        for i, line in enumerate(lines):
            if line.startswith("API_KEYS="):
                keys = [k.strip() for k in line.split("=", 1)[1].split(",") if k.strip()]
                del lines[i]
                break
        if key not in keys:
            keys.insert(0, key)          # 新 key 放最前（优先用）
        lines.append("API_KEYS=" + ",".join(keys))
        with open(env_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.environ["API_KEYS"] = ",".join(keys)

    def _login(self):
        """连接模型并建立 Actor 会话，不以固定演出时间伪装启动进度。"""
        if not self.async_startup:
            self._enter_room()          # 冒烟/同步路径：仍使用 Actor
            return
        self.login_btn.config(state="disabled")
        self._gen_done = False
        self._show_login(False)
        self._show_loading(True)
        self._set_loading_stage("正在验证模型连接…", 12)
        key = self.key_entry.get().strip()
        threading.Thread(target=self._prepare_live_room,
                         args=(key,), daemon=True).start()

    def _ping_once(self, key):
        """真实一次最小调用：密钥 + 网络一步验完（结果 = 三个阶段的事实依据）。"""
        if demo_mode_enabled():
            return  # 离线演示必须零网络、零密钥。
        import openai
        keys = [key] if key else parse_api_keys(os.environ)
        if not keys:
            raise RuntimeError("未配置 API 密钥，请在上方粘贴后重试")
        client = openai.OpenAI(api_key=keys[0],
                               base_url=os.getenv("BASE_URL", "https://api.deepseek.com"),
                               timeout=10)
        client.chat.completions.create(
            model=os.getenv("MODEL_NAME", "deepseek-v4-pro"),
            messages=[{"role": "user", "content": "ping"}], max_tokens=1)

    def _prepare_live_room(self, key):
        """后台：真实检查、建公会并把首次追平投递给角色 Actor。

        首屏进入不再等待所有角色的离线 catch-up；catch-up 仍在各自 Actor 内按正式
        时间语义结算，完成后以 normal output/replay 回到 UI。加载页只是连接/首屏
        体验，不能成为墙钟或消息资格的门。
        """
        try:
            self._post_ui_event("loading_stage", {
                "text": "正在验证模型连接…", "progress": 12})
            self._ping_once(key)
            self._post_ui_event("loading_stage", {
                "text": "连接正常，正在载入本地会话…", "progress": 30})
            if key and not demo_mode_enabled():
                self._save_key(key)
        except Exception as e:  # noqa: BLE001
            error = str(e)[:60]
            self._post_ui_event("startup_error", error)
            return
        try:
            if self._guild_factory is not None and self.guild is None:
                self.guild = self._guild_factory()
            self.guild.discover()
            self._post_ui_event("loading_stage", {
                "text": "会话已载入，正在追平互动时间线…", "progress": 52})
            # T7-1：每个角色一个 Actor（inbox 串行 + 动态 tick），回放改走事件
            from ops.actor import Actor
            for cid, actor in list(self.actors.items()):
                self._stop_attached_actor(cid, actor)
            self.actors = {}
            self._catch_up_done = set()
            for cid, r in self.guild.runners.items():
                self._attach_actor(cid, r, startup_catch_up=True)
                # 登录首轮回放 = 向每个 Actor 投递 catch_up 事件（tick 也会追平，幂等）
                self.actors[cid].post("catch_up", {
                    "now_ms": self._runtime_now_ms(cid), "startup_stream": True})
            if self.actors:
                self._post_ui_event("loading_stage", {
                    "text": "已读取会话末页，后台继续整理互动时间线…", "progress": 70})
            # 已投递的 catch-up 继续在 Actor 中自行结算，但它不再是桌面可进入聊天的
            # 前置条件。新会话的空 Store 同样是一个已确认的空首屏。
            self._gen_done = True
            self._post_ui_event("bootstrap_done")
        except Exception as e:  # noqa: BLE001
            error = "启动失败：" + str(e)[:50]
            self._post_ui_event("startup_error", error)

    def _show_startup_error(self, error):
        """后台连接失败后的唯一 UI 收口；不留下半启动的加载页。"""
        self._show_loading(False)
        self._show_login(True)
        self.login_btn.config(state="normal")
        self._set_login_status(f"❌ {error}")

    def _on_actor_output(self, cid, kind, payload):
        """Actor 事件输出 → 主线程分发（T7-1：GUI 只订阅，不再直接摸 runner）。"""
        if kind == "reply_result":
            self._on_reply(cid, payload, None)
        elif kind == "reply_cancelled":
            self._on_reply_cancelled(cid, payload)
        elif kind == "turn_prepared":
            self._on_turn_prepared(cid, payload)
        elif kind == "reply_draft":
            self._on_reply_draft(cid, payload)
        elif kind == "reply_failed":
            self._on_reply(cid, None, RuntimeError(payload.get("error", "回复生成失败")))
        elif kind == "replay_msg":
            self._on_replay_msg(cid, payload)
        elif kind == "released":
            self._on_released(cid, payload)
        elif kind == "catch_up_done":
            # Actor 的 replay_msg 输出先于 done 进入同一个 UI 队列；此刻所有本次
            # 回放 action 都已被登记到 pending，之后仍由 reveal queue 逐条放行。
            self._startup_replay_gate.discard(cid)
            self._catch_up_done.add(cid)
            total = len(self.guild.char_ids) if self.guild else 1
            done = len(self._catch_up_done)
            self._set_loading_stage(f"互动时间线已同步（{done}/{total}）…",
                                    70 + 25 * done / max(1, total))
            # 首屏早已在 bootstrap_done 进入；这里只保留进度/诊断收束。catch-up
            # 绝不能因较晚完成而重新触发加载、重置 UI，或以 UI 时刻重算墙钟。
        elif kind == "npc_msg":
            self._on_followup(cid, payload)      # 追发到达：她顿了顿，又发一条
        elif kind == "autonomous_started":
            self._on_autonomous_started(cid, payload)
        elif kind == "autonomous_cancelled":
            self._on_autonomous_cancelled(cid, payload)
        elif kind == "preference_done":
            if payload.get("ok"):
                if cid == self.active_char and not payload.get("skipped"):
                    self._sys("已记住你的陪伴偏好 🌸")
            else:
                if cid == self.active_char:
                    self._sys("偏好没听清，再点 💌 偏好 说一次吧")
        elif kind == "feedback_recorded" and cid == self.active_char:
            if payload.get("ok"):
                attribution = payload.get("attribution_confidence", "none")
                suffix = "已关联上一条回复" if attribution == "high" else "未强行关联到单一回复"
                self._sys(f"反馈已记录（{suffix}），不会自动改参数")
            else:
                self._sys("反馈记录失败，请稍后重试")
        elif kind == "cadence_feedback_recorded" and cid == self.active_char:
            if payload.get("ok"):
                self._sys("节奏反馈已记录，后续只用于人工标定，不会自动改参数")
            else:
                self._sys("节奏反馈记录失败，请稍后重试")
        elif kind == "demo_clock_advanced" and cid == self.active_char:
            self.busy[cid] = False
            self._stop_typing_rhythm(cid)
            self._update_send_state(cid)
            advanced = int(payload.get("advanced_by_ms", 0)) // 60_000
            self._sys(f"演示时间已推进 {advanced} 分钟；主动链路已按正式墙钟结算")
        elif kind == "task_failed" and cid == self.active_char:
            if payload.get("kind") == "advance_demo_clock":
                self.busy[cid] = False
                self._stop_typing_rhythm(cid)
                self._update_send_state(cid)
                self._sys("演示时间推进失败：" + str(payload.get("error", "未知错误")))
            elif payload.get("kind") == "release_pending":
                # 投递合同失败也必须关闭纯展示输入态；pending 仍由后端保留审计，
                # 但界面不能伪装成角色会无限输入。
                self._on_reply(cid, None, RuntimeError("回复未能按时间线投递"))

    def _on_actor_telemetry(self, cid, payload):
        """保存实际 Actor tick 的展示快照；它不是第二条状态/调度总线。"""
        data = dict(payload or {})
        if not self._is_demo_char(cid):
            return
        if data.get("kind") == "opening_state_transition":
            try:
                ts = int(data.get("ts"))
            except (TypeError, ValueError):
                return
            row = {"ts": ts, "event": str(data.get("event") or "state"),
                   "opening": dict(data.get("opening") or {})}
            trace = self._demo_opening_trace.setdefault(cid, [])
            trace.append(row)
            del trace[:-36]
            return
        if data.get("kind") != "wall_clock_tick":
            return
        try:
            ts = int(data.get("ts"))
        except (TypeError, ValueError):
            return
        self._demo_tick_views[cid] = {
            "ts": ts,
            "state": copy.deepcopy(data.get("state")) if isinstance(data.get("state"), dict) else {},
            "clock_plan": data.get("clock_plan") if isinstance(data.get("clock_plan"), dict) else None,
            "tick": data.get("tick") if isinstance(data.get("tick"), dict) else {},
        }
        history = self._demo_tick_history.setdefault(cid, [])
        point = {"ts": ts, "state": copy.deepcopy(self._demo_tick_views[cid]["state"])}
        if history and int(history[-1].get("ts", -1)) == ts:
            history[-1] = point
        else:
            history.append(point)
        del history[:-32]

    @staticmethod
    def _npc_visual_key(message):
        """稳定标识一条已提交输出，供 UI 暂时隐藏/排队，不参与业务身份。"""
        action_id = message.get("action_id") if isinstance(message, dict) else None
        if action_id:
            return ("action", str(action_id))
        data = message if isinstance(message, dict) else {}
        return ("row", int(data.get("ts") or 0), str(data.get("source") or ""),
                str(data.get("reply") or data.get("text") or ""))

    def _has_active_npc_visual(self, cid):
        """是否已有已提交 NPC 文本正在等待/分段播放（仅 UI 局部状态）。"""
        return bool(cid in self.hidden_npc or cid in self._reveal_text or cid in self._partial
                    or cid in self._pending_visual_reveal)

    def _is_pending_visual_npc_message(self, cid, message):
        """该条已落盘 NPC 消息是否仍在 Tk 的揭示前空窗中。

        Store 的“已提交”不能直接等同于 UI 的“已经说出口”。这一判断是纯展示层
        的读取闸；消息时间戳、Actor receipt 和后续墙钟始终以 Store 为准。
        """
        pending_ts = getattr(self, "_pending_visual_ts", {}).get(cid)
        return bool(message.get("role") == "npc" and pending_ts is not None
                    and message.get("ts") == pending_ts)

    def _is_queued_npc_visual(self, cid, message):
        return self._npc_visual_key(message) in self._queued_npc_visual_keys.get(cid, set())

    def _queue_committed_npc_visual(self, cid, message):
        key = self._npc_visual_key(message)
        queued = self._queued_npc_visual_keys.setdefault(cid, set())
        if key in queued:
            return
        queued.add(key)
        self._committed_npc_visual_queue.append((cid, dict(message)))

    def _begin_followup_visual(self, cid, m):
        """播放一条已提交自主输出；只变更 Tk 局部展示状态。"""
        # UI 已实际消费 Actor 的 ``npc_msg``，但这只是展示回执：不触碰 Runner、
        # 公式状态或墙钟。通过 Actor 串行记账，才能把“已提交”与“前台已接收”
        # 明确区分，排查时不再把其中一段误说成另一段。
        actor = self.actors.get(cid) if isinstance(getattr(self, "actors", None), dict) else None
        if actor is not None:
            try:
                actor.acknowledge_presentation({
                    "action_id": m.get("action_id"),
                    "source": m.get("source"), "ts": m.get("ts"),
                    "stage": "visual_started",
                })
            except Exception:
                # 展示自身不能因审计不可用而阻断正式已提交消息的播放。
                pass
        self.busy[cid] = True
        self._autonomous_typing_task.pop(cid, None)
        ts = m.get("ts")
        self.hidden_npc[cid] = ts
        self._reveal_send_at[cid] = ts
        self._reveal_text[cid] = m.get("reply") or ""
        receipt = m.get("presentation_receipt") if isinstance(m, dict) else None
        presentation = None
        if isinstance(receipt, dict):
            presentation = presentation_snapshot(typing_presentation_plan({
                "presentation_receipt": receipt,
            }))
        self._start_typing_rhythm(cid, presentation=presentation)
        self._render_messages()
        # 纯展示节律：不投递 Actor 事件、不改变 F15/F16/tick。
        self.root.after(int(self._ui_rng.uniform(1000, 2000)),
                        lambda c=cid: self._reveal_followup(c))

    def _play_next_committed_npc_visual(self, cid):
        """播放同角色的下一条已提交消息；返回是否接管了当前视觉周期。"""
        for index, (queued_cid, message) in enumerate(self._committed_npc_visual_queue):
            if queued_cid != cid:
                continue
            self._committed_npc_visual_queue.pop(index)
            self._queued_npc_visual_keys.get(cid, set()).discard(self._npc_visual_key(message))
            self._begin_followup_visual(cid, message)
            return True
        return False

    def _on_followup(self, cid, m):
        """追发/自我延续消息到达：已提交后由 UI 自己排队播放。"""
        if cid != self.active_char:
            self.unread[cid] = self.unread.get(cid, 0) + 1
            self._refresh_list()
            return
        # Actor 可以在上一条的视觉揭示期间已经提交下一条。不能让它覆盖
        # ``hidden_npc/_partial``，更不能要求 Actor 等 UI；只把事实放入本地播放队列。
        if self._has_active_npc_visual(cid):
            self._queue_committed_npc_visual(cid, m)
            self._render_messages()
            return
        self._begin_followup_visual(cid, m)

    def _on_turn_prepared(self, cid, payload):
        """主 responder 尚在运行时的只读展示快照。

        这不是消息、不是草稿，也不触碰 Actor/Runner。若用户已离开当前会话则保留
        该角色自己的等待态；定稿结果回来后会以完整的 F03/F14/F16 receipt 覆盖它。
        """
        if not self.busy.get(cid):
            return
        self._start_typing_rhythm(cid, presentation=(payload or {}).get("presentation"))
        if cid == self.active_char:
            self._render_messages()

    def _on_reply_cancelled(self, cid, payload):
        """旧主回复被下一次 Enter 抢占后的 UI 收口。

        此事件只属于旧事务；新输入在 ``_send`` 时已经建立了新的等待态。因此绝不
        在 ``busy`` 时把 typing 整体关掉，否则迟到的取消回执会误杀新一轮回复。
        """
        del payload
        self._reply_drafts.pop(cid, None)
        if not self.busy.get(cid):
            self._stop_typing_rhythm(cid)
        if cid == self.active_char:
            self._sync_typing_indicator()
            self._render_messages()

    def _on_reply_draft(self, cid, payload):
        """接收正式链路给出的稳定对白前缀，作为可撤销的内部就绪信号。

        草稿不进入 ``_render_messages``、不写消息文件、也不显示为输入状态下面的
        “预告正文”。它只说明 responder 已产出稳定句段；完整回复通过既有 receipt
        路径到达后才获得可见消息资格。
        """
        if not self.busy.get(cid):
            return
        data = dict(payload or {})
        # Actor 正常只会转发 ReplyDraftExtractor 的输出；这里再做一次无状态
        # 展示净化，防止异常 transport/旧插件直接投递控制字符或半个 surrogate 到 Tk。
        text = sanitize_draft_text(data.get("text") or "")
        if data.get("reset") or not text:
            self._reply_drafts.pop(cid, None)
        else:
            self._reply_drafts[cid] = text
        if cid == self.active_char:
            # 已有稳定句段时可切到“正在组织”，但不暴露正文。展示不会反写任何
            # 时间或心理状态。
            self._typing_visible[cid] = True
            self._set_typing_color(P["sub"])
            self._sync_typing_indicator()

    def _on_autonomous_started(self, cid, payload):
        """Actor 已开始自主生成：启动等待态节律，不预写/预显任何 NPC 正文。"""
        self._autonomous_typing_task[cid] = payload.get("task_id")
        if cid != self.active_char:
            return
        # 主回复已正式 release、但仍在 UI 的 80ms/分句展示窗口内时，新的 Actor
        # 任务不能用自己的 typing cycle 抹掉它。任务仍在后端运行，完成后由上述
        # committed visual queue 接手。
        if self._has_active_npc_visual(cid):
            return
        receipt = payload.get("presentation_receipt") if isinstance(payload, dict) else None
        presentation = None
        if isinstance(receipt, dict):
            presentation = presentation_snapshot(typing_presentation_plan({
                "presentation_receipt": receipt,
            }))
        self._start_typing_rhythm(cid, presentation=presentation)
        self._render_messages()

    def _on_autonomous_cancelled(self, cid, payload):
        """被用户输入抢占或生成失败的自主草稿没有消息资格，撤掉其专属等待态。"""
        task_id = payload.get("task_id")
        if self._autonomous_typing_task.get(cid) != task_id:
            return
        self._autonomous_typing_task.pop(cid, None)
        # 用户自己已发送新输入时，等待态已由主回复接管，不能被旧任务清掉。
        if self.busy.get(cid):
            return
        self._stop_typing_rhythm(cid)
        if cid == self.active_char:
            self._render_messages()

    def _reveal_followup(self, cid):
        ts = self.hidden_npc.pop(cid, None)
        if ts is None:
            ts = self._reveal_send_at.pop(cid, None)
        else:
            self._reveal_send_at.pop(cid, None)
        text = self._reveal_text.pop(cid, "")
        self._run_reveal(cid, ts, text)

    def _enter_room(self):
        """同步/冒烟入口也使用与产品一致的 Actor 调度。"""
        try:
            if self._guild_factory is not None and self.guild is None:
                self.guild = self._guild_factory()
        except Exception as e:  # noqa: BLE001
            self._set_login_status(f"❌ 无法启动：{str(e)[:60]}")
            self.login_btn.config(state="normal")
            return
        self._show_login(False)
        self._start_actor_runtime()

    # ------------------------------------------------------------------
    # Actor 启动 / 发送
    # ------------------------------------------------------------------
    def _stop_attached_actor(self, cid, actor):
        detach = getattr(
            self.guild, "detach_formal_recipient_actor", None)
        try:
            if callable(detach):
                detach(cid, actor)
        finally:
            actor.stop()

    def _attach_actor(self, cid, runner, *, startup_catch_up=False):
        """将一个已注册角色接到 UI 的唯一 Actor 输出桥。"""
        from ops.actor import Actor
        previous = self.actors.pop(cid, None)
        if previous is not None:
            self._stop_attached_actor(cid, previous)
        if startup_catch_up:
            try:
                baseline = {
                    str(row.get("action_id"))
                    for row in runner.store.read_messages()
                    if row.get("source") == "replay" and row.get("action_id")
                }
            except Exception:
                baseline = set()
            self._startup_replay_gate.add(cid)
            self._startup_replay_baseline[cid] = baseline
            self._startup_replay_pending[cid] = set()
            self._startup_replay_visible[cid] = set(baseline)
        formal_handler = None
        read_formal_handler = getattr(
            self.guild, "formal_required_reply_handler", None)
        if callable(read_formal_handler):
            formal_handler = read_formal_handler(cid)
        from interaction_extensions.preference_calibration.actor_commands import (
            PreferenceCalibrationActorCommands,
        )
        actor = Actor(
            runner,
            on_output=lambda kind, payload, c=cid: self._post_ui_event(
                "actor", (c, kind, payload)),
            on_telemetry=lambda payload, c=cid: self._post_ui_event(
                "actor_telemetry", (c, payload)),
            hold_clock_until_startup_catch_up=startup_catch_up,
            formal_required_reply_handler=formal_handler,
            command_extensions=(PreferenceCalibrationActorCommands(),))
        self.actors[cid] = actor
        attach_formal_recipient = getattr(
            self.guild, "attach_formal_recipient_actor", None)
        if callable(attach_formal_recipient):
            try:
                attach_formal_recipient(cid, actor)
            except Exception:
                actor.stop()
                self.actors.pop(cid, None)
                raise
        return actor

    def _start_actor_runtime(self):
        """建立角色 Actor 并投递首次追平；首屏不等待其全部完成。"""
        self.guild.discover()
        self._catch_up_done = set()
        self._gen_done = False
        self._show_loading(True)
        self.loading_done = False
        self._progressive = True
        if not self.guild.char_ids:
            self._gen_done = True
            self._finish_actor_bootstrap()
            return
        from ops.actor import Actor
        for cid, actor in list(self.actors.items()):
            self._stop_attached_actor(cid, actor)
        self.actors = {}
        for cid in self.guild.char_ids:
            runner = self.guild.runners[cid]
            self._attach_actor(cid, runner, startup_catch_up=True)
            self.actors[cid].post("catch_up", {
                "now_ms": self._runtime_now_ms(cid), "startup_stream": True})
        # 只要会话 descriptor + 尾页已可读（空会话也明确可读），桌面即可进入。
        # 后台 catch-up 仍是 Actor 的正式墙钟事务，而不是 UI 同步循环。
        self._gen_done = True
        self._finish_actor_bootstrap()

    def _on_replay_msg(self, cid, m):
        """回放消息逐条揭示：第一条到达即离开加载页进聊天室；后续按固定节奏冒出
        （并行生成后同时到达，用 reveal_gap_ms 排队演出"接二连三"）。"""
        # 演示时钟推进后的 replay 已由真实 responder 完成；此时结束等待态，
        # 不能让“正在输入”在气泡已经实际到达后继续挂着。
        if cid == self.active_char:
            self._stop_typing_rhythm(cid)
        self._set_loading_stage("已收到新的互动，正在打开会话…", 96)
        action_id = m.get("action_id")
        if cid in self._startup_replay_gate and action_id:
            self._startup_replay_pending.setdefault(cid, set()).add(str(action_id))
        self._reveal_queue.append((cid, m))
        self._maybe_reveal_next()

    def _hide_unrevealed_startup_replay(self, cid, message):
        """判断一条已落盘 replay 是否还没有获得本次 UI 的可见资格。"""
        if message.get("source") != "replay":
            return False
        action_id = message.get("action_id")
        if not action_id:
            return False  # 旧档案缺 receipt 引用时保持历史展示兼容。
        action_id = str(action_id)
        visible = self._startup_replay_visible.get(cid, set())
        if action_id in visible:
            return False
        if action_id in self._startup_replay_pending.get(cid, set()):
            return True
        # worker 已正式写入下一条、但其 UI callback 尚未被主线程消费时，也必须
        # 暂时隐藏；catch_up_done 后 pending 集合会成为完整的逐条可见清单。
        return (cid in self._startup_replay_gate
                and action_id not in self._startup_replay_baseline.get(cid, set()))

    def _maybe_reveal_next(self):
        if self._revealing or not self._reveal_queue:
            return
        self._revealing = True
        cid, m = self._reveal_queue.pop(0)
        action_id = m.get("action_id")
        if action_id:
            self._startup_replay_visible.setdefault(cid, set()).add(str(action_id))
            self._startup_replay_pending.setdefault(cid, set()).discard(str(action_id))
        if not getattr(self, "loading_done", False):
            self._show_loading(False)
            self.loading_done = True
            self._refresh_list()
            if self.guild.char_ids:
                self._select(self.guild.char_ids[0])
        if cid == self.active_char:
            self._render_messages()                      # 含刚回放的这条（时间分隔正确）
        else:
            self.unread[cid] = self.unread.get(cid, 0) + 1
            self._refresh_list()
        self.root.after(UI["reveal_gap_ms"], self._next_reveal)

    def _next_reveal(self):
        self._revealing = False
        self._maybe_reveal_next()

    def _finish_actor_bootstrap(self):
        """首次 Actor 追平已结算且没有待揭示消息时，安全地进入聊天界面。"""
        if not self._has_attachable_conversation():
            # 正式 Store 不会落盘 draft/typing；若本地档案异常到连最近完整消息也
            # 无法证明，保持加载页并等待 Actor 的正式 replay，而不是由 UI 造一条
            # 消息或擅自触发 catch-up/tick。
            return
        self._loading_progress.set(100.0)
        self._show_loading(False)
        if getattr(self, "loading_done", False):
            return
        self.loading_done = True
        self._refresh_list()
        if self.guild and self.guild.char_ids and self.active_char is None:
            self._select(self.guild.char_ids[0])

    def _has_attachable_conversation(self):
        """只读检查首屏资格；绝不把历史分页变成 Actor/墙钟操作。

        新产品 direct 会话有 immutable provision，因此可按 descriptor + 最近完整
        message 判断；旧兼容会话没有此 identity 锚，不在 read path 中补建，保持已有
        产品可进入性。空的新会话或只有背景种子的会话是创建事务已明确完成的首屏。
        """
        if not self.guild or not self.guild.char_ids:
            return True
        load_provision = getattr(self.guild, "load_default_direct_provision", None)
        for cid in self.guild.char_ids:
            runner = self.guild.runners.get(cid)
            if runner is None:
                continue
            if not callable(load_provision):
                return True  # 兼容测试 Guild / legacy host：不在 UI 偷造 identity。
            provision = load_provision(cid)
            if provision is None:
                return True  # legacy profile 同样只能由显式迁移后才获得新 identity。
            tail = runner.store.read_recent_message_window(1)
            rows = tail["records"]
            # 完全空的新 Store、或只包含演示背景种子的开局，均没有未知的互动短尾；
            # 不能为了等待 NPC 消息而把创建后的会话锁在 loading 页。
            # 普通未知历史仍必须有一条最新 committed 气泡才可 attach；但 competition
            # demo 的 prelude 是创建时一次性写入的显式背景。若最新尾页仍全是该类
            # seed，说明尚无后续互动（后续 interaction 必定位于 seed 之后），可作为
            # 已确认的空互动首屏进入，不能因此扫描 archive 或由 UI 造消息。
            demo_background_only = (
                is_demo_profile(runner.profile)
                and bool(rows)
                and all(is_non_dialogue_seed(row) for row in rows))
            confirmed_non_interaction = (
                (not tail["has_older"] and (
                    not rows or all(is_non_dialogue_seed(row) for row in rows)))
                or demo_background_only)
            snapshot = build_conversation_entry_snapshot(
                provision.session.conversation,
                rows,
                has_older_history=tail["has_older"],
                history_complete=confirmed_non_interaction,
            )
            if snapshot["ready"]:
                return True
        return False

    def _refresh_list(self):
        for w in self.char_widgets.values():
            w["frame"].destroy()
        self.char_widgets.clear()
        if not self.guild.char_ids:
            self.empty_label.pack(pady=24)
            return
        self.empty_label.pack_forget()
        for cid in self.guild.char_ids:
            runner = self.guild.runners[cid]
            msgs = [row for row in runner.store.read_recent_messages(1)
                    if not is_non_dialogue_seed(row)]
            preview = msgs[-1]["text"][:14] if msgs else "（还没有消息）"
            un = self.unread.get(cid, 0)
            fr = tk.Frame(self.char_list, bg=P["panel"], pady=8, padx=12)
            fr.pack(fill=tk.X)
            for w in (fr,):
                w.bind("<Button-1>", lambda e, c=cid: self._select(c))
            name = tk.Label(fr, text=cid, bg=P["panel"], fg=P["ink"],
                            font=("微软雅黑", 11, "bold"), anchor="w")
            name.pack(fill=tk.X)
            name.bind("<Button-1>", lambda e, c=cid: self._select(c))
            pv = tk.Label(fr, text=preview, bg=P["panel"], fg=P["sub"],
                          font=("微软雅黑", 9), anchor="w")
            pv.pack(fill=tk.X)
            pv.bind("<Button-1>", lambda e, c=cid: self._select(c))
            badge = None
            if un:
                badge = tk.Label(fr, text=str(un), bg=P["pink"], fg="white",
                                 font=("微软雅黑", 9, "bold"), width=2)
                badge.place(relx=0.79, rely=0.15)
            # 可见性操作而非删档：点击后只写 profile.ui_hidden，并停止此窗口
            # 的 Actor，角色数据、记忆和 trace 都留在本地。
            delete = tk.Button(fr, text="删除", bg=P["panel"], fg=P["sub"],
                               relief="flat", bd=0, cursor="hand2",
                               font=("微软雅黑", 8), padx=2, pady=0,
                               command=lambda c=cid: self._hide_contact(c))
            delete.place(relx=0.89, rely=0.14)
            self.char_widgets[cid] = {"frame": fr, "name": name,
                                      "preview": pv, "badge": badge,
                                      "delete": delete}

    def _hide_contact(self, cid):
        """从 UI 隐藏一个角色，保留其全部本地互动档案。

        这是联系人层的“删除好友”，不是回档、遗忘或物理删除。下一次启动时
        Guild 也会依照同一个 profile 标记忽略它，避免被 discover 重新挂回。
        """
        if not self.guild or cid not in self.guild.runners:
            return
        runner = self.guild.runners[cid]
        now_ms = int(time.time() * 1000)
        profile = dict(runner.store.load_profile() or runner.profile or {})
        profile["ui_hidden"] = True
        profile["ui_hidden_at"] = now_ms
        runner.store.save_profile(profile)
        runner.profile = profile
        runner.store.append_event({
            "kind": "ui_contact_hidden", "ts": now_ms,
            "detail": {"source": "contact_list"},
        })

        actor = self.actors.pop(cid, None)
        if actor is not None:
            self._stop_attached_actor(cid, actor)
        self.guild.unregister(cid)
        self.unread.pop(cid, None)
        self.busy.pop(cid, None)
        self.typing.pop(cid, None)
        self._typing_visible.pop(cid, None)
        self._typing_plans.pop(cid, None)
        self._last_typing_presentation.pop(cid, None)
        self._demo_tick_views.pop(cid, None)
        self._demo_tick_history.pop(cid, None)
        self._demo_opening_trace.pop(cid, None)
        self._reply_drafts.pop(cid, None)
        # 令已排队的 after 回调与已隐藏联系人失配，不能在联系人被移除后复活。
        self._typing_cycle[cid] = int(self._typing_cycle.get(cid, 0)) + 1
        self.pending_user.pop(cid, None)
        self.hidden_npc.pop(cid, None)
        self._partial.pop(cid, None)
        self._reveal_text.pop(cid, None)
        self._reveal_send_at.pop(cid, None)
        getattr(self, "_pending_visual_ts", {}).pop(cid, None)
        self._autonomous_typing_task.pop(cid, None)

        if self.active_char == cid:
            self.active_char = None
            self.active_label.config(text="")
            self.timeline_var.set("")
            self.typing_status_var.set("")
            self.typing_draft_var.set("")
            self._clear_chat()
            self._update_demo_controls(None)
        self._refresh_list()
        if self.guild.char_ids:
            self._select(self.guild.char_ids[0])
        else:
            self._update_send_state()

    def _select(self, cid):
        if self.active_char != cid:
            self._close_formal_diagnostics()
        self.active_char = cid
        self.history_limit.setdefault(cid, UI["history_initial_messages"])
        self.unread[cid] = 0
        self.active_label.config(text=cid)
        self._update_demo_controls(cid)
        self._update_timeline_label(cid)
        self._refresh_list()
        self._update_send_state()
        self._render_messages()

    def _is_demo_char(self, cid):
        runner = self.guild.runners.get(cid) if self.guild and cid else None
        return bool(runner and is_demo_profile(runner.profile))

    def _runtime_now_ms(self, cid):
        """只读取得该会话时钟；普通角色永远返回宿主墙钟。"""
        runner = self.guild.runners.get(cid) if self.guild and cid else None
        resolver = getattr(runner, "runtime_now_ms", None)
        host_now = int(time.time() * 1000)
        if callable(resolver):
            return int(resolver(host_now))
        return host_now

    def _update_demo_controls(self, cid):
        runner = self.guild.runners.get(cid) if self.guild and cid else None
        if callable(getattr(runner, "export_formal_diagnostic_snapshot", None)):
            self.formal_diagnostic_btn.pack(side=tk.RIGHT, padx=(0, 8))
        else:
            self.formal_diagnostic_btn.pack_forget()
            self._close_formal_diagnostics()
        if getattr(self, "_competition_tools_enabled", False) and self._is_demo_char(cid):
            if not self._realtime_demo_capture:
                self.demo_advance_btn.pack(side=tk.RIGHT, padx=(0, 8))
            else:
                self.demo_advance_btn.pack_forget()
            self.demo_observatory_btn.pack(side=tk.RIGHT, padx=(0, 8))
        else:
            self.demo_advance_btn.pack_forget()
            self.demo_observatory_btn.pack_forget()
            self._close_demo_observatory()

    def _close_formal_diagnostics(self):
        window = self._formal_diagnostic_window
        self._formal_diagnostic_window = None
        self._formal_diagnostic_cid = None
        if window is not None:
            window.close()

    def _open_formal_diagnostics(self):
        """Open the model-declared read-only diagnostic view."""
        cid = self.active_char
        runner = self.guild.runners.get(cid) if self.guild and cid else None
        provider = getattr(runner, "export_formal_diagnostic_snapshot", None)
        if not callable(provider):
            return
        window = self._formal_diagnostic_window
        if window is not None and window.exists():
            window.lift()
            return
        self._close_formal_diagnostics()
        self._formal_diagnostic_cid = cid
        self._formal_diagnostic_window = FormalDiagnosticWindow(
            self.root,
            title=f"{cid} · 模型诊断",
            snapshot_provider=provider,
            palette={
                "bg": P["bg"], "panel": P["panel"], "ink": P["ink"],
                "sub": P["sub"], "line": P["line"], "accent": P["pink"],
            },
        )

    def _open_trio_group_lab(self):
        """只读打开最近三 NPC 测试工件；不触碰现有直聊运行。"""
        try:
            from tools.participant_trio_group_probe import load_participant_trio_group_probe
            from tools.launch_participant_trio_group_lab import _read_receipts, DEFAULT_JOURNAL
            case = load_participant_trio_group_probe()
            # `_read_receipts` 的合同输入是 Path；不要在主 UI 再把它降成 str，
            # 否则真实点击路径会缺失 `read_text()` 并抛 AttributeError。
            artifact = Path("output") / "test_artifacts" / "trio_group_lab" / "latest" / "source_journal.json"
            journal = artifact if artifact.is_file() else DEFAULT_JOURNAL
            snapshot = build_trio_group_lab_snapshot(case, _read_receipts(journal))
            if artifact.is_file():
                snapshot["subtitle"] = "真实三 NPC 生命周期工件 · 只读已提交 source / F16"
            else:
                snapshot["subtitle"] = "尚未载入真实生命周期工件 · 当前为冻结已提交展示资产"
            TrioGroupLab(self.root, snapshot)
        except Exception as exc:  # noqa: BLE001 — 展示失败不能影响聊天 Actor
            # 只记录诊断，不向 UI 注入状态或改写任何运行时事实。
            traceback.print_exc()
            self._sys("三人实验台读取失败：%s：%s" % (type(exc).__name__, exc))

    def _advance_demo_clock(self):
        cid = self.active_char
        if not cid or not self._is_demo_char(cid) or self.busy.get(cid):
            return
        actor = self.actors.get(cid)
        if actor is None:
            self._sys("演示会话尚未就绪")
            return
        self.busy[cid] = True
        # +90 分钟会真实执行 catch_up；命中主动开口时 responder 仍要实际生成。
        # 先给用户真实等待反馈，不预写任何气泡。
        self._start_typing_rhythm(cid)
        self._update_send_state(cid)
        self._render_messages()
        actor.post("advance_demo_clock", {
            "delta_ms": 90 * 60 * 1000,
            "host_now_ms": int(time.time() * 1000),
        })

    def _update_timeline_label(self, cid):
        """只读显示会话的墙钟锚点；不把 UI 时间当成状态输入。"""
        runner = self.guild.runners.get(cid) if self.guild else None
        last_ts = (runner.state or {}).get("last_ts") if runner else None
        if last_ts is None:
            self.timeline_var.set("时间线已启动 · 等待首轮互动")
        else:
            self.timeline_var.set(f"时间线持续演化 · 上次互动 {_fmt_time(last_ts)}")

    def _open_session_overview(self):
        """打开本角色的只读状态快照，不读 prompt 或改变任何运行时状态。"""
        if not self.active_char or not self.guild:
            return
        runner = self.guild.runners.get(self.active_char)
        if runner is None:
            return
        summary = build_session_summary(runner.state, runner.store.read_messages())
        dlg = tk.Toplevel(self.root)
        dlg.title(f"{self.active_char} · 互动概览")
        dlg.configure(bg=P["bg"])
        dlg.geometry("430x430")
        dlg.resizable(False, False)

        tk.Label(dlg, text="互动概览", bg=P["bg"], fg=P["ink"],
                 font=("微软雅黑", 16, "bold")).pack(anchor=tk.W, padx=20, pady=(18, 2))
        last_ts = summary["last_ts"]
        subtitle = (f"本地只读快照 · 上次互动 {_fmt_time(last_ts)}" if last_ts else
                    "本地只读快照 · 尚未产生互动")
        tk.Label(dlg, text=subtitle, bg=P["bg"], fg=P["sub"],
                 font=("微软雅黑", 9)).pack(anchor=tk.W, padx=20)

        counts = summary["messages"]
        # ``field`` 是已结算词元的上下文/检索编译物；F08/F14/F15 不再读取它。
        # 不把它叫作“时间场”，避免 UI 把旧 archive 兼容键误说成当前数学驱动。
        context = (f"你 {counts['user']} 条  ·  TA {counts['npc']} 条  ·  "
                   f"动态上下文资源 {'已生成' if summary['field_active'] else '等待首轮事件'}")
        tk.Label(dlg, text=context, bg=P["pink_soft"], fg=P["pink_dark"],
                 font=("微软雅黑", 9, "bold"), padx=10, pady=6).pack(
                     fill=tk.X, padx=20, pady=(14, 10))

        body = tk.Frame(dlg, bg=P["bg"])
        body.pack(fill=tk.BOTH, expand=True, padx=20)
        for meter in summary["meters"]:
            row = tk.Frame(body, bg=P["bg"])
            row.pack(fill=tk.X, pady=5)
            tk.Label(row, text=meter["label"], width=10, anchor=tk.W,
                     bg=P["bg"], fg=P["ink"], font=("微软雅黑", 10)).pack(side=tk.LEFT)
            bar = tk.Canvas(row, width=235, height=9, bg=P["line"],
                            highlightthickness=0)
            bar.pack(side=tk.LEFT, padx=(6, 8))
            bar.create_rectangle(0, 0, int(235 * meter["bar"]), 9,
                                 fill=P["pink"], outline="")
            tk.Label(row, text=f"{meter['value']:.2f}", width=5, anchor=tk.E,
                     bg=P["bg"], fg=P["sub"], font=("微软雅黑", 9)).pack(side=tk.RIGHT)

        tk.Label(dlg, text="这些读数来自已结算的会话状态，仅用于理解变化，不会改变角色回复。",
                 bg=P["bg"], fg=P["sub"], justify=tk.LEFT, wraplength=380,
                 font=("微软雅黑", 9)).pack(anchor=tk.W, padx=20, pady=(4, 12))

    def _open_action_trace(self):
        """打开当前角色最近 action 的本地只读 receipt inspector。

        该窗不订阅 Actor、不发送模型请求，也不让用户借 UI 拼接一条不存在的因果链；
        所有内容均由 ``build_action_trace`` 以既有 action/turn/root ID 直接读取。
        """
        cid = self.active_char
        runner = self.guild.runners.get(cid) if cid and self.guild else None
        if runner is None:
            return
        action_ids = []
        for row in reversed(runner.store.read_messages()):
            if row.get("role") != "npc":
                continue
            action_id = str(row.get("action_id") or "").strip()
            if action_id and action_id not in action_ids:
                action_ids.append(action_id)
        if not action_ids:
            self._sys("尚无已提交的 NPC 输出可追溯")
            return

        dlg = tk.Toplevel(self.root)
        dlg.title(f"{cid} · 回执追溯")
        dlg.configure(bg=P["bg"])
        dlg.geometry("900x700")
        dlg.minsize(680, 500)
        tk.Label(dlg, text="Action 回执追溯", bg=P["bg"], fg=P["ink"],
                 font=("微软雅黑", 15, "bold")).pack(anchor=tk.W, padx=18, pady=(16, 2))
        tk.Label(dlg, text="只读：只按 action／turn／既有 root 引用联结；缺项会明确标出，绝不按时间猜补。",
                 bg=P["bg"], fg=P["sub"], font=("微软雅黑", 9)).pack(
                     anchor=tk.W, padx=18, pady=(0, 10))

        controls = tk.Frame(dlg, bg=P["bg"])
        controls.pack(fill=tk.X, padx=18, pady=(0, 8))
        tk.Label(controls, text="已发送 action", bg=P["bg"], fg=P["ink"],
                 font=("微软雅黑", 9)).pack(side=tk.LEFT)
        action_var = tk.StringVar(value=action_ids[0])
        selector = ttk.Combobox(controls, textvariable=action_var, values=action_ids,
                                state="readonly", width=44)
        selector.pack(side=tk.LEFT, padx=(8, 12))
        prompt_var = tk.BooleanVar(value=False)
        prompt_toggle = ttk.Checkbutton(
            controls, text="显示本机实际 prompt（含私密内容）", variable=prompt_var)
        prompt_toggle.pack(side=tk.LEFT)

        status_var = tk.StringVar(value="")
        tk.Label(dlg, textvariable=status_var, bg=P["pink_soft"], fg=P["pink_dark"],
                 anchor=tk.W, font=("微软雅黑", 9, "bold"), padx=10, pady=6).pack(
                     fill=tk.X, padx=18, pady=(0, 8))
        body = tk.Frame(dlg, bg=P["bg"])
        body.pack(fill=tk.BOTH, expand=True, padx=18, pady=(0, 16))
        scroll = ttk.Scrollbar(body)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        viewer = tk.Text(body, wrap=tk.NONE, bg=P["panel"], fg=P["ink"],
                         relief="flat", font=("Consolas", 9), yscrollcommand=scroll.set)
        viewer.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.configure(command=viewer.yview)

        def render(*_args):
            try:
                trace = build_action_trace(runner.store, action_var.get(),
                                           include_prompt=bool(prompt_var.get()))
                missing = [item["code"] for item in trace.get("diagnostics") or ()]
                status_var.set(
                    ("因果门禁通过" if trace["manifest"].get("passed") else "因果门禁未通过")
                    + " · 数学 span " + ("已找到" if trace["math"].get("available") else "缺失/未启用")
                    + (" · " + "、".join(missing) if missing else ""))
                rendered = json.dumps(trace, ensure_ascii=False, indent=2)
            except Exception as exc:  # noqa: BLE001 — inspector 失败也不能影响互动线程
                status_var.set("追溯读取失败：" + type(exc).__name__)
                rendered = "读取失败：" + repr(exc)
            viewer.configure(state=tk.NORMAL)
            viewer.delete("1.0", tk.END)
            viewer.insert("1.0", rendered)
            viewer.configure(state=tk.DISABLED)

        selector.bind("<<ComboboxSelected>>", render)
        prompt_toggle.configure(command=render)
        render()

    # ------------------------------------------------------------------
    # 比赛演示监测：只读 root state / memory 摘要 / wall-clock 计划
    # ------------------------------------------------------------------
    def _close_demo_observatory(self):
        """关闭/失效演示面板；只取消 UI 刷新，不触碰角色运行。"""
        self._demo_observatory_token += 1
        observer = self._demo_observatory
        self._demo_observatory = None
        window = observer.get("window") if isinstance(observer, dict) else None
        try:
            if window is not None and window.winfo_exists():
                window.destroy()
        except tk.TclError:
            pass

    @staticmethod
    def _set_demo_meter(canvas, value):
        """只改绘制宽度；输入值已经在纯投影层钳制到 0..1。"""
        width = int(canvas.cget("width"))
        canvas.coords("fill", 0, 0, int(width * float(value)), 9)

    @staticmethod
    def _draw_demo_trend(canvas, trend):
        """画两条连续根量轨迹，不把输出提交伪装成状态归零。

        纵轴按当前已提交点的共同真实范围缩放，使 tick 间的小幅变化可见；绝对
        数值仍由上方六条 root meter 给出。缩放只改变像素坐标，绝不重采样或插值。
        """
        canvas.delete("all")
        width, height = int(canvas.cget("width")), int(canvas.cget("height"))
        canvas.create_line(0, height - 1, width, height - 1, fill=P["line"], width=1)
        rows = list(trend or ())
        if len(rows) < 2:
            return
        step = width / max(1, len(rows) - 1)
        values = [max(0.0, min(1.0, float(row.get(key, 0.0))))
                  for row in rows for key in ("P_need", "V_rep")]
        low, high = min(values), max(values)
        padding = max(0.025, (high - low) * 0.18)
        low, high = max(0.0, low - padding), min(1.0, high + padding)
        if high - low < 0.05:
            center = (low + high) / 2.0
            low, high = max(0.0, center - 0.025), min(1.0, center + 0.025)
        span = max(1e-6, high - low)
        for key, color in (("P_need", P["pink"]), ("V_rep", P["blue"])):
            points = []
            for index, row in enumerate(rows):
                value = max(0.0, min(1.0, float(row.get(key, 0.0))))
                y = round((1.0 - (value - low) / span) * (height - 10) + 6)
                points.extend((round(index * step), y))
            canvas.create_line(*points, fill=color, width=2, smooth=True)
        canvas.create_text(width - 5, 6, text=f"{low:.2f}–{high:.2f}", anchor="ne",
                           fill=P["sub"], font=("微软雅黑", 7))

    @staticmethod
    def _draw_demo_urgency(canvas, urgency_view, reveal=1.0, marker_ts=None):
        """画 F15 的观察窗口和已走到的双曲线，不伪装成已发生的历史。

        淡虚线是正式状态向后的只读观察窗口；主色实线只画到演示时钟已抵达
        的位置。展示层因此不会把一张新快照一帧铺满画布。
        """
        canvas.delete("all")
        width, height = int(canvas.cget("width")), int(canvas.cget("height"))
        full_curve = list((urgency_view or {}).get("curve") or ())
        if len(full_curve) < 2:
            return
        # 每一次已结算状态更新后，曲线只在展示层由左向右显现；底层状态、F15
        # 资格和计划均不读取这个比例。避免“一帧完整曲线”看起来像预先塞进 UI。
        reveal = max(0.0, min(1.0, float(reveal)))
        visible_count = max(2, min(len(full_curve), int(math.ceil(len(full_curve) * reveal))))
        curve = full_curve[:visible_count]
        # x 轴始终使用完整窗口，不能跟随 reveal 片段重标；否则每一帧都会把
        # 已画片段拉伸满整张图，造成曲线“抢跑/闪现”的错觉。
        start_ts = int(full_curve[0].get("ts", 0))
        end_ts = int(full_curve[-1].get("ts", start_ts))
        visible_end_ts = int(curve[-1].get("ts", start_ts))
        span = max(1, end_ts - start_ts)

        def points_for(key, rows=None):
            points = []
            for row in (curve if rows is None else rows):
                value = max(0.0, min(1.0, float(row.get(key, 0.0))))
                x = round(width * (int(row.get("ts", start_ts)) - start_ts) / span)
                points.extend((x, round((1.0 - value) * (height - 4))))
            return points

        impulse_points = points_for("urgency")
        gate_points = points_for("gate")
        # 不使用 Tk 的 smooth 贝塞尔：它会在离散点之间过冲，造成余量未过零却
        # “视觉相交”的假象。这里的折线才严格对应每个 F15 observation point。
        # 虚线是未来观察窗口；实线只到演示时钟已经走到的位置。后者在真实
        # tick 到达后再缓动推进，绝不会倒灌到 F15/Runner。
        canvas.create_line(*impulse_points, fill=P["pink"], width=1, dash=(2, 3))
        canvas.create_line(*gate_points, fill=P["blue"], width=1, dash=(2, 3))
        active_until = start_ts if marker_ts is None else min(
            visible_end_ts, max(start_ts, int(marker_ts)))
        # tick 是离散的，但展示不必把两次 tick 之间冻成一根游标：在相邻的
        # 两个已知 observation point 间只作画面插值，让实线端点平滑向右延展。
        # 这份插值不写回 state，也不会被 F15 或 Actor 读取。
        active_curve = TideUI._f15_curve_until(curve, active_until)
        if len(active_curve) >= 2:
            for key, color in (("urgency", P["pink_dark"]), ("gate", P["blue"])):
                active_points = points_for(key, active_curve)
                canvas.create_line(*active_points, fill=color, width=2)
                x, y = active_points[-2:]
                canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill=color, outline="")
        # 首点是唯一已结算读数；其后的虚线是 C/X 经 F08 推进后的只读窗口。
        for points, color in ((impulse_points, P["pink_dark"]), (gate_points, P["blue"])):
            x, y = points[:2]
            canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill=color, outline="")
        previous = float(curve[0].get("margin", 0.0))
        for index, row in enumerate(curve[1:], 1):
            margin = float(row.get("margin", 0.0))
            if (previous < 0.0 <= margin) or (previous >= 0.0 > margin):
                x = round(width * (int(row.get("ts", start_ts)) - start_ts) / span)
                canvas.create_line(x, 0, x, height, fill=P["sub"], dash=(2, 3))
            previous = margin
    @staticmethod
    def _first_f15_crossing_ts(curve):
        """返回真实 margin 穿零的第一个离散观测点；没有则为 None。"""
        rows = list(curve or ())
        if len(rows) < 2:
            return None
        previous = float(rows[0].get("margin", 0.0) or 0.0)
        for row in rows[1:]:
            current = float(row.get("margin", 0.0) or 0.0)
            if previous < 0.0 <= current:
                return int(row.get("ts"))
            previous = current
        return None

    @staticmethod
    def _draw_demo_opening_trace(canvas, segments, progress=1.0, edge_reset=False):
        """绘制当前 receipt 段的两条真实 F15 线。

        用户 F03、主回复/F06 或主动输出/F06 都会开始一段新图；这只是把真实
        状态转换按因果顺序排开，绝不重置 F15 的用户锚。tick 才扩展当前段，计划
        点仅作越线／生成标记，不能制造一段不存在的未来曲线。
        """
        canvas.delete("all")
        width, height = int(canvas.cget("width")), int(canvas.cget("height"))
        all_segments = [list(segment) for segment in (segments or ()) if segment]
        if not all_segments:
            return
        rows = [dict(row) for row in all_segments[-1] if isinstance(row, dict)]
        # 计划仅说明当前已越线、Actor 已接管生成。它不改变 X，故不能额外制造
        # 一个折线点；真实 receipt 或下一 tick 才能推进两条线。
        curve_rows = [row for row in rows if not row.get("plan_marker")]
        if not curve_rows:
            curve_rows = rows[:1]
        # receipt 的数量不是时间。每一屏只呈现一段真实 P3→下一 observation
        # interval，因此 34 秒后的正式 tick 应抵达右缘；F16 新段会携带前一个
        # committed point 起笔，保持视觉连续但不合并因果状态。
        curve_rows, page_start_ms, page_end_ms, carried_boundary = opening_trace_time_page(
            all_segments)
        if not curve_rows:
            return
        progress = max(0.0, min(1.0, float(progress)))

        viewport = opening_trace_viewport_details(curve_rows)
        view_low, view_high = viewport["lower"], viewport["upper"]
        view_span = max(1e-9, view_high - view_low)
        main_top, main_bottom = 20, max(60, height - 87)
        delta_top, delta_bottom = main_bottom + 38, height - 13

        def value(row, key, local_view):
            raw = float(row.get(key, 0.0))
            local_span = max(1e-9, local_view["upper"] - local_view["lower"])
            return max(0.0, min(1.0, (raw - local_view["lower"]) / local_span))

        def point(item, key, local_view, top, bottom):
            if page_start_ms is None or page_end_ms is None or page_end_ms <= page_start_ms:
                x = 0
            else:
                ratio = (int(item.get("ts", page_start_ms)) - page_start_ms) / (page_end_ms - page_start_ms)
                x = round(width * max(0.0, min(1.0, ratio)))
            return (x, round((1.0 - value(item, key, local_view)) * (bottom - top) + top))

        def draw_grid(local_view, top, bottom, digits, *, show_labels=True):
            for tick in local_view["ticks"]:
                local_span = max(1e-9, local_view["upper"] - local_view["lower"])
                y = round((1.0 - (tick - local_view["lower"]) / local_span) * (bottom - top) + top)
                canvas.create_line(0, y, width, y, fill="#e3e8f4", dash=(1, 4))
                if show_labels:
                    canvas.create_text(width - 3, y, text=f"{tick:.{digits}f}", anchor="e",
                                       fill=P["sub"], font=("微软雅黑", 6))

        def draw_series(series_rows, local_view, top, bottom, *, short_marker=False):
            endpoints = {}
            for key, color in (("impulse", P["pink_dark"]), ("gate", P["blue"])):
                points = []
                completed = series_rows if len(series_rows) < 2 else series_rows[:-1]
                for row in completed:
                    points.extend(point(row, key, local_view, top, bottom))
                if len(series_rows) >= 2:
                    start_x, start_y = point(series_rows[-2], key, local_view, top, bottom)
                    end_x, end_y = point(series_rows[-1], key, local_view, top, bottom)
                    points.extend((round(start_x + (end_x - start_x) * progress),
                                   round(start_y + (end_y - start_y) * progress)))
                if len(points) >= 4:
                    canvas.create_line(*points, fill=color, width=2)
                if not points:
                    continue
                if len(series_rows) == 1 and short_marker:
                    _, y = points[-2:]
                    x = 8
                    canvas.create_line(x, y, x + 22, y, fill=color, width=2)
                    x += 22
                else:
                    x, y = points[-2:]
                canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill=color, outline="")
                endpoints[key] = (x, y)
            return endpoints

        # 原值图和变化图各自有共同纵轴。前者保留 I/Theta 的相对位置和 crossing；
        # 后者仅解释本因果页内的真实增减，不能被阅读为新的开口数值。
        main_digits = 6 if view_span < .01 else 3
        canvas.create_text(4, 4,
                           text=f"自动纵轴 {view_low:.{main_digits}f}–{view_high:.{main_digits}f}",
                           anchor="nw", fill=P["sub"], font=("微软雅黑", 7))
        draw_grid(viewport, main_top, main_bottom, main_digits)
        draw_series(curve_rows, viewport, main_top, main_bottom, short_marker=True)

        # 只显示实际 `opening_dispatch_committed` 所绑定的来源 tick。它既不是
        # 新 I/Theta 点，也不是 UI 预测；跨 F16 段时仍能说明“这条消息为什么有
        # 资格出现”，避免只见新段而误判为未越线就发送。
        dispatch_rows = [row for row in curve_rows if isinstance(row.get("opening_dispatch"), dict)]
        if dispatch_rows:
            dispatch_row = dispatch_rows[-1]
            dispatch = dispatch_row["opening_dispatch"]
            marker_x = point(dispatch_row, "impulse", viewport, main_top, main_bottom)[0]
            canvas.create_line(marker_x, main_top, marker_x, main_bottom,
                               fill=P["pink_dark"], dash=(2, 3))
            canvas.create_text(marker_x, main_top, text="F15 越线 → F16",
                               anchor="n", fill=P["pink_dark"], font=("微软雅黑", 7, "bold"))

        if viewport["flat"]:
            canvas.create_text(width - 4, 4, text="本页无已结算差异", anchor="ne",
                               fill=P["sub"], font=("微软雅黑", 7))

        delta_rows = opening_trace_delta_rows(curve_rows)
        delta_view = opening_trace_viewport_details(delta_rows, domain=None, flat_span=.0001)
        delta_span = delta_view["upper"] - delta_view["lower"]
        delta_digits = 6 if delta_span < .01 else 3
        canvas.create_text(4, main_bottom + 26,
                           text=(f"Δ 本段变化（相对首个 committed 点；不决定开口）  "
                                 f"{delta_view['lower']:.{delta_digits}f}–"
                                 f"{delta_view['upper']:.{delta_digits}f}"),
                           anchor="nw", fill=P["sub"], font=("微软雅黑", 7))
        draw_grid(delta_view, delta_top, delta_bottom, delta_digits, show_labels=False)
        draw_series(delta_rows, delta_view, delta_top, delta_bottom, short_marker=False)

        # 当前段的末端标签说明其因果状态。计划只标成“越线生成中”，不重开曲线。
        latest = rows[-1]
        label = {"input_committed": "用户输入已提交", "tick": "tick",
                 "opening_plan_reply": "已越线，生成中",
                 "opening_plan_tick": "已越线，生成中",
                 "opening_plan_expired": "到点复核：已回落",
                 # 直接回复是已提交输出边界：新段尚无后续真实 tick 时只会有一个
                 # 起点，必须明确这不是 UI reload、更不是已发送的 F15。
                 "reply_receipt": "F16 回复边界 · 等待真实 tick", "opening_receipt": "开口 receipt",
                 "share_receipt": "分享 receipt"}.get(str(latest.get("event")), "状态")
        latest_x = 34 if len(curve_rows) == 1 else point(curve_rows[-1], "impulse", viewport, main_top, main_bottom)[0]
        canvas.create_text(latest_x, main_bottom + 10, text=label, anchor="s",
                           fill=P["sub"], font=("微软雅黑", 7))
        if len(all_segments) > 1:
            segment_text = f"当前第 {len(all_segments)} 段"
            if carried_boundary:
                segment_text += " · 承接上一已提交点"
            canvas.create_text(width - 4, 4, text=segment_text,
                               anchor="ne", fill=P["sub"], font=("微软雅黑", 7))

    @staticmethod
    def _visual_tick_duration_seconds(interval_ms):
        """真实 tick 完成后，展示游标追上新位置的时长（只影响 UI）。"""
        interval_ms = max(1, int(interval_ms or 10_000))
        # 10 秒档约 2.8 秒：观众看得清变化，也不会把每次墙钟推进压缩成闪跳。
        return min(3.5, max(1.5, 0.8 + interval_ms / 5_000.0))

    @staticmethod
    def _hold_completed_tick_page(display_event, incoming_event, elapsed_s, duration_s):
        """是否暂留已结算 tick 的展示页，给 F16 边界一个可见的切换瞬间。

        这是纯展示延迟：F16 早已提交，消息也不被隐藏；函数既不改变 snapshot，
        也不改变 Actor 的下一次 deadline。它只避免同一次 GUI refresh 把“tick 到达
        右缘”与“输出边界新页”压成一帧，从而看起来像根状态重置。
        """
        return (str(display_event) == "tick"
                and str(incoming_event) in {"reply_receipt", "opening_receipt", "share_receipt"}
                and float(elapsed_s) < float(duration_s))

    @staticmethod
    def _f15_curve_until(curve, at_ms):
        """截取 F15 观察曲线到展示时钟，并在端点做纯展示插值。"""
        rows = [dict(row) for row in (curve or ()) if isinstance(row, dict)]
        if not rows:
            return []
        at = int(at_ms)
        first_ts = int(rows[0].get("ts", 0))
        if at <= first_ts:
            return [rows[0]]
        active = []
        for index, row in enumerate(rows):
            ts = int(row.get("ts", first_ts))
            if ts <= at:
                active.append(row)
                continue
            previous = active[-1]
            previous_ts = int(previous.get("ts", first_ts))
            span = max(1, ts - previous_ts)
            ratio = max(0.0, min(1.0, (at - previous_ts) / span))
            interpolated = {"ts": at}
            for key in ("urgency", "gate", "margin"):
                low = float(previous.get(key, 0.0))
                high = float(row.get(key, low))
                interpolated[key] = low + (high - low) * ratio
            active.append(interpolated)
            break
        return active

    @staticmethod
    def _format_demo_duration(ms):
        if ms is None:
            return "--"
        total = max(0, int(ms) // 1000)
        return f"{total // 60:02d}:{total % 60:02d}"

    def _open_demo_observatory(self):
        """打开一张演示专用的实时只读面板；普通会话没有该入口。"""
        cid = self.active_char
        if not (getattr(self, "_competition_tools_enabled", False) and self._is_demo_char(cid)):
            return
        current = self._demo_observatory
        try:
            if isinstance(current, dict) and current["window"].winfo_exists():
                current["window"].lift()
                current["window"].focus_force()
                return
        except (KeyError, tk.TclError):
            pass
        self._close_demo_observatory()

        dlg = tk.Toplevel(self.root)
        dlg.title(f"{cid} · 演示监测")
        dlg.configure(bg=P["bg"])
        dlg.geometry("500x850")
        dlg.resizable(False, False)
        tk.Label(dlg, text="TIDE · 互动状态监测", bg=P["bg"], fg=P["ink"],
                 font=("微软雅黑", 15, "bold")).pack(anchor=tk.W, padx=18, pady=(16, 2))
        tk.Label(dlg, text="只读显示：正式状态轨迹；不写回引擎、不提前生成消息",
                 bg=P["bg"], fg=P["sub"], font=("微软雅黑", 9)).pack(
                     anchor=tk.W, padx=18, pady=(0, 10))

        root_vars = tk.Frame(dlg, bg=P["bg"])
        root_vars.pack(fill=tk.X, padx=18)
        tk.Label(root_vars, text="连续根状态", bg=P["bg"], fg=P["pink_dark"],
                 font=("微软雅黑", 10, "bold")).pack(anchor=tk.W, pady=(0, 4))
        meter_widgets = {}
        for key in ("E_env", "L_self", "L_opponent", "P_need", "V_attr", "V_rep"):
            row = tk.Frame(root_vars, bg=P["bg"])
            row.pack(fill=tk.X, pady=2)
            label = tk.Label(row, width=10, anchor=tk.W, bg=P["bg"], fg=P["ink"],
                             font=("微软雅黑", 9))
            label.pack(side=tk.LEFT)
            bar = tk.Canvas(row, width=265, height=9, bg=P["line"], highlightthickness=0)
            bar.create_rectangle(0, 0, 0, 9, fill=P["pink"], outline="", tags="fill")
            bar.pack(side=tk.LEFT, padx=(5, 7))
            value = tk.Label(row, width=5, anchor=tk.E, bg=P["bg"], fg=P["sub"],
                             font=("微软雅黑", 9))
            value.pack(side=tk.RIGHT)
            meter_widgets[key] = (label, bar, value)

        field_row = tk.Frame(dlg, bg=P["pink_soft"])
        field_row.pack(fill=tk.X, padx=18, pady=(10, 6))
        field_var = tk.StringVar(value="互动势能 0.00 · 越线张力 0.00 · 动态爆发容量 0.00")
        tk.Label(field_row, textvariable=field_var, bg=P["pink_soft"], fg=P["pink_dark"],
                 font=("微软雅黑", 10, "bold"), padx=10, pady=6).pack(anchor=tk.W)

        urgency_var = tk.StringVar(value="统一开口曲线：等待首轮互动")
        tk.Label(dlg, textvariable=urgency_var, bg=P["bg"], fg=P["pink_dark"], anchor=tk.W,
                 font=("微软雅黑", 10, "bold")).pack(fill=tk.X, padx=18, pady=(10, 2))
        urgency_canvas = tk.Canvas(dlg, width=420, height=180, bg=P["panel"], highlightthickness=1,
                                   highlightbackground=P["line"])
        urgency_canvas.pack(padx=18, pady=(0, 2))
        tk.Label(dlg, text="紫 = 统一开口冲动 I_open · 蓝 = 动态门限 Θ_open · 每点均为已结算事件",
                 bg=P["bg"], fg=P["sub"],
                 font=("微软雅黑", 8)).pack(anchor=tk.W, padx=18)
        urgency_gate_var = tk.StringVar(value="资格：等待正式时间锚")
        tk.Label(dlg, textvariable=urgency_gate_var, bg=P["bg"], fg=P["sub"], justify=tk.LEFT,
                 anchor=tk.W, wraplength=455, font=("微软雅黑", 8)).pack(fill=tk.X, padx=18)
        opening_var = tk.StringVar(value="统一开口：等待首轮用户事件")
        tk.Label(dlg, textvariable=opening_var, bg=P["bg"], fg=P["sub"], justify=tk.LEFT,
                 anchor=tk.W, wraplength=455, font=("微软雅黑", 8)).pack(fill=tk.X, padx=18, pady=(2, 0))

        measurement_var = tk.StringVar(value="测量：等待首轮输入")
        memory_var = tk.StringVar(value="记忆：尚未形成可读摘要")
        tick_var = tk.StringVar(value="轮询：等待 Actor")
        delivery_var = tk.StringVar(value="投递：等待互动")
        receipt_var = tk.StringVar(value="最近提交：暂无")
        clock_var = tk.StringVar(value="墙钟：等待首轮互动")
        tk.Label(dlg, textvariable=measurement_var, bg=P["bg"], fg=P["sub"], anchor=tk.W,
                 font=("微软雅黑", 8)).pack(fill=tk.X, padx=18, pady=(10, 2))
        tk.Label(dlg, textvariable=memory_var, bg=P["bg"], fg=P["ink"], anchor=tk.W,
                 font=("微软雅黑", 9)).pack(fill=tk.X, padx=18, pady=(12, 3))
        tk.Label(dlg, textvariable=clock_var, bg=P["bg"], fg=P["ink"], justify=tk.LEFT,
                 anchor=tk.W, wraplength=430, font=("微软雅黑", 9)).pack(
                     fill=tk.X, padx=18, pady=(0, 10))
        tk.Label(dlg, textvariable=tick_var, bg=P["bg"], fg=P["ink"], anchor=tk.W,
                 font=("微软雅黑", 9)).pack(fill=tk.X, padx=18, pady=(0, 2))
        tick_canvas = tk.Canvas(dlg, width=420, height=7, bg=P["line"], highlightthickness=0)
        tick_canvas.create_rectangle(0, 0, 0, 7, fill=P["blue"], outline="", tags="fill")
        tick_canvas.pack(padx=18, pady=(0, 5))
        tk.Label(dlg, textvariable=delivery_var, bg=P["bg"], fg=P["ink"], anchor=tk.W,
                 font=("微软雅黑", 9)).pack(fill=tk.X, padx=18, pady=(0, 2))
        tk.Label(dlg, textvariable=receipt_var, bg=P["bg"], fg=P["sub"], anchor=tk.W,
                 font=("微软雅黑", 8)).pack(fill=tk.X, padx=18, pady=(0, 4))
        tk.Label(dlg, text="监测层不参与回复、记忆排序、tick 或参数更新。",
                 bg=P["bg"], fg=P["sub"], font=("微软雅黑", 8)).pack(anchor=tk.W, padx=18)

        self._demo_observatory_token += 1
        token = self._demo_observatory_token
        self._demo_observatory = {"window": dlg, "cid": cid, "meters": meter_widgets,
                                  "field_var": field_var,
                                  "urgency_canvas": urgency_canvas, "urgency_var": urgency_var,
                                  "urgency_gate_var": urgency_gate_var, "memory_var": memory_var,
                                  "opening_var": opening_var,
                                  "measurement_var": measurement_var,
                                  "clock_var": clock_var, "tick_var": tick_var,
                                  "delivery_var": delivery_var, "receipt_var": receipt_var,
                                  "tick_canvas": tick_canvas,
                                  "curve_animation": {"signature": None, "started_at": 0.0},
                                  "curve_cursor": {"signature": None, "target_ms": None,
                                                   "from_ms": None, "at_ms": None,
                                                   "started_at": 0.0, "duration_s": 1.0},
                                  "store_cache": {"at": 0.0, "telemetry": (), "messages": (),
                                                  "receipts": ()}}
        dlg.protocol("WM_DELETE_WINDOW", self._close_demo_observatory)
        self._refresh_demo_observatory(token)

    def _refresh_demo_observatory(self, token):
        observer = self._demo_observatory
        if token != self._demo_observatory_token or not isinstance(observer, dict):
            return
        try:
            if not observer["window"].winfo_exists():
                self._demo_observatory = None
                return
        except tk.TclError:
            self._demo_observatory = None
            return
        cid = observer["cid"]
        runner = self.guild.runners.get(cid) if self.guild else None
        if runner is None or not self._is_demo_char(cid):
            self._close_demo_observatory()
            return
        actor = self.actors.get(cid)
        cache = observer.get("store_cache") or {}
        # 面板每 250ms 刷新。根状态、计划与当前时间必须读正式 Runner / Store 的
        # 最新提交态：用户消息和 NPC 输出同样是正式事件，不能被较早的 Actor tick
        # 快照覆盖成旧值。tick 只作为“最近轮询”旁路观测，不拥有状态的展示权。
        mono = time.monotonic()
        if mono - float(cache.get("at", 0.0)) >= 1.0:
            cache = {"at": mono, "telemetry": tuple(runner.store.read_telemetry()),
                     "messages": tuple(runner.store.read_messages()),
                     "receipts": tuple(runner.store.read_action_receipts()),
                     "events": tuple(runner.store.read_events())}
            observer["store_cache"] = cache
        tick_snapshot = {}
        read_tick = getattr(actor, "tick_status_snapshot", None)
        if callable(read_tick):
            try:
                tick_snapshot = read_tick()
            except Exception:
                tick_snapshot = {}
        # 不把 tick_view 用作状态源。此前它会在一次用户输入/回复提交后继续覆盖
        # runner 的新状态和新计划，造成“根量、开口门都不动”的假象。
        observed_state = runner.state
        observed_plan = runner.store.load_clock_plan()
        observed_now = self._runtime_now_ms(cid)
        if self._autonomous_typing_task.get(cid) is not None:
            delivery = {"stage": "自主开口已触发，正在生成"}
        elif self.busy.get(cid):
            delivery = {"stage": "当前回复正在生成或揭示"}
        elif cid in self.hidden_npc or cid in self._reveal_text:
            delivery = {"stage": "已提交，正在按聊天节奏显示"}
        else:
            delivery = {"stage": "等待下一次正式事件"}
        view = build_demo_observatory_snapshot(
            state=observed_state, constants=(runner.profile or {}).get("constants") or {},
            clock_plan=observed_plan, telemetry=cache["telemetry"],
            messages=cache["messages"], action_receipts=cache["receipts"],
            events=cache["events"],
            actor_tick=tick_snapshot, delivery=delivery,
            cadence=(observed_plan or {}).get("cadence"), now_ms=observed_now,
            tick_history=tuple(self._demo_tick_history.get(cid) or ()),
            opening_trace=tuple(self._demo_opening_trace.get(cid) or ()))
        for meter in view["root_meters"]:
            label, bar, value = observer["meters"][meter["key"]]
            label.config(text=meter["label"])
            self._set_demo_meter(bar, meter["bar"])
            value.config(text=f"{meter['value']:.4f}")
        field = {row["key"]: row["value"] for row in view["field_meters"]}
        observer["field_var"].set(
            f"互动势能 {field['P']:.4f}  ·  越线张力 {field['E']:.4f}"
            f"  ·  动态爆发容量 {field['H_max']:.4f}")
        urgency = view["urgency"]
        next_refresh_ms = 250
        if urgency["available"]:
            silence = self._format_demo_duration(view["user_silence_ms"])
            opening_label = ("已锁定开口（F15 crossing）" if urgency.get("locked_crossing")
                             else "主动冲动")
            observer["urgency_var"].set(
                f"{opening_label}：{urgency['value']:.3f} / 动态门限 {urgency['line']:.3f}"
                f" · 余量 {float(urgency.get('margin', 0.0)):.3f} · 用户未回应 {silence}")
            gate_text = "  ·  ".join(
                f"{'✓' if row['ok'] else '○'} {row['label']} {row['detail']}" for row in urgency["gates"])
            observer["urgency_gate_var"].set(
                f"统一开口：{gate_text}")
        else:
            if view.get("awaiting_formal_reply"):
                observer["urgency_var"].set("统一开口曲线：等待本轮正式用户事件")
                observer["urgency_gate_var"].set("资格：用户提交后会建立新的事件锚点")
            else:
                observer["urgency_var"].set("统一开口曲线：等待首轮互动")
                observer["urgency_gate_var"].set("资格：没有正式用户事件锚，尚不能计算")
        # 主图只追加 Actor 已结算的 transition：没有 F08 未来预测、没有 receipt
        # 重播，也不使用 UI 的当前时间补点。每出现一个正式新点，才缓慢画最后一段。
        segments = list(view.get("opening_segments") or ())
        active_segment = list(segments[-1]) if segments else []
        # 计划 marker 不扩展曲线，故不能令最后一段重新播放。
        curve_rows = [row for row in active_segment if not row.get("plan_marker")]
        last = (curve_rows[-1] if curve_rows else (active_segment[-1] if active_segment else {}))
        signature = (len(segments), len(curve_rows), last.get("ts"), last.get("event"))
        now_monotonic = time.monotonic()
        animation = observer.get("curve_animation") or {}

        def install_display(display_segments, display_signature, display_last):
            interval_ms = int(display_last.get("interval_ms") or (view.get("tick") or {}).get(
                "interval_ms", 10_000) or 10_000)
            event_name = str(display_last.get("event") or "")
            animation.update({
                "display_segments": display_segments,
                "display_signature": display_signature,
                "display_event": event_name,
                "display_started_at": now_monotonic,
                "display_duration_s": (self._visual_tick_duration_seconds(interval_ms)
                                       if event_name == "tick" else 1.6),
                "edge_reset": False,
            })

        if "display_signature" not in animation:
            animation = {}
            install_display(segments, signature, last)
            animation["observed_signature"] = signature
        elif animation.get("observed_signature") != signature:
            prior_elapsed = now_monotonic - float(animation.get("display_started_at") or now_monotonic)
            prior_duration = float(animation.get("display_duration_s") or 1.6)
            animation["observed_signature"] = signature
            if self._hold_completed_tick_page(animation.get("display_event"), last.get("event"),
                                               prior_elapsed, prior_duration):
                # F16 已在真实历史；这里只让刚刚结算的 tick 页先走完动画。
                animation["pending_segments"] = segments
                animation["pending_signature"] = signature
                animation["pending_last"] = last
            else:
                install_display(segments, signature, last)
        if animation.get("pending_segments") is not None:
            elapsed = now_monotonic - float(animation.get("display_started_at") or now_monotonic)
            if elapsed >= float(animation.get("display_duration_s") or 1.6):
                pending_segments = animation.pop("pending_segments")
                pending_signature = animation.pop("pending_signature")
                pending_last = animation.pop("pending_last")
                install_display(pending_segments, pending_signature, pending_last)
            else:
                next_refresh_ms = 80
        observer["curve_animation"] = animation
        displayed_segments = animation.get("display_segments") or segments
        duration_s = float(animation.get("display_duration_s") or 1.6)
        progress = min(1.0, max(0.0, (now_monotonic - float(
            animation.get("display_started_at") or now_monotonic)) / duration_s))
        if progress < 1.0:
            next_refresh_ms = 80
        self._draw_demo_opening_trace(
            observer["urgency_canvas"], displayed_segments, progress=progress,
            edge_reset=bool(animation.get("edge_reset")))
        opening = view["opening"]
        if opening["available"]:
            if opening.get("dispatch_eligible"):
                status = "本次越线已进入计划"
            elif opening.get("edge_disposition") == "held_above_after_consumption":
                if opening.get("dispatch"):
                    status = "对应自主 F16 已投递；仍在线上，等待回落后再越线"
                else:
                    status = "仍在线上；未找到同源自主 F16 投递回执"
            elif opening.get("edge_disposition") == "below_zero_rearmed":
                status = "已回落线下；下一次越线可重新开口"
            else:
                status = "候选未过线"
            due_at = opening.get("due_at_ms")
            due_text = ""
            if due_at is not None:
                try:
                    due_text = f" · 约 {max(0, int(due_at) - observed_now) / 1000:.1f} 秒"
                except (TypeError, ValueError):
                    pass
            version = opening.get("formula_version")
            version_text = f" · {version}" if version else ""
            observer["opening_var"].set(
                f"统一开口：{status} · 冲动 {opening['impulse']:.3f}"
                f" − 动态门限 {opening['gate']:.3f} = 余量 {opening['margin']:.3f}"
                f"{due_text}{version_text}")
        elif opening.get("legacy"):
            observer["opening_var"].set("统一开口：检测到旧计划，等待 Actor 按当前根量公式重建")
        else:
            observer["opening_var"].set("统一开口：尚无用户事件后的候选 receipt")
        memory = view["memory"]
        observer["measurement_var"].set("测量：" + str(view["measurement"].get("status") or "暂无读数"))
        observer["memory_var"].set(
            f"记忆选择：候选 {memory['candidate_count']}  · 实际引用 {memory['selected_count']}  · 压制 {memory['suppressed_count']}")
        clock = view["clock"]
        if clock["next_at_ms"] is None:
            edge = clock.get("opening_edge") or {}
            if edge.get("disposition") == "held_above_after_consumption":
                if opening.get("dispatch"):
                    text = "墙钟：对应自主 F16 已投递；仍在线上不重复发言，等待根量回落后再次越线。"
                else:
                    text = "墙钟：edge 仍在线上，但没有同源自主 F16 投递回执；不得当作已发送。"
            elif edge.get("disposition") == "below_zero_rearmed":
                text = "墙钟：已回落线下，下一次根量/时间自然越线时才会重新开口。"
            else:
                text = "墙钟：当前没有待执行开口；角色状态仍会在下一次正式事件结算。"
        else:
            remaining = clock["remaining_ms"]
            remaining_text = "" if remaining is None else f" · 约 {remaining / 60_000:.1f} 分钟"
            reasons = "、".join(clock["reasons"]) or "时间边界"
            suffix = "；即时追发已进入正式候选" if clock["opening_eligible"] else ""
            text = f"墙钟：{reasons}{remaining_text}（计划 v{clock['version']}）{suffix}"
        observer["clock_var"].set(text)
        tick = view["tick"]
        if tick:
            seen = self._format_demo_duration(tick.get("since_observe_ms"))
            work = f"；正在处理 {tick['inflight_kind']}" if tick.get("inflight") else ""
            interval_ms = max(1, int(tick.get("interval_ms", 1) or 1))
            due_in_ms = tick.get("due_in_ms")
            if due_in_ms is None:
                progress = min(1.0, float(tick.get("since_observe_ms") or 0) / interval_ms)
                deadline = "下一观察时间待定"
            else:
                progress = 1.0 - min(1.0, max(0.0, float(due_in_ms)) / interval_ms)
                deadline = f"距下次观察 {max(0, float(due_in_ms)) / 1000:.1f} 秒"
            self._set_demo_meter(observer["tick_canvas"], progress)
            density = float(tick.get("density", 0.0) or 0.0)
            observer["tick_var"].set(
                f"动态轮询：根量观察密度 {density:.3f} · 间隔 {interval_ms / 1000:.1f} 秒"
                f" · {deadline} · 最近观测 {seen}{work}")
        else:
            observer["tick_var"].set("动态轮询：等待 Actor 状态")
            self._set_demo_meter(observer["tick_canvas"], 0.0)
        observer["delivery_var"].set("投递：" + str(view["delivery"].get("stage") or "等待下一次正式事件"))
        receipt = view["receipt"]
        if receipt.get("available"):
            observer["receipt_var"].set(
                f"最近正式提交：{receipt['source']} · 相关记忆引用 {receipt['recall_count']} 条")
        else:
            observer["receipt_var"].set("最近正式提交：暂无")
        self.root.after(next_refresh_ms, lambda: self._refresh_demo_observatory(token))

    def _open_feedback_dialog(self):
        """显式反馈只归档并归因；不把 UI 点击伪装成一条用户对话。"""
        cid = self.active_char
        if not cid or cid not in self.actors:
            return
        dlg = tk.Toplevel(self.root)
        dlg.title("本次互动反馈")
        dlg.configure(bg=P["bg"])
        dlg.geometry("420x390")
        dlg.resizable(False, False)
        tk.Label(dlg, text="这次回应感觉怎么样？", bg=P["bg"], fg=P["ink"],
                 font=("微软雅黑", 14, "bold")).pack(anchor=tk.W, padx=18, pady=(18, 3))
        tk.Label(dlg, text="反馈只作为可追溯证据，不会自动改角色参数或立刻生成新回复。",
                 bg=P["bg"], fg=P["sub"], wraplength=370, justify=tk.LEFT,
                 font=("微软雅黑", 9)).pack(anchor=tk.W, padx=18, pady=(0, 12))
        options = (
            ("这句话让我不舒服", "hurt"), ("这句很合适", "helpful"),
            ("有点太过了", "too_much"), ("回应不够", "too_little"),
        )
        body = tk.Frame(dlg, bg=P["bg"])
        body.pack(fill=tk.X, padx=18)
        for text, label in options:
            tk.Button(body, text=text, bg=P["panel"], fg=P["ink"], relief="solid",
                      bd=1, highlightthickness=0, font=("微软雅黑", 10), anchor="w",
                      padx=10, pady=6,
                      command=lambda value=label: self._submit_feedback(dlg, cid, value)).pack(
                          fill=tk.X, pady=3)
        tk.Label(dlg, text="如果你想评价的是发消息的节奏：", bg=P["bg"], fg=P["sub"],
                 font=("微软雅黑", 9)).pack(anchor=tk.W, padx=18, pady=(10, 2))
        cadence = tk.Frame(dlg, bg=P["bg"])
        cadence.pack(fill=tk.X, padx=18)
        for text, label in (("发得有点太快／太密", "too_fast"),
                            ("节奏刚好", "balanced"),
                            ("间隔太久／太冷", "too_slow")):
            tk.Button(cadence, text=text, bg=P["panel"], fg=P["ink"], relief="solid",
                      bd=1, highlightthickness=0, font=("微软雅黑", 10), anchor="w",
                      padx=10, pady=5,
                      command=lambda value=label: self._submit_cadence_feedback(dlg, cid, value)).pack(
                          fill=tk.X, pady=2)
        tk.Button(dlg, text="取消", bg=P["bg"], fg=P["sub"], relief="flat",
                  command=dlg.destroy).pack(anchor=tk.E, padx=18, pady=10)

    def _submit_feedback(self, dlg, cid, feedback):
        dlg.destroy()
        actor = self.actors.get(cid)
        if actor is not None:
            actor.post("record_feedback", {"feedback": feedback,
                                             "now_ms": int(time.time() * 1000)})

    def _submit_cadence_feedback(self, dlg, cid, feedback):
        dlg.destroy()
        actor = self.actors.get(cid)
        if actor is not None:
            actor.post("record_cadence_feedback", {
                "feedback": feedback,
                "now_ms": int(time.time() * 1000),
                "presentation": dict(self._last_typing_presentation.get(cid) or {}),
            })

    # ------------------------------------------------------------------
    # 陪伴体验声明（创建期填写；会话内仅由用户主动打开）
    # ------------------------------------------------------------------
    def _maybe_ask_preference(self, cid):
        """兼容占位：偏好不再由选中角色时自动弹出。"""
        # 保留该私有入口，避免旧的外部 UI 扩展导入时报错；产品路径只在创建页
        # 展示偏好字段。已创建角色可由顶部“互动偏好”按钮主动修改。
        return None

    def _ask_preference_dialog(self, cid):
        if getattr(self, "_pref_dialog_open", False) or not cid:
            return
        self._pref_dialog_open = True
        dlg = tk.Toplevel(self.root)
        dlg.title("陪伴体验 🌸")
        dlg.configure(bg=P["bg"])
        dlg.geometry("440x200")
        ttk.Label(dlg, text=f"和「{cid}」相处，你希望她怎么对你？\n"
                            "（例如：喜欢被凶一点互怼 / 想要温柔黏人——之后随时可改）",
                  style="Section.TLabel").pack(anchor=tk.W, padx=16, pady=(14, 6))
        var = tk.StringVar()
        ttk.Entry(dlg, textvariable=var, font=("微软雅黑", 10)).pack(fill=tk.X, padx=16)

        def confirm():
            dlg.destroy()
            self._pref_dialog_open = False
            text = var.get().strip()
            if text and cid in self.actors:
                self.actors[cid].post("set_preference", {"text": text})
            else:
                self._mark_pref_declared(cid)

        def skip():
            dlg.destroy()
            self._pref_dialog_open = False
            self._mark_pref_declared(cid)

        ttk.Button(dlg, text="🌸 确认", style="Primary.TButton",
                   command=confirm).pack(side=tk.LEFT, padx=(16, 8), pady=14)
        def open_weights():
            dlg.destroy()
            self._pref_dialog_open = False
            self._ask_preference_weights_dialog(cid)

        ttk.Button(dlg, text="五维比例设置", style="TButton",
                   command=open_weights).pack(side=tk.LEFT, padx=(0, 8), pady=14)
        ttk.Button(dlg, text="跳过", style="TButton",
                   command=skip).pack(side=tk.LEFT, pady=14)

    def _ask_preference_weights_dialog(self, cid):
        """可选的直接 B₀ 声明入口；比例自动归一，仍保留自由文本偏好入口。"""
        if getattr(self, "_pref_dialog_open", False) or not cid:
            return
        self._pref_dialog_open = True
        dlg = tk.Toplevel(self.root)
        dlg.title("陪伴偏好比例 🌸")
        dlg.configure(bg=P["bg"])
        dlg.geometry("460x360")
        ttk.Label(dlg, text="可选：调整五种互动方向的占比。\n"
                            "这是你主动设置的初始基准，之后仍可用文字重新说明。",
                  style="Section.TLabel").pack(anchor=tk.W, padx=16, pady=(14, 8))
        labels = (
            ("Counter_Phobic", "逆恐"), ("Mirroring", "镜映"),
            ("Avoidance", "逃避"), ("Trauma_Recon", "重构"),
            ("Pragmatism", "实用"),
        )
        values = {key: tk.DoubleVar(value=20.0) for key, _ in labels}
        body = tk.Frame(dlg, bg=P["bg"])
        body.pack(fill=tk.X, padx=16)
        for key, title in labels:
            line = tk.Frame(body, bg=P["bg"])
            line.pack(fill=tk.X, pady=3)
            tk.Label(line, text=title, width=8, anchor=tk.W,
                     bg=P["bg"], fg=P["ink"], font=("微软雅黑", 10)).pack(side=tk.LEFT)
            tk.Scale(line, variable=values[key], from_=0, to=100, resolution=1,
                     orient=tk.HORIZONTAL, showvalue=True, length=280,
                     bg=P["bg"], highlightthickness=0).pack(side=tk.LEFT, fill=tk.X,
                                                               expand=True)

        def confirm():
            dlg.destroy()
            self._pref_dialog_open = False
            if cid in self.actors:
                self.actors[cid].post("set_preference_weights", {
                    "weights": {key: value.get() for key, value in values.items()}})

        def cancel():
            dlg.destroy()
            self._pref_dialog_open = False

        actions = tk.Frame(dlg, bg=P["bg"])
        actions.pack(fill=tk.X, padx=16, pady=14)
        ttk.Button(actions, text="🌸 使用这个比例", style="Primary.TButton",
                   command=confirm).pack(side=tk.LEFT)
        ttk.Button(actions, text="返回", style="TButton", command=cancel).pack(side=tk.LEFT, padx=8)

    def _mark_pref_declared(self, cid):
        """跳过声明也经 Actor；GUI 不直接写 profile／用户卡。"""
        actor = self.actors.get(cid)
        if actor is not None:
            actor.post("mark_preference_declared", {})

    def _update_send_state(self, cid=None):
        """主回复可抢占：只在没有收件人时禁用，不能用 busy 封死下一次 Enter。"""
        active = self.active_char
        # 没有可见联系人时，输入框不能留下一个“可发送但无收件人”的假状态。
        blocked = not active
        self.send_btn.config(state="disabled" if blocked else "normal")

    def chat_content(self):
        """聊天内容镜像（冒烟断言用）。"""
        return "\n".join(self._chat_texts)

    def _scroll_bottom(self):
        """滚到底：先让布局收敛（idletasks），再以最终 scrollregion 定位——
        修复"切换角色后要手动滚滚动栏才能看到旧对话"（旧实现只 moveto，
        scrollregion 还是重建前的旧值，视图停在错误位置）。"""
        def go():
            try:
                self.bubbles.update_idletasks()
                self.chat_canvas.configure(
                    scrollregion=self.chat_canvas.bbox("all"))
                self.chat_canvas.yview_moveto(1.0)
            except Exception:
                pass
        self.root.after_idle(go)

    def _clear_chat(self):
        for w in self.bubbles.winfo_children():
            w.destroy()
        self._chat_texts = []

    def _add_bubble(self, text, kind):
        """一条气泡：用户微信绿靠右 / NPC 白靠左；气泡内文字一律左对齐。"""
        if kind == "user":
            bg, fg, side = P["user_bubble"], P["ink"], "right"
        else:
            bg, fg, side = P["npc_bubble"], P["ink"], "left"
        row = tk.Frame(self.bubbles, bg=P["chat_bg"])
        row.pack(fill=tk.X, padx=14, pady=UI["bubble_gap_y"])
        lbl = tk.Label(row, text=text, bg=bg, fg=fg, font=("微软雅黑", 11),
                       justify="left", anchor="w", wraplength=380,
                       padx=12, pady=6)
        lbl.pack(side=side, anchor="e" if kind == "user" else "w")
        self._chat_texts.append(("我：" if kind == "user" else "TA：") + text)

    def _demo_output_separator(self, previous, current):
        """返回演示专用的正式输出边界文字，普通会话永远不调用它。

        一次正式回复会在展示层拆成多个短气泡；F14/F15 则是另一张已提交的
        output receipt。两者视觉上很容易混淆。这里仅依据已落盘消息的 source
        投影一个非消息分界条：它不写入 Store、记忆、prompt 或时间总线，也不
        参与任何调度。这样演示中的观众能看清“补发”不是把同一回复切碎了。
        """
        if not isinstance(previous, dict) or not isinstance(current, dict):
            return None
        current_source = str(current.get("source") or "")
        previous_source = str(previous.get("source") or "")
        if (previous.get("role") == "npc" and current.get("role") == "npc"
                and previous_source == "reply" and current_source == "followup"):
            return "—— 上一条已发送 · 她又补发了一句 ——"
        if (previous.get("role") == "user" and current.get("role") == "npc"
                and current_source == "online_proactive"):
            return "—— 沉默之后 · 她又主动发来一条 ——"
        return None

    def _add_demo_output_separator(self, text):
        """演示输出边界：视觉注释，不是一条聊天消息。"""
        row = tk.Frame(self.bubbles, bg=P["chat_bg"])
        row.pack(fill=tk.X, padx=14, pady=(12, 7))
        tk.Label(row, text=text, bg=P["chat_bg"], fg=P["sub"],
                 font=("微软雅黑", 9), anchor="center").pack()

    def _maybe_time(self, ts, last_ts):
        """聊天时间投影：跨日显示日期，长间隔显示时分。

        它只消费已落盘消息的真实时间戳；不向墙钟、记忆或 prompt 写入任何日期事件。
        """
        markers = markers_for_message(ts, last_ts, gap_ms=UI["time_gap_ms"])
        if markers["date"]:
            day = markers["date"]
            lbl = tk.Label(self.bubbles, text=f"— {day} —",
                           bg=P["chat_bg"], fg=P["sub"], font=("微软雅黑", 9))
            lbl.pack(pady=(10, 4))
            self._chat_texts.append(f"— {day} —")
        if markers["show_time"]:
            lbl = tk.Label(self.bubbles, text=f"· {_fmt_time(ts)} ·",
                           bg=P["chat_bg"], fg=P["sub"], font=("微软雅黑", 9))
            lbl.pack(pady=8)
            self._chat_texts.append(f"· {_fmt_time(ts)} ·")
        return ts

    def _split_parts(self, text):
        """回复 → 展示短句气泡：先按段落拆；无段落则按完整句末标点断句。

        JSON 输出契约后模型不再写换行（实证：回复退化成一大段）——分句不依赖
        模型配合，确定性断句。省略号是语气停顿，不是独立消息边界：连续的中文／
        ASCII 省略号只在展示层收敛为一次，避免模型的重复标点变成一串空洞气泡。
        末尾中文句号只在展示层隐藏：原始回复仍完整落盘、进入记忆和后续上下文；
        问号、感叹号和一次省略号保留语气。
        """
        import re
        t = (text or "").strip()
        if not t:
            return [t]
        # 只净化 UI 文本，不能回写 Store。``……``、``......`` 或两者混用都
        # 代表同一次停顿；单个英文句点不碰，避免影响数字、缩写或 URL。
        t = re.sub(r"[….]{2,}", "……", t)
        if "\n" in t:
            parts = [p.strip() for p in t.split("\n") if p.strip()]
        else:
            # 省略号不参与 split：`……嗯……` 应是一句有停顿的话，而不是三个
            # 连续白气泡。真正完整的陈述／疑问／感叹才形成下一条展示气泡。
            parts = [p for p in re.split(r"(?<=[。！？!?])", t) if p.strip()]
        merged = []
        for p in parts:
            if p in ("……", "…") and merged:
                merged[-1] = merged[-1] + p
            else:
                merged.append(p)
        if len(merged) > UI["bubble_max_parts"]:
            merged = [t]            # 小作文：句子多到拆不动 → 一整条长气泡（不截断）
        return [part.rstrip("。").strip() for part in merged[:UI["bubble_max_parts"]]]

    def _load_older_messages(self):
        """扩大当前角色的只读历史窗口；不读取或修改任何引擎状态。"""
        cid = self.active_char
        if not cid or not self.guild:
            return
        runner = self.guild.runners.get(cid)
        if runner is None:
            return
        current = self.history_limit.get(cid, UI["history_initial_messages"])
        self.history_limit[cid] = min(
            MAX_CHAT_LINES, current + UI["history_page_messages"])
        self._render_messages(scroll_to_bottom=False)
        self.root.after_idle(lambda: self.chat_canvas.yview_moveto(0.0))

    def _render_messages(self, scroll_to_bottom=True):
        self._clear_chat()
        self.history_more_btn = None
        runner = self.guild.runners.get(self.active_char)
        if runner:
            hidden = self.hidden_npc.get(self.active_char)
            partial = self._partial.get(self.active_char)
            last_ts = None
            scenario = (runner.profile or {}).get("demo_scenario") or {}
            if is_demo_profile(runner.profile):
                # 场景说明只在展示层出现：它既不伪装成聊天，也不会落入短尾/记忆。
                tk.Label(self.bubbles, text="演示背景 · " + str(scenario.get("title") or ""),
                         bg=P["pink_soft"], fg=P["pink_dark"], justify="left", anchor="w",
                         wraplength=520, font=("微软雅黑", 10, "bold"), padx=12, pady=6).pack(
                             fill=tk.X, padx=14, pady=(10, 2))
                tk.Label(self.bubbles, text=str(scenario.get("background") or ""),
                         bg=P["chat_bg"], fg=P["sub"], justify="left", anchor="w",
                         wraplength=520, font=("微软雅黑", 9), padx=12, pady=4).pack(
                             fill=tk.X, padx=14, pady=(0, 6))
                self._chat_texts.append("· 演示背景（非聊天记录）")
            # 演示背景种子和合成测试夹具可以进入召回，但绝不能被伪装成用户已发送的
            # 聊天气泡；用户看到的是场景说明与之后真实经过 Actor 的互动。
            # Append-only 文件记录“何时提交”；屏幕时间线则以事件真实发生时刻为准。
            # 同 ts 保留原文件顺序，避免把同一动作的人为拆散。
            raw_window = runner.store.read_recent_message_window(
                self.history_limit.get(self.active_char, UI["history_initial_messages"]))
            all_messages = [m for m in raw_window["records"]
                            if not is_non_dialogue_seed(m)]
            all_messages = [m for _index, m in sorted(enumerate(all_messages),
                            key=lambda pair: (int(pair[1].get("ts", 0) or 0), pair[0]))]
            page = select_history_window(
                all_messages,
                self.history_limit.get(self.active_char, UI["history_initial_messages"]),
                MAX_CHAT_LINES)
            has_older = bool(raw_window["has_older"] or page["hidden_count"])
            if has_older and not page["reached_render_limit"]:
                more = tk.Button(
                    self.bubbles, text="加载更早消息",
                    bg=P["pink_soft"], fg=P["pink_dark"], relief="flat",
                    font=("微软雅黑", 9, "bold"), command=self._load_older_messages)
                more.pack(pady=(10, 6))
                self.history_more_btn = more
            elif page["reached_render_limit"]:
                tk.Label(self.bubbles, text="已达到本次界面渲染上限，可导出完整本地记录",
                         bg=P["chat_bg"], fg=P["sub"], font=("微软雅黑", 9)).pack(pady=(10, 6))
            previous_visible = None
            for m in page["messages"]:
                if self._hide_unrevealed_startup_replay(self.active_char, m):
                    continue  # 已落盘但尚未轮到 UI 揭示的启动回放，不可提前剧透。
                if self._is_queued_npc_visual(self.active_char, m):
                    continue  # 已提交但尚在前一条之后排队的自主输出，不可抢画面。
                if self._is_pending_visual_npc_message(self.active_char, m):
                    continue  # 已提交但仍在本地输入空窗中，不能先剧透全文再收回。
                if m.get("role") == "npc" and m.get("ts") == hidden:
                    continue                      # 打字停留期间先别剧透
                kind = "user" if m.get("role") == "user" else "npc"
                text = m.get("text", "")
                if kind == "npc":
                    if partial and m.get("ts") == partial["ts"]:
                        visible = partial["parts"][:partial["shown"]]
                    else:
                        visible = self._split_parts(text)
                    # 短句 → 连续几条气泡（连发），时间只挂在第一条上
                    for i, p in enumerate(visible):
                        if i == 0:
                            last_ts = self._maybe_time(m.get("ts", 0), last_ts)
                            # 仅演示角色展示“另一张 receipt 已提交”的边界；这里
                            # 不能把注释当消息写回，也不能让它影响下方的聊天时间线。
                            if is_demo_profile(runner.profile):
                                separator = self._demo_output_separator(previous_visible, m)
                                if separator:
                                    self._add_demo_output_separator(separator)
                        self._add_bubble(p, kind)
                else:
                    last_ts = self._maybe_time(m.get("ts", 0), last_ts)
                    self._add_bubble(text, kind)
                previous_visible = m
            # 乐观气泡：刚发出的消息在管线落盘前就显示（不跟着 AI 回复一起冒出来）
            pending = self.pending_user.get(self.active_char)
            if pending:
                stored_texts = [str(m.get("text", "")) for m in all_messages
                                if m.get("role") == "user"]
                if str(pending["text"]) in stored_texts:
                    # 管线⑦已落盘、气泡还没来得及退役（切换角色的窄窗口）→ 以存储为准
                    self.pending_user.pop(self.active_char, None)
                else:
                    last_ts = self._maybe_time(pending["ts"], last_ts)
                    self._add_bubble(pending["text"], "user")
        self._sync_typing_indicator()
        if scroll_to_bottom:
            self._scroll_bottom()

    def _sync_typing_indicator(self):
        """把等待态投影到输入框上方，而非伪装成对方的一条聊天记录。"""
        widget = getattr(self, "typing_status", None)
        if widget is None:
            return
        cid = self.active_char
        has_draft = bool(getattr(self, "_reply_drafts", {}).get(cid))
        visible = bool(cid and (self._typing_visible.get(cid) or has_draft))
        text = self._typing_text() if visible else ""
        # 流式草稿仅是内部就绪信号。将候选正文置于“正在输入”下方会破坏聊天仿真，
        # 还会造成“先剧透、定稿后再等一次”的双重等待。
        draft = ""
        try:
            self.typing_status_var.set(text)
        except Exception:
            # 保留轻量 UI 冒烟对象的兼容性；真实 Tk 界面必有 StringVar。
            widget.config(text=text)
        draft_widget = getattr(self, "typing_draft", None)
        draft_var = getattr(self, "typing_draft_var", None)
        if draft_widget is not None and draft_var is not None:
            draft_var.set(draft)

    @staticmethod
    def _blend_hex(start, end, fraction):
        """两个 ``#RRGGBB`` 颜色之间的纯展示插值。"""
        f = max(0.0, min(1.0, float(fraction)))
        a = tuple(int(str(start)[i:i + 2], 16) for i in (1, 3, 5))
        b = tuple(int(str(end)[i:i + 2], 16) for i in (1, 3, 5))
        mixed = tuple(round(x + (y - x) * f) for x, y in zip(a, b))
        return "#%02X%02X%02X" % mixed

    def _set_typing_color(self, color):
        widget = getattr(self, "typing_status", None)
        if widget is not None:
            try:
                widget.config(fg=color)
            except Exception:
                pass

    def _fade_typing(self, cid, cycle, step=1):
        """可见窗口尾部渐隐；过期/切换后的回调不得改动当前界面。"""
        if (self._typing_cycle.get(cid) != cycle or not self.typing.get(cid)
                or not self._typing_visible.get(cid) or cid != self.active_char):
            return
        plan = getattr(self, "_typing_plans", {}).get(cid) or typing_presentation_plan()
        steps = max(1, int(UI["typing_fade_steps"]))
        self._set_typing_color(self._blend_hex(P["sub"], P["bg"], step / steps))
        if step < steps:
            visible_ms = int(UI["typing_visible_ms"] * plan["visible_multiplier"])
            interval = max(1, int(visible_ms * UI["typing_fade_ms"] /
                                  UI["typing_visible_ms"]) // steps)
            self.root.after(interval, lambda c=cid, token=cycle, n=step + 1:
                            self._fade_typing(c, token, n))

    def _typing_text(self):
        """只显示等待状态；稳定草稿由独立的下一行展示。"""
        cid = getattr(self, "active_char", None)
        draft = str(getattr(self, "_reply_drafts", {}).get(cid) or "")
        if draft:
            return "· 对方正在组织…"
        dots = getattr(self, "_typing_dots", 1)
        return "· 对方正在输入" + "…" * dots

    def _typing_draft_text(self):
        """兼容旧调用：流式草稿永远不是用户可见文本。"""
        return ""

    def _start_typing_rhythm(self, cid, turn_result=None, presentation=None):
        """开始等待态的“思考空窗 → 输入一阵 → 停一阵”展示节律。

        这只描述用户看见的状态，不表示模型/角色从用户按发送起持续生成；Actor
        的真实在飞任务和 message/receipt 时间线完全不受影响。
        """
        if not cid:
            return
        if not hasattr(self, "_typing_plans"):
            self._typing_plans = {}
        if presentation is not None:
            plan = presentation_snapshot(presentation)
            self._typing_plans[cid] = plan
            self._last_typing_presentation[cid] = dict(plan)
        elif turn_result is not None:
            self._typing_plans[cid] = typing_presentation_plan(turn_result)
            self._last_typing_presentation[cid] = presentation_snapshot(
                self._typing_plans[cid])
        plan = self._typing_plans.get(cid) or typing_presentation_plan()
        self.typing[cid] = True
        cycle = int(self._typing_cycle.get(cid, 0)) + 1
        self._typing_cycle[cid] = cycle
        self._typing_visible[cid] = False
        self._set_typing_color(P["sub"])
        if cid == self.active_char:
            self._sync_typing_indicator()
        self.root.after(int(UI["typing_initial_delay_ms"] * plan["initial_multiplier"]),
                        lambda c=cid, token=cycle: self._set_typing_phase(c, token, True))

    def _stop_typing_rhythm(self, cid):
        """结束等待态，并使所有旧的 UI 定时回调失效。"""
        if not cid:
            return
        self.typing[cid] = False
        self._typing_visible[cid] = False
        self._typing_plans.pop(cid, None)
        self._typing_cycle[cid] = int(self._typing_cycle.get(cid, 0)) + 1
        self._set_typing_color(P["sub"])
        if cid == self.active_char:
            self._sync_typing_indicator()

    def _set_typing_phase(self, cid, cycle, visible):
        if (self._typing_cycle.get(cid) != cycle
                or not self.typing.get(cid)):
            return
        self._typing_visible[cid] = bool(visible)
        self._set_typing_color(P["sub"])
        if cid == self.active_char:
            self._sync_typing_indicator()
        plan = getattr(self, "_typing_plans", {}).get(cid) or typing_presentation_plan()
        if visible:
            self._tick_typing(cid, cycle)
            visible_ms = max(1, int(UI["typing_visible_ms"] * plan["visible_multiplier"]))
            fade_ms = min(visible_ms, int(UI["typing_fade_ms"] * plan["visible_multiplier"]))
            fade_after = max(0, visible_ms - fade_ms)
            self.root.after(fade_after,
                            lambda c=cid, token=cycle: self._fade_typing(c, token))
            delay = visible_ms
        else:
            delay = max(1, int(UI["typing_hidden_ms"] * plan["hidden_multiplier"]))
        self.root.after(int(delay), lambda c=cid, token=cycle, show=not visible:
                        self._set_typing_phase(c, token, show))

    def _tick_typing(self, cid=None, cycle=None):
        """仅在可见窗口内更新点数；隐藏窗口不伪装成持续打字。"""
        cid = cid or getattr(self, "active_char", None)
        if (not cid or not self.typing.get(cid) or not self._typing_visible.get(cid)
                or (cycle is not None and self._typing_cycle.get(cid) != cycle)):
            return
        self._typing_dots = getattr(self, "_typing_dots", 1) % 3 + 1
        if cid == self.active_char:
            self._sync_typing_indicator()
        plan = getattr(self, "_typing_plans", {}).get(cid) or typing_presentation_plan()
        self.root.after(max(1, int(UI["typing_dot_interval_ms"] * plan["dot_multiplier"])),
                        lambda c=cid, token=cycle: self._tick_typing(c, token))

    def _reveal_cadence(self, gap_count, cid=None):
        """为已提交文本生成纯 UI 的分句展示节律。

        这份随机性只在 Tk 本地存在：不写档、不发 Actor 事件、不参与 F14/F15/F16，
        所以它可以为体验服务而不会反向改动角色的墙钟或决策。
        """
        count = max(0, int(gap_count))
        cid = cid or getattr(self, "active_char", None)
        plan = getattr(self, "_typing_plans", {}).get(cid) or typing_presentation_plan()
        anchors = (UI["reveal_cadence_fast_ms"], UI["reveal_cadence_mid_ms"],
                   UI["reveal_cadence_slow_ms"])
        patterns = ((0, 1, 2), (0, 2, 1), (1, 0, 2),
                    (1, 2, 0), (2, 0, 1), (2, 1, 0))
        index = min(len(patterns) - 1,
                    max(0, int(self._ui_rng.uniform(0, len(patterns)))))
        pattern = patterns[index]
        jitter = float(UI["reveal_cadence_jitter_ms"])
        result = []
        for index in range(count):
            base = float(anchors[pattern[index % len(pattern)]]) * plan["reveal_multiplier"]
            result.append(max(180, int(round(base + self._ui_rng.uniform(-jitter, jitter)))))
        return result

    def _schedule_next_reveal(self, cid):
        partial = self._partial.get(cid) or {}
        shown = int(partial.get("shown", 0))
        cadence = list(partial.get("cadence_ms") or ())
        delay = cadence[shown - 1] if 0 < shown <= len(cadence) else UI["reveal_cadence_mid_ms"]
        self.root.after(int(delay), lambda c=cid: self._reveal_next_part(c))

    def _sys(self, msg):
        lbl = tk.Label(self.bubbles, text="· " + msg, bg=P["chat_bg"], fg=P["sub"],
                       font=("微软雅黑", 9), anchor="w")
        lbl.pack(fill=tk.X, padx=18, pady=2)
        self._chat_texts.append("· " + msg)
        self._scroll_bottom()

    def _run_async(self, fn, cb):
        def worker():
            result, err = None, None
            try:
                result = fn()
            except Exception as e:  # noqa: BLE001
                err = e
            self._post_ui_event("callback", (cb, result, err))
        threading.Thread(target=worker, daemon=True).start()

    def _send(self, event=None):
        cid = self.active_char
        if not cid:
            return
        text = self.input_entry.get().strip()
        if not text:
            return
        # 首登回放可能已在 UI 队列中等待视觉揭示。用户一旦主动发言，Actor 会令
        # 尚未提交的 replay 失效；这里同步清掉尚未揭示的 UI 项，避免已失效批次在
        # 当前用户气泡之后“补演”。已真实显示/已落盘的历史不回滚。
        self._reveal_queue = [(queued_cid, message)
                              for queued_cid, message in self._reveal_queue
                              if queued_cid != cid]
        # 已正式提交的旧回放仍是共同历史，不能因用户 Enter 被藏成“没发生”。
        # 这里只结束其尚未播放的动画，让重绘时按真实时间线显示在新输入之前。
        pending_replay = self._startup_replay_pending.get(cid, set())
        if pending_replay:
            self._startup_replay_visible.setdefault(cid, set()).update(pending_replay)
            pending_replay.clear()
        self._startup_replay_gate.discard(cid)
        # 与 Actor 的 interaction_epoch 对齐：本地未播放时间线同样失效；追发
        # 资格和撤销由 Actor 管理，UI 不再保存可投递任务。
        self._reply_drafts.pop(cid, None)
        # 第二次 Enter 取代尚未提交的主回复：旧乐观气泡、输入中和分句揭示
        # 只是本地展示态，立即撤掉；Actor/Runner 会在提交闸上拒绝迟到结果。
        self._partial.pop(cid, None)
        self._reveal_text.pop(cid, None)
        self._reveal_send_at.pop(cid, None)
        getattr(self, "_pending_visual_ts", {}).pop(cid, None)
        self._pending_visual_reveal.discard(cid)
        self.hidden_npc.pop(cid, None)
        self._stop_typing_rhythm(cid)
        self.busy[cid] = True
        self.pending_user[cid] = {"ts": int(time.time() * 1000), "text": text}
        # 发送后先保留短暂思考空窗；不是把整个网络生成期伪装成持续输入。
        self._start_typing_rhythm(cid)
        self._update_send_state(cid)
        self.input_entry.delete(0, tk.END)
        self._render_messages()          # 乐观气泡立刻上屏；等待态按独立节律出现
        actor = self.actors.get(cid)
        if actor is None:
            self.busy[cid] = False
            self._stop_typing_rhythm(cid)
            self.pending_user.pop(cid, None)
            self._update_send_state(cid)
            self._sys("会话尚未就绪，请稍后再试")
            return
        # 发送 = 向 Actor 提交一次本地 Enter；Actor 根据已注入 composition 选择
        # typed formal work 或显式 legacy compatibility。UI 不认识 Runner/Writer。
        actor.submit_local_message(text, now_ms=self._runtime_now_ms(cid))

    def _typing_ms(self, reply, cid=None):
        """"正在输入"停留：回复越长打字越久（挣扎感 ≠ 真的晾人）。"""
        n = max(0, len(str(reply or "")))
        cid = cid or getattr(self, "active_char", None)
        plan = getattr(self, "_typing_plans", {}).get(cid) or typing_presentation_plan()
        return min(UI["typing_max_ms"], int((UI["typing_min_ms"] +
                   n * UI["typing_per_char_ms"]) * plan["dwell_multiplier"]))

    def _on_reply(self, cid, result, err):
        if err:
            self._reveal_send_at.pop(cid, None)
            self._reveal_text.pop(cid, None)
            self._reply_drafts.pop(cid, None)
            self.busy[cid] = False
            self._stop_typing_rhythm(cid)
            self.pending_user.pop(cid, None)
            self._update_send_state(cid)
            if cid == self.active_char:
                self._sys(f"回复失败：{err}")
            return
        d = result.get("delay_sec") or 0
        # 完整 JSON 已经走完 Runner 的正式提交路径；临时流前缀立即失效，避免
        # 既有的隐藏/分段揭示与草稿双重出现。
        self._reply_drafts.pop(cid, None)
        self.pending_user.pop(cid, None)   # 管线⑦已落盘，乐观气泡退役
        self._start_typing_rhythm(cid, result)
        self._reveal_text[cid] = result.get("reply") or ""
        self._reveal_send_at[cid] = result.get("send_at")
        if d > 0:
            # 延迟回复的正式提交由 Actor 的 F16 deadline 完成；UI 只维持输入态，
            # 等 ``released`` receipt 后展示，绝不能自己通过 after 推进状态。
            if cid == self.active_char:
                self._render_messages()
            return
        # 即时回复同样等待 Actor 的 ``released`` receipt。它通常紧随
        # ``reply_result`` 到达，但 UI 不能再用 after(0) 反向调用 Actor；否则
        # 展示层会成为 F16 的第二个释放入口。
        self.hidden_npc[cid] = result.get("send_at")
        if cid == self.active_char:
            self._render_messages()

    def _on_released(self, cid, reply):
        """Actor 释放完成 → 主线程揭示（挂起消息已落盘，时间戳 = 原发送时刻）。"""
        reply_text = reply.get("reply", "") if isinstance(reply, dict) else reply
        # 秒回曾把消息暂时隐藏，延迟回复则到此刻才落盘；两者都必须带同一
        # ``send_at`` 进入 partial，渲染器才能只显第一短句、再按间隔续显。
        ts = self.hidden_npc.pop(cid, None)
        if ts is None:
            ts = self._reveal_send_at.pop(cid, None)
        else:
            self._reveal_send_at.pop(cid, None)
        reply_text = reply_text or self._reveal_text.get(cid) or ""
        self._reveal_text.pop(cid, None)
        self._schedule_released_reveal(cid, ts, reply_text)

    def _schedule_released_reveal(self, cid, ts, reply_text):
        """纯 UI 呈现等待；不回投 Actor，也不修改墙钟。"""
        delay = self._typing_ms(reply_text, cid)
        # 从 Actor 发出 release 到 Tk 真正绘制的这一小段也属于前一条视觉周期。
        # 否则后续 Actor 任务的 typing 状态会把这条已提交主回复的 reveal 作废。
        self._pending_visual_reveal.add(cid)
        pending_ts = getattr(self, "_pending_visual_ts", None)
        if pending_ts is None:  # 兼容极简同步测试对象
            pending_ts = {}
            self._pending_visual_ts = pending_ts
        pending_ts[cid] = ts
        # 极简对象/同步单测没有 Tk root，保留直接展示语义；真实 UI 的延迟只服务
        # 体验，且新 Enter 使旧 cycle 自然失效。
        root = getattr(self, "root", None)
        if root is None or delay <= 0:
            self._run_reveal(cid, ts, reply_text)
            return
        cycle = self._typing_cycle.get(cid)

        def reveal_if_current():
            if (self._typing_cycle.get(cid) != cycle or not self.busy.get(cid)):
                return
            self._run_reveal(cid, ts, reply_text)

        root.after(delay, reveal_if_current)

    def _run_reveal(self, cid, ts, reply_text):
        # 文本分段和间隔是纯呈现：它不会写 Store、改变 Actor 的 deadline 或影响
        # 后续输出资格。节律只在 Tk 本地生成，不能成为任何行为输入。
        self._pending_visual_reveal.discard(cid)
        getattr(self, "_pending_visual_ts", {}).pop(cid, None)
        parts = self._split_parts(reply_text) if reply_text else []
        cadence = self._reveal_cadence(len(parts) - 1, cid)
        if len(parts) > 1 and cid == self.active_char:
            # 连发揭示：先亮第一条，中间"对方正在输入"打打停停，再逐条冒
            self._start_typing_rhythm(cid)
            self._partial[cid] = {"ts": ts, "parts": parts, "shown": 1,
                                  "cadence_ms": cadence}
            self._render_messages()
            self._schedule_next_reveal(cid)
            return
        self._finish_reveal(cid)

    def _reveal_next_part(self, cid):
        """连发揭示：每亮一条，中间停顿（正在输入动画），直到全部亮完。"""
        part = self._partial.get(cid)
        if not part:
            self._finish_reveal(cid)
            return
        part["shown"] += 1
        if part["shown"] >= len(part["parts"]):
            self._partial.pop(cid, None)
            self._finish_reveal(cid)
            return
        if cid == self.active_char:
            self._render_messages()
            self._start_typing_rhythm(cid)
        self._schedule_next_reveal(cid)

    def _finish_reveal(self, cid):
        self._stop_typing_rhythm(cid)
        self.busy[cid] = False
        self._reply_drafts.pop(cid, None)
        self.pending_user.pop(cid, None)
        self._partial.pop(cid, None)
        self._reveal_send_at.pop(cid, None)
        getattr(self, "_pending_visual_ts", {}).pop(cid, None)
        self._pending_visual_reveal.discard(cid)
        # 已提交的下一条只在前一条视觉完成后播放；这不通知 Actor，更不影响其
        # 已经完成的 tick／计划／生成。用户仍可随时 Enter。
        if self._play_next_committed_npc_visual(cid):
            return
        # 自主任务可能在主回复仍播放时就已由 Actor 启动。前面为了不打断主回复
        # 没有重置 typing cycle；等主回复视觉完成后，才恢复这份“仍在生成”的只读
        # 等待态。它不创建任务，也不向 Actor 发送任何命令。
        if self._autonomous_typing_task.get(cid) is not None:
            self._start_typing_rhythm(cid)
        if cid != self.active_char:
            self.unread[cid] = self.unread.get(cid, 0) + 1   # 后台回复：角标提醒
        self._update_send_state(cid)
        self._refresh_list()
        if cid == self.active_char:
            self._render_messages()
            self.input_entry.focus_set()

    # ------------------------------------------------------------------
    # 新建角色
    # ------------------------------------------------------------------
    def _new_dialog(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("创建新角色 🌸")
        dlg.configure(bg=P["bg"])
        dlg.geometry("650x840")
        dlg.minsize(600, 760)
        ttk.Label(dlg, text="角色模板（作为初始值）", style="Section.TLabel").pack(
            anchor=tk.W, padx=16, pady=(14, 4))
        card_var = tk.StringVar()
        presets = scan_presets(self.presets_dir)
        background_mode = "（从简介自定义生成）"
        combo = ttk.Combobox(dlg, textvariable=card_var, values=[background_mode] + presets,
                             state="readonly", font=("微软雅黑", 10))
        combo.pack(fill=tk.X, padx=16)
        if presets:
            combo.set(presets[0])
        else:
            combo.set(background_mode)

        template_info = tk.Label(dlg, justify="left", anchor="w", wraplength=600,
                                 bg=P["pink_soft"], fg=P["ink"], padx=10, pady=5,
                                 font=("微软雅黑", 9))
        template_info.pack(fill=tk.X, padx=16, pady=(8, 3))

        identity_var = tk.StringVar()
        ttk.Label(dlg, text="角色称呼（可选；用于本地会话名）", style="Hint.TLabel").pack(
            anchor=tk.W, padx=16, pady=(7, 2))
        ttk.Entry(dlg, textvariable=identity_var, font=("微软雅黑", 10)).pack(
            fill=tk.X, padx=16)

        intro_label = ttk.Label(dlg, text="角色简介（可选）", style="Hint.TLabel")
        intro_label.pack(anchor=tk.W, padx=16, pady=(8, 2))
        intro_box = tk.Text(dlg, height=2, wrap=tk.WORD, font=("微软雅黑", 10),
                                 bg="#FFFFFF", fg=P["ink"], relief=tk.FLAT)
        intro_box.pack(fill=tk.X, padx=16)

        role_header = tk.Frame(dlg, bg=P["bg"])
        role_header.pack(fill=tk.X, padx=16, pady=(9, 3))
        ttk.Label(role_header, text="角色互动底色", style="Hint.TLabel").pack(side=tk.LEFT)
        weight_total = tk.StringVar(value="合计 100%")
        tk.Label(role_header, textvariable=weight_total, bg=P["bg"], fg=P["pink_dark"],
                 font=("微软雅黑", 9, "bold")).pack(side=tk.RIGHT)
        tk.Label(dlg, text="拖动一项，其余会按当前比例自动调整；七常量由此配比生成。",
                 bg=P["bg"], fg=P["sub"], font=("微软雅黑", 8)).pack(
                     anchor=tk.W, padx=16, pady=(0, 2))
        role_values = {key: tk.DoubleVar(value=20.0) for key, _ in ROLE_WEIGHT_LABELS}
        weights_updating = [False]
        role_body = tk.Frame(dlg, bg=P["bg"])
        role_body.pack(fill=tk.X, padx=16)
        for key, title in ROLE_WEIGHT_LABELS:
            line = tk.Frame(role_body, bg=P["bg"])
            line.pack(fill=tk.X, pady=1)
            tk.Label(line, text=title, width=8, anchor=tk.W,
                     bg=P["bg"], fg=P["ink"], font=("微软雅黑", 9)).pack(side=tk.LEFT)
            tk.Scale(line, variable=role_values[key], from_=0, to=100, resolution=1,
                     orient=tk.HORIZONTAL, showvalue=True, length=440,
                     bg=P["bg"], highlightthickness=0).pack(side=tk.LEFT, fill=tk.X,
                                                               expand=True)

        constants_info = tk.Label(dlg, justify="left", anchor="w", wraplength=600,
                                  bg=P["panel"], fg=P["sub"], padx=10, pady=5,
                                  font=("Consolas", 9))
        constants_info.pack(fill=tk.X, padx=16, pady=(5, 2))

        # C（角色底色）与 X0（双方已确认的关系起点）必须在创建时分开。默认不
        # 写入 X0，严格沿用通用冷启动；作者需要明确勾选后，才把这四条运行态交给
        # 正式 state schema。两条杠杆表示已说出口的事实，不能在这里凭空填写。
        relation_enabled = tk.BooleanVar(value=False)
        relation_box = tk.Frame(dlg, bg=P["panel"], highlightbackground=P["line"],
                                highlightthickness=1)
        relation_box.pack(fill=tk.X, padx=16, pady=(8, 2))
        tk.Checkbutton(
            relation_box, variable=relation_enabled, bg=P["panel"], fg=P["ink"],
            activebackground=P["panel"], text="设定开局关系起点（可选）",
            font=("微软雅黑", 9, "bold"), anchor="w",
        ).pack(fill=tk.X, padx=8, pady=(6, 0))
        tk.Label(
            relation_box,
            text="只描述开始时双方已确认的关系，不会伪造聊天或记忆；未勾选则使用通用冷启动。",
            bg=P["panel"], fg=P["sub"], font=("微软雅黑", 8), anchor="w",
        ).pack(fill=tk.X, padx=8, pady=(0, 3))
        relation_labels = (
            ("E_env", "当下压力", 50.0),
            ("P_need", "牵挂／未尽", 50.0),
            ("V_attr", "亲近感", 0.0),
            ("V_rep", "别扭／防备", 50.0),
        )
        relation_values = {key: tk.DoubleVar(value=value)
                           for key, _title, value in relation_labels}
        relation_grid = tk.Frame(relation_box, bg=P["panel"])
        relation_grid.pack(fill=tk.X, padx=8, pady=(0, 6))
        for index, (key, title, _value) in enumerate(relation_labels):
            row, col = divmod(index, 2)
            cell = tk.Frame(relation_grid, bg=P["panel"])
            cell.grid(row=row, column=col, sticky="ew", padx=(0, 9) if col == 0 else (0, 0), pady=1)
            relation_grid.grid_columnconfigure(col, weight=1)
            tk.Label(cell, text=title, width=9, anchor=tk.W, bg=P["panel"],
                     fg=P["ink"], font=("微软雅黑", 8)).pack(side=tk.LEFT)
            tk.Scale(cell, variable=relation_values[key], from_=0, to=100, resolution=1,
                     orient=tk.HORIZONTAL, showvalue=True, length=175, bg=P["panel"],
                     highlightthickness=0).pack(side=tk.LEFT, fill=tk.X, expand=True)

        def read_template(name):
            if name not in presets:
                return None
            with open(os.path.join(self.presets_dir, name), "r", encoding="utf-8") as handle:
                return json.load(handle)

        def refresh_preview(*_args):
            name = card_var.get()
            try:
                card = read_template(name)
                if card is None:
                    preview = build_role_creation_preview(
                        {"persona_card": "自定义角色；身份和语气由下方简介解析。"},
                        {key: value.get() for key, value in role_values.items()})
                    template_info.config(text="自定义角色 · 从下方简介生成身份与人设")
                    intro_label.config(text="角色简介（必填）")
                else:
                    preview = build_role_creation_preview(
                        card, {key: value.get() for key, value in role_values.items()})
                    identity = preview["identity"] or "该模板未提供身份简介。"
                    template_info.config(text=f"当前模板：{name.removesuffix('.json')} · {identity}")
                    intro_label.config(text="角色简介（可选；补充本次人设）")
                constants = "  ".join(
                    f"{key}={value:.3f}" for key, value in preview["constants"].items())
                total = sum(int(round(value.get())) for value in role_values.values())
                weight_total.set(f"合计 {total}%")
                constants_info.config(text="七常量预览（只读）  " + constants)
            except Exception as exc:  # noqa: BLE001 -- 预览不能阻断用户改滑块。
                template_info.config(text=f"预览读取失败：{exc}")
                constants_info.config(text="当前五维无法生成七常量；创建时会再次校验。")

        def set_role_weights(values):
            """批量装载预设；屏蔽滑条互相回调，最后一次性刷新。"""
            weights_updating[0] = True
            try:
                for key, value in role_values.items():
                    value.set(max(0, min(100, int(round(float(values.get(key, 0)))))))
            finally:
                weights_updating[0] = False
            refresh_preview()

        def rebalance_from_slider(key):
            if weights_updating[0]:
                return
            current = {name: value.get() for name, value in role_values.items()}
            adjusted = rebalance_role_weights(current, key)
            set_role_weights(adjusted)

        def load_template_weights(*_args):
            name = card_var.get()
            try:
                card = read_template(name)
                if card is None:
                    refresh_preview()
                    return
                parsed = parse_labor(json.dumps(card, ensure_ascii=False))
                set_role_weights({key: float((parsed.get("weights") or {}).get(key, 0.0)) * 100.0
                                  for key in role_values})
            except Exception as exc:  # noqa: BLE001 -- 创建时仍会二次校验并显示失败。
                self._sys(f"角色卡读取失败：{exc}")
            refresh_preview()

        combo.bind("<<ComboboxSelected>>", load_template_weights)
        for key, value in role_values.items():
            value.trace_add("write", lambda *_args, name=key: rebalance_from_slider(name))
        load_template_weights()
        ttk.Label(dlg, text="你想要的陪伴体验（可留空；留空即使用中性基准）",
                  style="Hint.TLabel").pack(anchor=tk.W, padx=16, pady=(10, 2))
        pref_var = tk.StringVar()
        ttk.Entry(dlg, textvariable=pref_var, font=("微软雅黑", 10)).pack(fill=tk.X, padx=16)

        ttk.Label(dlg, text="陪伴偏好的五维比例可在创建后点「💌 偏好」调整；它与上方角色五维不同。",
                  style="Hint.TLabel").pack(anchor=tk.W, padx=16, pady=(6, 0))

        def create():
            name = card_var.get()
            intro = intro_box.get("1.0", tk.END).strip()
            if name not in presets and not intro:
                self._sys("请选择模板，或填写角色背景")
                return
            requested_name = identity_var.get().strip()
            base_name = requested_name or (name.replace(".json", "") if name in presets else "自定义角色")
            safe_base = "".join(char if (char.isalnum() or char in "-_") else "_"
                                for char in base_name).strip("_") or "角色"
            char_id = safe_base + "_" + time.strftime("%H%M%S")
            pref = pref_var.get().strip()
            role_weights = {key: value.get() for key, value in role_values.items()}
            initial_state = ({key: value.get() / 100.0 for key, value in relation_values.items()}
                             if relation_enabled.get() else None)
            if name not in presets:
                background = ((f"角色称呼：{requested_name}\n" if requested_name else "") + intro)
                create_runner = lambda: self.guild.new_character_from_background(
                    char_id, background, pref or None, preference_declared=True,
                    role_weights_override=role_weights, initial_state=initial_state)
            else:
                try:
                    card = parse_labor(json.dumps(read_template(name), ensure_ascii=False))
                    card["weights"] = role_weights
                    if initial_state is not None:
                        card["initial_state"] = initial_state
                    if intro or requested_name:
                        additions = []
                        if requested_name:
                            additions.append(f"称呼：{requested_name}")
                        if intro:
                            additions.append(intro)
                        card["persona_card"] = (card["persona_card"].rstrip()
                                                + "\n\n## 本次角色简介\n"
                                                + "\n".join(additions))
                except Exception as exc:  # noqa: BLE001
                    self._sys(f"角色卡读取失败：{exc}")
                    return
                create_runner = lambda: self.guild.new_character(
                    char_id, card, pref or None, preference_declared=True)
            dlg.destroy()
            self._run_async(
                lambda: (char_id, create_runner()),
                self._on_created)

        ttk.Button(dlg, text="🌸 创建", style="Primary.TButton",
                   command=create).pack(pady=14)

    def _new_demo_dialog(self):
        """显式创建比赛录屏场景；背景展示而非伪造历史聊天。"""
        dlg = tk.Toplevel(self.root)
        dlg.title("创建演示场景")
        dlg.configure(bg=P["bg"])
        dlg.geometry("460x425")
        tk.Label(dlg, text="选择比赛演示场景", bg=P["bg"], fg=P["ink"],
                 font=("微软雅黑", 15, "bold")).pack(anchor=tk.W, padx=18, pady=(18, 5))
        scenes = {row["title"]: row for row in scene_catalog()}
        scene_var = tk.StringVar(value=get_scene(HIGH_TENSION_SCENE_ID)["title"])
        scene_combo = ttk.Combobox(dlg, textvariable=scene_var, values=list(scenes),
                                   state="readonly", font=("微软雅黑", 10))
        scene_combo.pack(fill=tk.X, padx=18)
        scene_info = tk.Label(dlg, bg=P["bg"], fg=P["sub"], justify="left", anchor="w",
                              wraplength=420, font=("微软雅黑", 10))
        scene_info.pack(fill=tk.X, padx=18, pady=(10, 4))
        scene_guide = tk.Label(dlg, bg=P["bg"], fg=P["sub"], justify="left", anchor="w",
                               wraplength=420, font=("微软雅黑", 9))
        scene_guide.pack(fill=tk.X, padx=18, pady=(0, 9))
        tk.Label(dlg, text="每次创建都是独立轨迹；不满意可以新建一条再录。",
                 bg=P["bg"], fg=P["sub"], font=("微软雅黑", 9)).pack(
                     anchor=tk.W, padx=18, pady=(0, 8))
        card_var = tk.StringVar()
        presets = scan_demo_presets(self.presets_dir, self.demo_presets_dir)
        combo = ttk.Combobox(dlg, textvariable=card_var, values=presets,
                             state="readonly", font=("微软雅黑", 10))
        combo.pack(fill=tk.X, padx=18)
        tk.Label(dlg, text="你希望她怎么和你相处（可留空；留空即使用中性基准）",
                 bg=P["bg"], fg=P["sub"], font=("微软雅黑", 9)).pack(
                     anchor=tk.W, padx=18, pady=(12, 3))
        pref_var = tk.StringVar()
        ttk.Entry(dlg, textvariable=pref_var, font=("微软雅黑", 10)).pack(
            fill=tk.X, padx=18)

        def selected_scene():
            return scenes.get(scene_var.get()) or get_scene(HIGH_TENSION_SCENE_ID)

        def refresh_scene(*_args):
            scene = selected_scene()
            scene_info.config(text=scene["background"])
            scene_guide.config(text=scene["guide"])
            preferred = scene["default_preset"]
            combo.set(preferred if preferred in presets else (presets[0] if presets else ""))

        scene_combo.bind("<<ComboboxSelected>>", refresh_scene)
        refresh_scene()

        def create():
            name = card_var.get()
            if not name:
                return
            try:
                path = resolve_demo_preset(name, self.presets_dir, self.demo_presets_dir)
                if path is None:
                    raise FileNotFoundError("演示角色卡不存在或不属于演示入口")
                with open(path, "r", encoding="utf-8") as f:
                    card = parse_labor(f.read())
            except Exception as e:  # noqa: BLE001
                self._sys(f"角色卡读取失败：{e}")
                return
            char_id = "演示_" + name.replace(".json", "") + "_" + time.strftime("%H%M%S")
            scene_id = selected_scene()["id"]
            pref = pref_var.get().strip()
            dlg.destroy()
            self._run_async(
                lambda: (char_id, prepare_competition_scene(
                    self.guild, char_id, card, scene_id=scene_id,
                    preference_text=pref or None, preference_declared=True)),
                self._on_demo_created)

        ttk.Button(dlg, text="创建并开始演示", style="Primary.TButton",
                   command=create).pack(pady=14)

    def _on_demo_created(self, result, err):
        if err:
            self._sys(f"演示创建失败：{err}")
            return
        char_id, prepared = result
        self._attach_actor(char_id, prepared["runner"])
        self._refresh_list()
        self._select(char_id)
        self._sys("演示背景已载入（非聊天记录）；之后每条互动均走正式 Actor／Runner 链路")

    def _on_created(self, result, err):
        if err:
            self._sys(f"创建失败：{err}")
            return
        char_id, runner = result
        # 新角色创建完立即接入 Actor；之后没有 Runner 直连的回退路径。
        self._attach_actor(char_id, runner)
        self._refresh_list()
        self._select(char_id)
        status = (runner.profile or {}).get("preference_status")
        if status == "degraded":
            self._sys("角色已创建并加载人格卡；陪伴偏好暂按中性基准，可稍后重新声明")

    # ------------------------------------------------------------------
    def _on_close(self):
        self._closed = True
        for cid, actor in list(self.actors.items()):
            try:
                self._stop_attached_actor(cid, actor)
            except Exception:
                pass
        if self.guild is not None:
            for cid, runner in list(self.guild.runners.items()):
                try:
                    if runner.profile is not None:
                        export_session(runner.store, runner.profile)
                except Exception:
                    pass
        self.root.destroy()
