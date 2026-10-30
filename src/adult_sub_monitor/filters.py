import re

from adult_sub_monitor.models import Item
from adult_sub_monitor.tags import split_words

KeywordPatterns = list[tuple[str, "re.Pattern[str]"]]


# Joins the words of a keyword; optional so "deepthroat" matches "Deep Throat" too.
_WORD_GAP = r"[\s_-]*"


def _keyword_pattern(keyword: str) -> "re.Pattern[str]":
    """Match a keyword however the text spaces it out.

    Tags are split into words for display ("Deep Penetration") and ManyVids spells
    the same tag differently from video to video, so "deeppenetration",
    "deep penetration" and "Deep-Penetration" all have to hit the same keyword.
    """
    words = split_words(keyword) or [keyword]
    body = _WORD_GAP.join(re.escape(word) for word in words)
    return re.compile(r"(?i)\b" + body + r"\b")


def compile_blocked_keywords(keywords: list[str]) -> KeywordPatterns:
    return [(kw, _keyword_pattern(kw)) for kw in keywords]


def find_blocked_keyword(item: Item, patterns: KeywordPatterns) -> str | None:
    fields: list[str] = [item.title]
    if item.description:
        fields.append(item.description)
    fields.extend(item.tags)
    for keyword, pattern in patterns:
        if any(pattern.search(f) for f in fields):
            return keyword
    return None
