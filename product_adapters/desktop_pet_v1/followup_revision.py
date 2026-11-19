"""Compatibility of a speculative reply across its own preceding output settlement."""
import json

VERSION = "self-output-followup-v1"
HEADER = "【本角色各条关系的当前心理状态】"
DEFERRED = "回应对象：此前已收到且尚未回应的原问题，以当前输入栏原文为准。"


def compatible_messages(previous, current):
    if (len(previous) != 2 or len(current) != 2 or previous[1] != current[1]
            or previous[0].get("role") != "system" or current[0].get("role") != "system"):
        return False
    old = previous[0]["content"].split("\n")
    new = current[0]["content"].split("\n")
    if DEFERRED in old or new.count(DEFERRED) != 1 or old.count(HEADER) != 1:
        return False
    new.remove(DEFERRED)
    i = old.index(HEADER) + 2
    if (i >= len(old) or i >= len(new) or old[:i] != new[:i] or old[i+1:] != new[i+1:]
            or not old[i-1].startswith("本轮表达资源对应：")):
        return False
    peer = old[i-1].removeprefix("本轮表达资源对应：").removesuffix("。")
    try:
        before, after = json.loads(old[i]), json.loads(new[i])
        if not isinstance(before,list) or not isinstance(after,list) or len(before) != len(after):
            return False
        for a,b in zip(before,after):
            if a == b:
                continue
            if a["关系对象"] != peer or len(a["心理状态词元"]) != len(b["心理状态词元"]):
                return False
            for x,y in zip(a["心理状态词元"],b["心理状态词元"]):
                if (x != y and x["kind"] == "continuous_direction" and x["prominence"] == "微弱"
                        and x["orientation"] in ("positive","negative") and y["orientation"] == "neutral"):
                    x["orientation"] = "neutral"
        return before == after
    except (KeyError, TypeError, ValueError):
        return False


def compatible_payloads(previous, current):
    return ({k:v for k,v in previous.items() if k != "messages"}
            == {k:v for k,v in current.items() if k != "messages"}
            and compatible_messages(previous["messages"],current["messages"]))
