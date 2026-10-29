"""Versioned CJK lexical matching for the six/seven reference package."""

import re


SINGLE_CHAR_BOUNDARY = {
    "滚": True,
    "嗯": True,
    "哦": True,
}

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def _is_cjk(character):
    return bool(character and _CJK.match(character))


def word_hit_count(word, text):
    """Count matches using the frozen single-character CJK boundary table."""
    value = text or ""
    if not word:
        return 0
    if len(word) >= 2 or word not in SINGLE_CHAR_BOUNDARY:
        return value.count(word)
    count = 0
    for index, character in enumerate(value):
        if character != word:
            continue
        left_ok = index == 0 or not _is_cjk(value[index - 1])
        right_ok = index == len(value) - 1 or not _is_cjk(value[index + 1])
        if left_ok and right_ok:
            count += 1
    return count


def any_hit(words, text):
    """Return whether any lexical item has at least one valid match."""
    return any(word_hit_count(word, text) > 0 for word in words)


__all__ = ("SINGLE_CHAR_BOUNDARY", "any_hit", "word_hit_count")
