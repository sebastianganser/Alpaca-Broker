"""Analyst price targets of ``upgrades_downgrades`` rows (pure helpers).

Yahoo returns three price-target fields next to the rating of every firm
action: ``currentPriceTarget``, ``priorPriceTarget`` and ``priceTargetAction``
(``Raises`` / ``Lowers`` / ``Maintains`` / ``Announces`` / ``Initiates`` ...).
The collector stores the complete row as text in ``analyst_ratings.raw_data``
(every value passed through ``str()``), so the numbers show up there as
``"275.0"``, ``"nan"``, ``"None"`` or ``"0.0"``.

Used by

* ``YFinanceClient.fetch_analyst_ratings`` (new rows fill the columns),
* the context pack loader (a row whose columns are still NULL falls back to
  ``raw_data``),
* ``scripts/repair/backfill_analyst_price_targets.py`` (fills the columns of
  the historical rows from ``raw_data``).

A price target is a positive price: Yahoo uses ``0`` / NaN as "no target", and
neither is a target, so both become ``None`` (NULL, not ``0``).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

#: Keys of the Yahoo row (``raw_data`` keeps them verbatim).
KEY_NEW = "currentPriceTarget"
KEY_OLD = "priorPriceTarget"
KEY_ACTION = "priceTargetAction"

#: Largest price target accepted (a mis-scaled value must not reach a table).
MAX_PRICE_TARGET = 1e7

_MISSING_TEXT = frozenset({"", "nan", "none", "null", "nat", "<na>", "n/a", "-"})


def parse_price_target(value: Any) -> float | None:
    """A price target as ``float``, or ``None`` if there is none.

    ``None``, NaN, infinity, text such as ``"nan"`` / ``"None"``, zero, negative
    and absurdly large values are no price target.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        if text.lower() in _MISSING_TEXT:
            return None
        try:
            number = float(text.replace(",", ""))
        except ValueError:
            return None
    else:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
    if not math.isfinite(number) or number <= 0 or number > MAX_PRICE_TARGET:
        return None
    return number


def parse_target_action(value: Any) -> str | None:
    """The ``priceTargetAction`` text, or ``None`` if Yahoo gave none."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    text = str(value).strip()
    if text.lower() in _MISSING_TEXT or text == "0" or text == "0.0":
        return None
    return text


def price_target_fields(
    source: Mapping[str, Any] | Any,
) -> tuple[float | None, float | None, str | None]:
    """``(new, old, action)`` of a Yahoo row or of a stored ``raw_data`` dict.

    ``source`` is anything with ``.get`` (a ``dict``, a pandas ``Series``).
    """
    getter = getattr(source, "get", None)
    if getter is None:
        return None, None, None
    return (
        parse_price_target(getter(KEY_NEW, None)),
        parse_price_target(getter(KEY_OLD, None)),
        parse_target_action(getter(KEY_ACTION, None)),
    )


__all__ = [
    "KEY_ACTION",
    "KEY_NEW",
    "KEY_OLD",
    "MAX_PRICE_TARGET",
    "parse_price_target",
    "parse_target_action",
    "price_target_fields",
]
