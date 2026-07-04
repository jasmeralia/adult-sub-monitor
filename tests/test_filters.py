import pytest

from adult_sub_monitor.filters import compile_blocked_keywords, find_blocked_keyword
from adult_sub_monitor.models import Item


def _item(
    title: str = "Test Video",
    tags: list[str] | None = None,
    description: str | None = None,
) -> Item:
    return Item(
        site_name="test",
        item_id="1",
        title=title,
        url="https://example.com/v/1",
        tags=tags or [],
        description=description,
    )


@pytest.mark.parametrize(
    ("title", "tags", "description", "keywords", "expected"),
    [
        # keyword in title — exact match
        ("A pee video", [], None, ["pee"], "pee"),
        # keyword in title — case-insensitive
        ("A PEE Video", [], None, ["pee"], "pee"),
        # keyword in tag
        ("Nice video", ["Pee Fetish"], None, ["pee"], "pee"),
        # keyword in description
        ("Nice video", [], "She really needed to pee!", ["pee"], "pee"),
        # multi-word phrase in title
        ("Golden Shower Scene", [], None, ["golden shower"], "golden shower"),
        # multi-word phrase in tag
        ("Nice video", ["Golden Shower"], None, ["golden shower"], "golden shower"),
        # compound keyword as tag (un-split)
        ("Nice video", ["pisskink"], None, ["pisskink"], "pisskink"),
        # no match — unrelated content
        ("Nice regular video", ["POV", "Blowjob"], None, ["pee", "piss"], None),
        # word boundary: "speed" must not match "pee"
        ("Need for Speed", [], None, ["pee"], None),
        # word boundary: "steep" must not match "pee"
        ("Too steep", [], None, ["pee"], None),
        # word boundary: "happiness" must not match "piss"
        ("Pure happiness", [], None, ["piss"], None),
        # empty patterns — never matches
        ("A pee video", ["piss"], "golden shower", [], None),
        # first matching keyword is returned
        ("piss and pee video", [], None, ["pee", "piss"], "pee"),
        # description is None — no crash
        ("Nice video", [], None, ["pee"], None),
    ],
)
def test_find_blocked_keyword(
    title: str,
    tags: list[str],
    description: str | None,
    keywords: list[str],
    expected: str | None,
) -> None:
    patterns = compile_blocked_keywords(keywords)
    assert find_blocked_keyword(_item(title, tags, description), patterns) == expected
