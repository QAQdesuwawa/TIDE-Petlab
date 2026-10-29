# ops/ui/theme.py
"""本地互动工作台的 ttk 主题：阅读优先、低饱和、清晰状态层级。"""
from ops.config import PALETTE


def apply_theme(style, root):
    """把主题应用到指定 ttk.Style 与根窗口。"""
    s = style
    P = PALETTE
    root.configure(bg=P["bg"])

    # ── 基础框架 ──
    s.configure("TFrame", background=P["bg"])
    s.configure("TLabelframe", background=P["bg"], bordercolor=P["line"],
                lightcolor=P["line"], darkcolor=P["line"], relief="flat")
    s.configure("TLabelframe.Label", font=("微软雅黑", 11, "bold"),
                foreground=P["pink_dark"], background=P["bg"])

    # ── 标签 ──
    s.configure("TLabel", font=("微软雅黑", 10), background=P["bg"], foreground=P["ink"])
    s.configure("Title.TLabel", font=("微软雅黑", 18, "bold"),
                foreground=P["pink_dark"], background=P["panel"])
    s.configure("Subtitle.TLabel", font=("微软雅黑", 9),
                foreground=P["sub"], background=P["panel"])
    s.configure("Section.TLabel", font=("微软雅黑", 11, "bold"),
                foreground=P["pink_dark"], background=P["bg"])
    s.configure("Hint.TLabel", font=("微软雅黑", 9), foreground=P["sub"], background=P["bg"])

    # ── 按钮 ──
    s.configure("TButton", font=("微软雅黑", 10, "bold"), padding=(14, 8),
                background=P["panel"], foreground=P["ink"], borderwidth=0, relief="flat")
    s.map("TButton",
          background=[("active", P["pink_soft"]), ("pressed", P["pink_soft"])],
          foreground=[("disabled", "#D9C0CE")])
    s.configure("Primary.TButton", font=("微软雅黑", 11, "bold"), padding=(16, 9),
                background=P["pink"], foreground="white", borderwidth=0)
    s.map("Primary.TButton",
          background=[("active", P["pink_dark"]), ("pressed", P["blue_dark"]), ("disabled", "#F2C3DC")],
          foreground=[("disabled", "#FFF5FA")])
    s.configure("Action.TButton", font=("微软雅黑", 11, "bold"), padding=(14, 8),
                background=P["blue"], foreground="white", borderwidth=0)
    s.map("Action.TButton",
          background=[("active", P["blue_dark"]), ("pressed", P["pink_dark"]), ("disabled", "#C3CFF2")])

    # 加载进度：进度只对应已确认的连接／载入／追平阶段；模型生成未完成时不虚报满格。
    s.configure("Loading.Horizontal.TProgressbar", troughcolor="#DDD9F5",
                background=P["pink"], bordercolor="#DDD9F5", lightcolor=P["pink"],
                darkcolor=P["pink_dark"])

    # ── 输入控件 ──
    s.configure("TEntry", fieldbackground=P["panel"], bordercolor=P["line"],
                lightcolor=P["line"], darkcolor=P["line"], padding=4)
    s.configure("TCombobox", fieldbackground=P["panel"])

    # ── 复选 ──
    s.configure("TCheckbutton", background=P["bg"], font=("微软雅黑", 10), foreground=P["ink"])
    s.map("TCheckbutton", background=[("active", P["bg"])])

    # ── 滚动条 ──
    s.configure("TScrollbar", background="#F0C9DD", troughcolor=P["pink_soft"],
                bordercolor=P["pink_soft"], arrowcolor=P["pink_dark"])
