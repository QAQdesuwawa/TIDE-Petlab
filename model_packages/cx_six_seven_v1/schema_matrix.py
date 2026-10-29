"""Six/seven prototype schema and preset persistence authority."""
import json
import os
from datetime import datetime

from .initialization_schema import generate_base_constants
from .preference_parameters import B0_TARGETS


class SchemaMatrix:
    def __init__(self):
        self.BASE_MATRIX = {
            "Counter_Phobic": {
                "description": "逆恐：用攻击掩饰恐惧。顺风局必骑脸(a1极高)，高压下绝不认怂(b1极低)，除非被戳中死穴否则绝不发狂(g极低)。",
                **B0_TARGETS["Counter_Phobic"],
            },
            "Mirroring": {
                "description": "镜映：死要面子活受罪。极度虚荣(b2极高)，需要关注(a2中高)。一旦处于杠杆下风，防御拉满拼命圆谎(b3高)。",
                **B0_TARGETS["Mirroring"],
            },
            "Avoidance": {
                "description": "逃避(依恋)：重度社恐/冷漠。外界一有压力就想切断联系(b1极高)，绝不主动攻击(a1,a2极低)。",
                **B0_TARGETS["Avoidance"],
            },
            "Trauma_Recon": {
                "description": "重构：病娇/自毁疯批。极其容易被羁绊和压力引爆(g1极高)，平时疯狂作死来确认对方的爱(g2拉满)。防线极脆。",
                **B0_TARGETS["Trauma_Recon"],
            },
            "Pragmatism": {
                "description": "实用主义 (普通人补丁)：极其平庸。不发疯、不极度虚荣。所有的参数都在中低水平，靠逻辑而非情绪驱动。",
                **B0_TARGETS["Pragmatism"],
            },
        }
        self.PARAM_DESCRIPTIONS = {
            "a1": "绝对权力变现率 (上位时攻击性)",
            "a2": "内心黑洞掠夺率 (空虚时代偿攻击)",
            "b1": "环境应激退行率 (压力下逃避)",
            "b2": "虚荣防御阈值 (需要认同时的防御)",
            "b3": "信息下位防御率 (被压制时的退缩)",
            "g1": "高压羁绊引爆系数 (羁绊+压力下崩溃)",
            "g2": "创伤强迫重复率 (主动作死确认爱)",
        }
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        self.presets_dir = os.path.join(repo_root, "presets")

    def get_available_schemas(self):
        return list(self.BASE_MATRIX.keys())

    def get_schema_info(self, schema_name):
        if schema_name not in self.BASE_MATRIX:
            raise KeyError(f"未知的心理模型: {schema_name}")
        return self.BASE_MATRIX[schema_name].copy()

    def get_schema_description(self, schema_name):
        if schema_name not in self.BASE_MATRIX:
            raise KeyError(f"未知的心理模型: {schema_name}")
        return self.BASE_MATRIX[schema_name].get("description", "无描述")

    def get_all_descriptions(self):
        return {
            name: self.BASE_MATRIX[name].get("description", "")
            for name in self.BASE_MATRIX
        }

    def generate_pure_preset(self, schema_name):
        if schema_name not in self.BASE_MATRIX:
            raise KeyError(f"未知的心理模型: {schema_name}")
        return {schema_name: 1.0}

    def get_available_presets(self):
        if not os.path.exists(self.presets_dir):
            return []
        return sorted(
            filename for filename in os.listdir(self.presets_dir)
            if filename.endswith(".json")
        )

    def load_preset_from_file(self, filename):
        filepath = os.path.join(self.presets_dir, filename)
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"预设文件不存在: {filename}")
        with open(filepath, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if "weights" in data:
            return data["weights"]
        return {key: value for key, value in data.items() if not key.startswith("_")}

    def save_preset_to_file(self, filename, weights_dict, author="", notes=""):
        os.makedirs(self.presets_dir, exist_ok=True)
        filepath = os.path.join(self.presets_dir, filename)
        total = sum(weights_dict.values())
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"预设权重总和必须为1.0，当前为 {total}")
        mixture_desc_parts = []
        for schema, weight in sorted(
            weights_dict.items(), key=lambda item: item[1], reverse=True,
        ):
            if weight > 0:
                mixture_desc_parts.append(
                    f"{weight * 100:.0f}% [{schema}] "
                    f"{self.get_schema_description(schema)}"
                )
        preset_data = {
            "_format_version": "1.0",
            "_description": "T.I.D.E. 角色人格预设文件（承自 M.A.N.E. 格式）",
            "_note": "此文件为 JSON 格式。其中 _ 开头的字段为注释，不影响加载。核心权重在 'weights' 字段中。",
            "_created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "_author": author if author else "匿名",
            "_notes": notes if notes else "无",
            "_mixture_description": mixture_desc_parts,
            "_param_meanings": self.PARAM_DESCRIPTIONS,
            "weights": weights_dict,
        }
        with open(filepath, "w", encoding="utf-8") as handle:
            json.dump(preset_data, handle, ensure_ascii=False, indent=4)
        return filepath

    def generate_presets_readme(self):
        os.makedirs(self.presets_dir, exist_ok=True)
        readme_path = os.path.join(self.presets_dir, "README.txt")
        lines = [
            "=" * 60,
            "T.I.D.E. - 角色人格预设说明（承自 M.A.N.E.）",
            "=" * 60,
            "",
            "【预设是什么】",
            "预设文件 (.json) 保存了一个角色的心理图式权重组合。",
            "",
            "【如何使用预设】",
            "1. 加载预设文件中的 weights 字段。",
            "2. 权重总和必须为 1.0（容差 0.01）。",
            "",
            "-" * 60,
            "【原生心理模型一览】",
            "-" * 60,
        ]
        for name in self.BASE_MATRIX:
            lines.append(f"  {name}: {self.get_schema_description(name)}")
        lines.extend(("", "-" * 60, "【参数含义一览】", "-" * 60))
        for parameter, meaning in self.PARAM_DESCRIPTIONS.items():
            lines.append(f"  {parameter}: {meaning}")
        lines.append("")
        with open(readme_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
        return readme_path

    def generate_constants(self, weights_dict):
        return generate_base_constants(weights_dict)


__all__ = ("SchemaMatrix",)
