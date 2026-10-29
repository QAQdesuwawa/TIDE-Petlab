"""responder JSON 流的可撤销对白草稿提取。

这里只识别输出协议中的 ``reply`` JSON 字符串，并且只释放已经到达自然停顿的
前缀。它不生成消息、不写状态、更不解释任何业务语义；网络中断时调用方可直接
丢弃该对象，完整 JSON 仍由既有 ``parse_responder`` 作为唯一正式入口。
"""
from __future__ import annotations

import re


_REPLY_KEY = re.compile(r'"reply"\s*:\s*"')
_STOP_CHARS = frozenset("。！？!?；;\n")


def sanitize_draft_text(value):
    """将临时显示文本限制为 Tk 可安全承载的可见字符。

    草稿来自未完成的网络 JSON，不是正式消息。控制字符和孤立 UTF-16 surrogate
    既没有对白意义，也可能令 Windows/Tk 显示为替代符或乱码；在草稿层丢弃它们，
    正式回复仍由完整 JSON 解析与既有输出链路处理。
    """
    safe = []
    for char in str(value or ""):
        code = ord(char)
        if 0xD800 <= code <= 0xDFFF:
            continue
        if code < 0x20 or code == 0x7F:
            safe.append(" ")
        else:
            safe.append(char)
    return "".join(safe)


def _decode_reply_prefix(raw):
    """返回 ``(decoded, complete)``；不完整 escape 不会抢先暴露给 UI。"""
    match = _REPLY_KEY.search(str(raw or ""))
    if match is None:
        return "", False
    text, index = str(raw), match.end()
    out = []
    while index < len(text):
        char = text[index]
        if char == '"':
            return sanitize_draft_text("".join(out)), True
        if char != "\\":
            out.append(char)
            index += 1
            continue
        if index + 1 >= len(text):
            break
        escaped = text[index + 1]
        simple = {"\"": '"', "\\": "\\", "/": "/", "b": "\b",
                  "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
        if escaped in simple:
            out.append(simple[escaped])
            index += 2
            continue
        if escaped == "u":
            if index + 5 >= len(text):
                break
            digits = text[index + 2:index + 6]
            try:
                codepoint = int(digits, 16)
            except ValueError:
                # 非法 JSON 会在最终 parse_responder 被拒绝；草稿宁可停在这里。
                break
            # JSON 可用两个 UTF-16 转义表达 emoji。单独把高 surrogate 塞给
            # Tk 会得到替代符/乱码，因此等待低 surrogate 到齐再一次性合并。
            if 0xD800 <= codepoint <= 0xDBFF:
                if index + 11 >= len(text) or text[index + 6:index + 8] != "\\u":
                    break
                low_digits = text[index + 8:index + 12]
                try:
                    low = int(low_digits, 16)
                except ValueError:
                    break
                if not 0xDC00 <= low <= 0xDFFF:
                    break
                out.append(chr(0x10000 + ((codepoint - 0xD800) << 10)
                               + (low - 0xDC00)))
                index += 12
                continue
            if 0xDC00 <= codepoint <= 0xDFFF:
                break
            out.append(chr(codepoint))
            index += 6
            continue
        break
    return sanitize_draft_text("".join(out)), False


def _stable_prefix(text, complete):
    """未结束 JSON 只交付到最后一个自然句段；完整值可交付尾段。"""
    value = str(text or "")
    if complete:
        return sanitize_draft_text(value)
    last = -1
    for index, char in enumerate(value):
        if char in _STOP_CHARS:
            last = index
    return sanitize_draft_text(value[:last + 1]) if last >= 0 else ""


class ReplyDraftExtractor:
    """按 SSE delta 增量提取稳定对白；返回本次新增加的可见文本。"""

    def __init__(self):
        self.raw = ""
        self.visible = ""

    def reset(self):
        """丢弃一次失败 SSE 尝试的全部临时内容，开始新的协议帧。"""
        self.raw = ""
        self.visible = ""

    def feed(self, delta):
        self.raw += str(delta or "")
        decoded, complete = _decode_reply_prefix(self.raw)
        stable = _stable_prefix(decoded, complete)
        if not stable.startswith(self.visible):
            # JSON 前缀解析应单调；服务异常/格式回滚时不把不确定内容发到 UI。
            return ""
        added = stable[len(self.visible):]
        self.visible = stable
        return added
