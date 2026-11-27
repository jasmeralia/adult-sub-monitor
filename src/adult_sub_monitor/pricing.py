from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_MANYVIDS_URL = re.compile(r"^https?://(?:www\.)?manyvids\.com/", re.IGNORECASE)
_MANYVIDS_VIDEO_ID = re.compile(
    r"^https?://(?:www\.)?manyvids\.com/Video/(\d+)(?:[/?#]|$)", re.IGNORECASE
)


def parse_price_cents(text: str | None) -> int | None:
    """Parse a dollar string to integer cents, rounding half-up."""
    if text is None:
        return None
    value = text.strip().removeprefix("$").replace(",", "").strip()
    if not value:
        return None
    try:
        amount = Decimal(value)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def resolve_manyvids_price(
    regular: str | None,
    discounted: str | None,
    *,
    on_sale: bool,
    free: bool,
) -> tuple[int | None, int | None]:
    """Return effective and list prices in cents for a ManyVids Price record."""
    list_cents = parse_price_cents(regular)
    if free:
        return 0, list_cents
    if list_cents is None:
        return None, None
    disc = parse_price_cents(discounted)
    if on_sale and disc is not None and 0 < disc < list_cents:
        return disc, list_cents
    return list_cents, list_cents


def is_manyvids_url(url: str) -> bool:
    return _MANYVIDS_URL.match(url) is not None


def manyvids_video_id_from_url(url: str) -> str | None:
    match = _MANYVIDS_VIDEO_ID.match(url)
    return match.group(1) if match else None
