"""Small Markdown cell formatters shared by the analysis reports."""

from __future__ import annotations

from typing import Any

#: Placeholder for missing values.
DASH = "–"


def pct(x: Any, digits: int = 2, signed: bool = True) -> str:
    """Fraction as percent (``0.0123`` -> ``+1.23 %``); NaN / None -> dash."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return DASH
    if v != v:
        return DASH
    return f"{v * 100:+.{digits}f} %" if signed else f"{v * 100:.{digits}f} %"


def num(x: Any, digits: int = 2) -> str:
    """Plain number with ``digits`` decimals; NaN / None -> dash."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return DASH
    return DASH if v != v else f"{v:.{digits}f}"


def ci_cell(
    est: Any, lo: Any, hi: Any, digits: int = 2, mark: bool = True,
    signed: bool = True,
) -> str:
    """``est [lo ... hi]`` in percent with a significance marker.

    ``✅`` = the whole interval is above zero, ``❌`` = below, otherwise
    ``n. s.``; a missing estimate gives a dash, a missing interval (too few
    dates for a batch jackknife) the bare estimate with a note. With
    ``mark=False`` (too little data for a verdict, or a descriptive figure)
    the interval is shown without any marker. ``signed=False`` drops the
    plus sign (rates).
    """
    try:
        e, lo_f, hi_f = float(est), float(lo), float(hi)
    except (TypeError, ValueError):
        return pct(est, digits, signed) if est is not None else DASH
    if e != e:
        return DASH
    if lo_f != lo_f or hi_f != hi_f:
        return f"{pct(e, digits, signed)} (KI zu wenig Daten)"
    text = (f"{pct(e, digits, signed)} [{pct(lo_f, digits, signed)} … "
            f"{pct(hi_f, digits, signed)}]")
    if not mark:
        return text
    sig = "✅" if lo_f > 0 else ("❌" if hi_f < 0 else "n. s.")
    return f"{text} {sig}"
