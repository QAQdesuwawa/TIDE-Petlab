# core/state_manager.py
"""状态持久化：全量状态 JSON 读写 + 每轮快照（自 M.A.N.E. core/state_manager.py 改造，实现设计 §3.2）。

改造点：
- 状态字典 = T.I.D.E. 全量状态（temporal.bus.new_state() 的产物）；
- 新增 save_snapshot：每轮全量快照落盘（架构文档 §5.4，本地存储不省大小）；
- 删除 MANE 的 prev_drives / accum 独立文件（并入全量状态，减少分叉）。
"""
import json
import os


class StateManager:
    def __init__(self, file_path="data/state.json", snapshots_dir="data/snapshots"):
        self.file_path = file_path
        self.snapshots_dir = snapshots_dir
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

    def save_state(self, state_dict):
        """当前全量状态写入 JSON（utf-8，缩进可读）。"""
        os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(state_dict, f, ensure_ascii=False, indent=2)

    def load_state(self):
        """读取全量状态；文件不存在返回 None。utf-8-sig 兼容记事本 BOM。"""
        if not os.path.exists(self.file_path):
            return None
        with open(self.file_path, "r", encoding="utf-8-sig") as f:
            return json.load(f)

    def save_snapshot(self, state_dict, turn_id):
        """每轮全量快照落盘，返回快照路径。turn_id 为轮次序号（防覆写）。"""
        os.makedirs(self.snapshots_dir, exist_ok=True)
        path = os.path.join(self.snapshots_dir, f"snapshot_{int(turn_id):06d}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state_dict, f, ensure_ascii=False, indent=2)
        return path

    def list_snapshots(self):
        if not os.path.exists(self.snapshots_dir):
            return []
        return sorted(os.listdir(self.snapshots_dir))
