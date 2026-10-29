# ops/export.py
"""会话导出（P7 金标数据入口）：退出时把本场会话写为 data/golden/*.jsonl。"""
import json
import os
import time


def export_session(store, profile, out_dir="data/golden"):
    """store/UserStore + profile → 一条未标注会话 JSONL。返回导出路径。"""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"exported_{int(time.time() * 1000)}.jsonl")
    session = {
        "session_id": os.path.basename(path),
        "background": (profile or {}).get("background", ""),
        "manifest": (profile or {}).get("manifest"),   # P7 版本戳（MANE 铁律：labels 带戳）
        "messages": store.read_messages(),
        "labels": None,
    }
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(session, ensure_ascii=False) + "\n")
    return path
