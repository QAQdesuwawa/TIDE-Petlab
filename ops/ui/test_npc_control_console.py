"""隔离测试控制台的纯 Tk 视图。

本模块绝不导入 Router/Actor/Runner/Store/tick。按钮只把已确认的文本交给外部 host
callback；成功或失败的审计与状态变化均由 host 和正式链负责。
"""
from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Mapping

from ops.config import PALETTE


class TestNpcControlConsole:
    """为已有 NPC 提供两种明确标记的隔离测试输入。"""
    __test__ = False

    def __init__(self, root: tk.Misc, *, participant_labels: Mapping[str, str],
                 submit_scenario_source: Callable[[str, str], Mapping[str, object]],
                 arm_manual_response: Callable[[str, str], Mapping[str, object]]):
        labels = {str(key): str(value) for key, value in participant_labels.items()
                  if str(key).strip() and str(value).strip()}
        if not labels:
            raise ValueError("测试控制台必须有至少一个 participant")
        if not callable(submit_scenario_source) or not callable(arm_manual_response):
            raise TypeError("测试控制台必须接收两个 host callback")
        self._labels = labels
        self._submit_scenario_source = submit_scenario_source
        self._arm_manual_response = arm_manual_response
        self.window = tk.Toplevel(root)
        self.window.title("TIDE · NPC 隔离测试控制台")
        self.window.geometry("620x510")
        self.window.minsize(560, 460)
        self.window.configure(bg=PALETTE["bg"])
        self._sender_var = tk.StringVar(value=next(iter(labels)))
        self._status_var = tk.StringVar(value="隔离 test session：输入尚未提交。")
        self._build()

    def _build(self):
        outer = tk.Frame(self.window, bg=PALETTE["bg"])
        outer.pack(fill=tk.BOTH, expand=True, padx=20, pady=18)
        tk.Label(outer, text="NPC 隔离测试控制台", bg=PALETTE["bg"], fg=PALETTE["ink"],
                 font=("微软雅黑", 16, "bold")).pack(anchor=tk.W)
        tk.Label(outer,
                 text="场景 source 不伪造 F16；手工台词只在下一次正常输出已经成立时被消费。",
                 bg=PALETTE["bg"], fg=PALETTE["sub"], font=("微软雅黑", 9),
                 justify=tk.LEFT).pack(anchor=tk.W, pady=(4, 14))
        chooser = tk.Frame(outer, bg=PALETTE["bg"])
        chooser.pack(fill=tk.X, pady=(0, 8))
        tk.Label(chooser, text="作为发言者的 NPC", bg=PALETTE["bg"], fg=PALETTE["ink"],
                 font=("微软雅黑", 10)).pack(side=tk.LEFT)
        menu = tk.OptionMenu(chooser, self._sender_var, *self._labels)
        menu.configure(bg=PALETTE["panel"], fg=PALETTE["ink"], highlightthickness=0)
        menu.pack(side=tk.LEFT, padx=(12, 0))

        self._source_text = self._panel(
            outer, "A · 提交场景 source", "作为明确的 test_injected source 路由；由 address resolver 决定谁进入 F03。",
            "提交场景 source", self._submit_source)
        self._manual_text = self._panel(
            outer, "B · arm 下一次正常 F16", "只替换该 NPC 已获正常输出资格的一次正文；不立即发话。",
            "arm 手工台词", self._arm_response)
        tk.Label(outer, textvariable=self._status_var, bg=PALETTE["bg"], fg=PALETTE["blue_dark"],
                 font=("微软雅黑", 9), justify=tk.LEFT, wraplength=560).pack(anchor=tk.W, pady=(14, 0))

    def _panel(self, parent, title: str, hint: str, button: str, command):
        panel = tk.Frame(parent, bg=PALETTE["panel"], highlightbackground=PALETTE["line"],
                         highlightthickness=1)
        panel.pack(fill=tk.X, pady=7)
        tk.Label(panel, text=title, bg=PALETTE["panel"], fg=PALETTE["ink"],
                 font=("微软雅黑", 11, "bold")).pack(anchor=tk.W, padx=13, pady=(10, 1))
        tk.Label(panel, text=hint, bg=PALETTE["panel"], fg=PALETTE["sub"],
                 font=("微软雅黑", 8), justify=tk.LEFT).pack(anchor=tk.W, padx=13)
        text = tk.Text(panel, height=3, wrap=tk.WORD, font=("微软雅黑", 10),
                       bg="white", fg=PALETTE["ink"], relief=tk.FLAT)
        text.pack(fill=tk.X, padx=13, pady=(7, 8))
        tk.Button(panel, text=button, command=command, bg=PALETTE["blue"], fg="white",
                  activebackground=PALETTE["blue_dark"], activeforeground="white",
                  relief=tk.FLAT, font=("微软雅黑", 9, "bold"), padx=12, pady=5).pack(
                      anchor=tk.E, padx=13, pady=(0, 10))
        return text

    def _invoke(self, text_widget: tk.Text, callback: Callable[[str, str], Mapping[str, object]], label: str):
        text = text_widget.get("1.0", tk.END).strip()
        if not text:
            self._status_var.set("%s 未提交：请输入文本。" % label)
            return
        participant_id = self._sender_var.get()
        try:
            receipt = callback(participant_id, text)
        except Exception as exc:  # host 决定合法性；UI 不猜测、也不补写。
            self._status_var.set("%s 失败：%s：%s" % (label, type(exc).__name__, exc))
            return
        identity = (receipt.get("event_id") or receipt.get("request_id") or "已确认") \
            if isinstance(receipt, Mapping) else "已确认"
        self._status_var.set("%s 已交给隔离 host：%s" % (label, identity))
        text_widget.delete("1.0", tk.END)

    def _submit_source(self):
        self._invoke(self._source_text, self._submit_scenario_source, "场景 source")

    def _arm_response(self):
        self._invoke(self._manual_text, self._arm_manual_response, "手工台词")
