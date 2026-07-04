import re

from adult_sub_monitor.models import Item

KeywordPatterns = list[tuple[str, "re.Pattern[str]"]]


def compile_blocked_keywords(keywords: list[str]) -> KeywordPatterns:
    return [(kw, re.compile(r"(?i)\b" + re.escape(kw) + r"\b")) for kw in keywords]


def find_blocked_keyword(item: Item, patterns: KeywordPatterns) -> str | None:
    fields: list[str] = [item.title]
    if item.description:
        fields.append(item.description)
    fields.extend(item.tags)
    for keyword, pattern in patterns:
        if any(pattern.search(f) for f in fields):
            return keyword
    return None
