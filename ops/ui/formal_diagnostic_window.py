"""Generic Tk display for committed diagnostic snapshot views."""
from __future__ import annotations

from collections.abc import Callable, Mapping
import tkinter as tk

from ops.ui.diagnostic_snapshot_view import (
    DiagnosticSnapshotView,
    compile_diagnostic_snapshot_view,
)


class FormalDiagnosticPresenter:
    def __init__(self, snapshot_provider: Callable[[], object]):
        if not callable(snapshot_provider):
            raise TypeError("snapshot_provider must be callable")
        self._snapshot_provider = snapshot_provider

    def current_view(self) -> DiagnosticSnapshotView:
        return compile_diagnostic_snapshot_view(self._snapshot_provider())


def view_render_signature(view: DiagnosticSnapshotView) -> tuple:
    if not isinstance(view, DiagnosticSnapshotView):
        raise TypeError("view must be DiagnosticSnapshotView")
    return (
        view.available,
        view.status,
        view.descriptor_version,
        view.snapshot_fingerprint,
    )


class FormalDiagnosticWindow:
    """Read-only window whose only data input is a snapshot provider."""

    def __init__(
        self, root, *, title: str, snapshot_provider: Callable[[], object],
        palette: Mapping[str, str] | None = None, refresh_ms: int = 1000,
    ):
        colors = dict(palette or {})
        self._bg = colors.get("bg", "#f7f6ff")
        self._panel = colors.get("panel", "#ffffff")
        self._ink = colors.get("ink", "#25233a")
        self._sub = colors.get("sub", "#6f6b86")
        self._line = colors.get("line", "#dedbea")
        self._accent = colors.get("accent", "#5648e8")
        self._presenter = FormalDiagnosticPresenter(snapshot_provider)
        self._refresh_ms = max(250, int(refresh_ms))
        self._closed = False
        self._signature = None

        self.window = tk.Toplevel(root)
        self.window.title(title)
        self.window.configure(bg=self._bg)
        self.window.geometry("520x720")
        self.window.minsize(440, 420)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        tk.Label(
            self.window, text="TIDE · 模型诊断", bg=self._bg, fg=self._ink,
            font=("微软雅黑", 15, "bold"),
        ).pack(anchor=tk.W, padx=18, pady=(16, 2))
        tk.Label(
            self.window, text="只读已提交切片 · 指标由当前模型包声明",
            bg=self._bg, fg=self._sub, font=("微软雅黑", 9),
        ).pack(anchor=tk.W, padx=18, pady=(0, 10))
        self._status = tk.StringVar(value="等待已提交观测")
        tk.Label(
            self.window, textvariable=self._status, bg=self._bg, fg=self._sub,
            anchor=tk.W, font=("微软雅黑", 9),
        ).pack(fill=tk.X, padx=18, pady=(0, 8))
        self._content = tk.Frame(self.window, bg=self._bg)
        self._content.pack(fill=tk.BOTH, expand=True, padx=18, pady=(0, 16))
        self.refresh()

    def exists(self) -> bool:
        try:
            return not self._closed and bool(self.window.winfo_exists())
        except tk.TclError:
            return False

    def lift(self) -> None:
        self.window.lift()
        self.window.focus_force()

    def close(self) -> None:
        self._closed = True
        try:
            if self.window.winfo_exists():
                self.window.destroy()
        except tk.TclError:
            pass

    def refresh(self) -> None:
        if not self.exists():
            return
        try:
            view = self._presenter.current_view()
        except (TypeError, ValueError):
            self._status.set("诊断不可用：已提交切片未通过校验")
            self._clear_content()
            self._signature = ("invalid",)
        else:
            signature = view_render_signature(view)
            if signature != self._signature:
                self._render(view)
                self._signature = signature
        self.window.after(self._refresh_ms, self.refresh)

    def _clear_content(self) -> None:
        for child in self._content.winfo_children():
            child.destroy()

    def _render(self, view: DiagnosticSnapshotView) -> None:
        self._clear_content()
        if not view.available:
            self._status.set("等待下一条已提交正式观测")
            tk.Label(
                self._content, text="当前还没有可验证的诊断切片。",
                bg=self._bg, fg=self._sub, font=("微软雅黑", 10),
            ).pack(anchor=tk.W, pady=12)
            return
        self._status.set(
            f"快照 {view.snapshot_version} · 描述 {view.descriptor_version}")
        for panel in view.panels:
            box = tk.LabelFrame(
                self._content, text=f" {panel.label} ", bg=self._panel,
                fg=self._ink, bd=1, relief=tk.SOLID,
                font=("微软雅黑", 10, "bold"), padx=10, pady=8,
            )
            box.pack(fill=tk.X, pady=(0, 10))
            tk.Label(
                box, text=panel.chart, bg=self._panel, fg=self._sub,
                font=("微软雅黑", 8),
            ).pack(anchor=tk.E)
            for metric in panel.series:
                row = tk.Frame(box, bg=self._panel)
                row.pack(fill=tk.X, pady=3)
                tk.Label(
                    row, text=metric.label, width=14, anchor=tk.W,
                    bg=self._panel, fg=self._ink, font=("微软雅黑", 9),
                ).pack(side=tk.LEFT)
                bar = tk.Canvas(
                    row, width=250, height=9, bg=self._line,
                    highlightthickness=0,
                )
                bar.create_rectangle(
                    0, 0, int(250 * metric.position), 9,
                    fill=self._accent, outline="",
                )
                bar.pack(side=tk.LEFT, padx=(6, 8), fill=tk.X, expand=True)
                tk.Label(
                    row, text=metric.display_text, width=8, anchor=tk.E,
                    bg=self._panel, fg=self._sub, font=("微软雅黑", 9),
                ).pack(side=tk.RIGHT)


__all__ = (
    "FormalDiagnosticPresenter",
    "FormalDiagnosticWindow",
    "view_render_signature",
)
