# ops/config.py
"""全局配置与常量：本地互动工作台的中性色板与 UI 常量。"""
import os

# 界面色板（保留旧 key 名，避免把纯视觉重命名扩散进界面行为代码）。
PALETTE = {
    "bg": "#F6F7FB",          # 全局底色 · 冷白
    "panel": "#FFFFFF",       # 卡片/面板底
    "chat_bg": "#F5F2FF",     # 聊天阅读面 · 极浅蓝粉紫，衬出微信式白／绿气泡
    "pink": "#4F46E5",        # 主色 · 靛蓝（沿用键名兼容旧 UI）
    "pink_dark": "#3730A3",   # 主色加深
    "pink_soft": "#EEF2FF",   # 淡靛蓝区块
    "blue": "#0891B2",        # 辅色 · 青蓝
    "blue_dark": "#0E7490",
    "blue_soft": "#ECFEFF",
    "ink": "#172033",         # 主文字 · 深靛灰
    "sub": "#64748B",         # 次级文字
    "line": "#E2E8F0",        # 分隔线
    "user_bubble": "#95EC69", # 用户气泡 · 微信绿
    "npc_bubble": "#FFFFFF",  # NPC 气泡 · 微信白
    "success": "#059669",
    "warning": "#D97706",
    "danger": "#DC2626",
}

# 聊天面板最大保留行数（内存保护）
MAX_CHAT_LINES = 2000

# 项目根下的卡片边界：用户可选角色卡、演示专用卡与测试 fixture 必须物理隔离。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRESETS_DIR = os.path.join(PROJECT_ROOT, "presets")
DEMO_PRESETS_DIR = os.path.join(PROJECT_ROOT, "demo_presets")
TEST_PRESETS_DIR = os.path.join(PROJECT_ROOT, "tests", "fixtures", "presets")
