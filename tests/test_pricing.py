import pytest

from adult_sub_monitor.pricing import (
    is_manyvids_url,
    manyvids_video_id_from_url,
    parse_price_cents,
    resolve_manyvids_price,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("24.99", 2499),
        ("0.00", 0),
        ("$5.99", 599),
        ("1,299.00", 129900),
        (" 7.5 ", 750),
        ("5.999", 600),
        ("", None),
        ("  ", None),
        (None, None),
        ("abc", None),
        ("-1.00", None),
        ("NaN", None),
        ("Infinity", None),
    ],
)
def test_parse_price_cents(value: str | None, expected: int | None) -> None:
    assert parse_price_cents(value) == expected


@pytest.mark.parametrize(
    ("regular", "discounted", "on_sale", "free", "expected"),
    [
        ("24.99", "21.24", True, False, (2124, 2499)),
        ("11.99", "0.00", False, False, (1199, 1199)),
        ("11.99", "0.00", True, False, (1199, 1199)),
        ("11.99", "12.99", True, False, (1199, 1199)),
        ("11.99", "9.99", False, False, (1199, 1199)),
        ("9.99", "0.00", False, True, (0, 999)),
        (None, None, False, False, (None, None)),
        ("", "0.00", False, False, (None, None)),
    ],
)
def test_resolve_manyvids_price(regular, discounted, on_sale, free, expected) -> None:
    assert (
        resolve_manyvids_price(regular, discounted, on_sale=on_sale, free=free)
        == expected
    )


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.manyvids.com/Video/7774614/blake-blossom", "7774614"),
        ("https://www.manyvids.com/Video/123", "123"),
        ("https://www.manyvids.com/Video/123/x?y=1", "123"),
        ("http://manyvids.com/Video/55/x", "55"),
        ("https://www.manyvids.com/Profile/1/x/Store/Videos", None),
        ("https://venus.sensual.love/members/content/1", None),
        ("https://www.manyvids.com/Video/abc/x", None),
    ],
)
def test_manyvids_video_id_from_url(url: str, expected: str | None) -> None:
    assert manyvids_video_id_from_url(url) == expected


def test_is_manyvids_url() -> None:
    assert is_manyvids_url("https://www.manyvids.com/Video/1/x")
    assert is_manyvids_url("https://manyvids.com/Profile/1/x")
    assert not is_manyvids_url("https://venus.sensual.love/members/content/1")
    assert not is_manyvids_url("https://notmanyvids.com/Video/1/x")
