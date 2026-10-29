"""Reference-model semantic request and parser; no provider or state authority.

The prompt is a versioned package resource. The provider adapter controls the
actual network call and runtime model identity. This module cannot read root
state, raw dialogue tails, Actor, Store, tick, or Writer results.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

from .semantic_projection import coerce_projection


SEMANTIC_REQUEST_VERSION = "cx67-semantic-request-v1"
SEMANTIC_PROMPT_PATH = Path(__file__).with_name("resources") / "semantic_projection.md"
SEMANTIC_TEMPERATURE = 0.2


def _strip_fences(raw):
    text = str(raw or "").strip()
    matched = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.S)
    return matched.group(1) if matched else text


def parse_semantic_response(raw, *, event_kind="user_input"):
    """Parse one provider result into the existing bounded measurement contract."""
    try:
        data = json.loads(_strip_fences(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return coerce_projection(data, event_kind=event_kind)


def build_semantic_request(*, text, recall_hint, event_kind="user_input"):
    """Return the frozen request for one committed event and eligible recall."""
    recall = str(recall_hint or "").strip() or "（无本轮召回）"
    kind = str(event_kind or "user_input")
    label = ("当前角色自身已提交输出" if kind == "npc_output"
             else "当前已投递的对端参与者消息" if kind in {
                 "human_input", "peer_input", "simulation_input"}
             else "当前用户原文")
    user = (
        "【已召回上下文】\n" + recall
        + "\n\n【" + label + "】\n" + str(text or "")
    )
    return SEMANTIC_PROMPT_PATH.read_text(encoding="utf-8"), user


class CxSixSevenSemanticCodec:
    """Package-owned request/parser surface; provider remains outside the package."""

    request_version = SEMANTIC_REQUEST_VERSION
    require_json = True

    @property
    def temperature(self):
        return SEMANTIC_TEMPERATURE

    def build_request(self, *, text, recall_hint, event_kind):
        return build_semantic_request(
            text=text, recall_hint=recall_hint, event_kind=event_kind)

    def parse_response(self, raw, *, event_kind):
        return parse_semantic_response(raw, event_kind=event_kind)
