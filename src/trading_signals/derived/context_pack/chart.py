"""Chart image of a ticker file: ``TICKER.png`` (matplotlib Agg, deterministic).

One image per full ticker file, drawn from the same real daily bars (up to the
pack session, nothing later) the text sections use:

* top panel: about one year (252 sessions) of daily candles, SMA50 and SMA200
  (computed with the bars before the window as warm-up), the 52-week high and
  low (dashed) and the support / resistance levels of
  :func:`~.technicals.support_resistance` (dotted, labelled ``S`` / ``R``);
* bottom panel: volume, coloured by the direction of the day, with its 20-day
  mean.

**Deterministic:** the same bars give the same bytes. The figure is built with
the object-oriented API only (no ``pyplot`` - the scheduler runs jobs in
threads and ``pyplot`` keeps global state), fonts are matplotlib's bundled
DejaVu Sans, every style is set explicitly, and the PNG is written by Pillow
from the RGB buffer with fixed settings (no timestamp, no library version).

**Ownership marker:** the PNG carries a ``Description`` text chunk
(:func:`chart_description`). The generator deletes an existing ``TICKER.png``
of an earlier run only if :func:`is_generated_chart` finds that marker - a
hand-made image with the same name is never touched.
"""

from __future__ import annotations

import io
import struct
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from trading_signals.derived.context_pack.fmt import clean_text
from trading_signals.derived.context_pack.rows import BarRow
from trading_signals.derived.context_pack.technicals import (
    LEVEL_SESSIONS,
    Level,
    PriceAnalysis,
    bars_frame,
)

#: Bump when the look of the chart changes in a way the skill should know.
CHART_FORMAT = 1
CHART_MARKER = "alpaca-broker context_pack chart"
CHART_SUFFIX = ".png"

#: Sessions shown (about one year) and the minimum for a chart.
CHART_SESSIONS = LEVEL_SESSIONS
MIN_CHART_BARS = 60
SMA_WINDOWS = (50, 200)
VOLUME_SMA = 20
#: Minimum distance (bars) between the first two month labels.
MIN_TICK_GAP = 16

WIDTH_IN, HEIGHT_IN, DPI = 10.0, 6.4, 100
#: White plate behind the line labels (they sit on top of candles).
LABEL_BOX = {"boxstyle": "square,pad=0.12", "fc": "white", "ec": "none", "alpha": 0.8}

UP = "#26a269"
DOWN = "#c01c28"
SMA50_COLOR = "#1c71d8"
SMA200_COLOR = "#e66100"
LEVEL_COLOR = "#813d9c"
RANGE_COLOR = "#5e5c64"
VOLUME_MEAN_COLOR = "#241f31"

_MONTHS = (
    "Jan",
    "Feb",
    "Mär",
    "Apr",
    "Mai",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Okt",
    "Nov",
    "Dez",
)


def chart_description(ticker: str, session: date) -> str:
    """The text stored in the PNG: marker, format, ticker and session."""
    return (
        f"{CHART_MARKER} v{CHART_FORMAT} ticker={ticker} session={session.isoformat()}"
    )


def chart_file_name(ticker: str) -> str:
    return f"{ticker}{CHART_SUFFIX}"


def _month_ticks(dates: Sequence[date]) -> tuple[list[int], list[str]]:
    """Index and label of the first bar of every month in the view.

    A first, partial month whose successor is closer than ``MIN_TICK_GAP`` bars
    is left out (its label would overlap); January and the first label carry the
    year.
    """
    firsts: list[tuple[int, date]] = []
    last: tuple[int, int] | None = None
    for i, d in enumerate(dates):
        if (d.year, d.month) != last:
            firsts.append((i, d))
            last = (d.year, d.month)
    if len(firsts) > 1 and firsts[1][0] - firsts[0][0] < MIN_TICK_GAP:
        firsts = firsts[1:]
    pos = [i for i, _ in firsts]
    labels = [
        _MONTHS[d.month - 1] + (f" {d.year}" if d.month == 1 or k == 0 else "")
        for k, (_, d) in enumerate(firsts)
    ]
    return pos, labels


def _fmt_volume(value: float, _pos: int) -> str:
    if value >= 1e6:
        return f"{value / 1e6:.0f}M"
    if value >= 1e3:
        return f"{value / 1e3:.0f}K"
    return f"{value:.0f}"


def _level_label(level: Level) -> str:
    return f"{'S' if level.kind == 'support' else 'R'} {level.price:.2f}"


def render_chart_png(
    ticker: str,
    company: str | None,
    session: date,
    bars: Sequence[BarRow],
    analysis: PriceAnalysis | None,
) -> bytes | None:
    """The chart as PNG bytes, or None if there are too few real bars.

    Args:
        bars: Real daily bars; bars after ``session`` are ignored.
        analysis: Range and levels shown as lines (None = candles only).
    """
    frame = bars_frame([b for b in bars if b.trade_date <= session])
    if len(frame) < MIN_CHART_BARS:
        return None
    close = frame["close"]
    sma = {w: close.rolling(w, min_periods=w).mean() for w in SMA_WINDOWS}
    n_view = min(CHART_SESSIONS, len(frame))
    view = frame.tail(n_view).reset_index(drop=True)
    first = len(frame) - n_view
    x = np.arange(n_view)
    o, h, lo, c = (view[k].to_numpy() for k in ("open", "high", "low", "close"))
    volume = np.nan_to_num(view["volume"].to_numpy(), nan=0.0)
    up = c >= o
    dates = list(view["date"])

    fig = Figure(figsize=(WIDTH_IN, HEIGHT_IN), dpi=DPI, facecolor="white")
    FigureCanvasAgg(fig)
    ax, axv = fig.subplots(
        2, 1, sharex=True, gridspec_kw={"height_ratios": [4.2, 1.0], "hspace": 0.04}
    )
    fig.subplots_adjust(left=0.05, right=0.93, top=0.93, bottom=0.07)

    # candles: wicks, then bodies (a doji gets a hairline body)
    for mask, color in ((up, UP), (~up, DOWN)):
        if mask.any():
            ax.vlines(x[mask], lo[mask], h[mask], color=color, linewidth=0.7)
            body = np.maximum(np.abs(c[mask] - o[mask]), c[mask] * 1e-4)
            ax.bar(
                x[mask], body, bottom=np.minimum(o[mask], c[mask]), width=0.7,
                color=color, linewidth=0,
            )  # fmt: skip
    for w, color in zip(SMA_WINDOWS, (SMA50_COLOR, SMA200_COLOR), strict=True):
        values = sma[w].to_numpy()[first:]
        if np.isfinite(values).any():
            ax.plot(x, values, color=color, linewidth=1.2, label=f"SMA{w}")

    def hline(
        price: float, label: str, color: str, style: str, below: bool = False
    ) -> None:
        """A horizontal line with its label at the right edge (above the line,
        or below it for the 52-week low so it cannot sit on a level's label)."""
        ax.axhline(price, color=color, linewidth=0.9, linestyle=style, alpha=0.85)
        ax.text(
            0.995, price, label, transform=ax.get_yaxis_transform(),
            ha="right", va="top" if below else "bottom", fontsize=7, color=color,
            bbox=LABEL_BOX,
        )  # fmt: skip

    if analysis is not None:
        if analysis.range52 is not None:
            hline(
                analysis.range52.high,
                f"52W-Hoch {analysis.range52.high:.2f}",
                RANGE_COLOR,
                "--",
            )
            hline(
                analysis.range52.low,
                f"52W-Tief {analysis.range52.low:.2f}",
                RANGE_COLOR,
                "--",
                below=True,
            )
        for level in analysis.levels:
            hline(level.price, _level_label(level), LEVEL_COLOR, ":")

    ax.margins(x=0.01, y=0.06)
    ax.yaxis.tick_right()
    ax.tick_params(axis="both", labelsize=8)
    ax.grid(True, axis="y", color="#deddda", linewidth=0.5)
    ax.legend(loc="upper left", fontsize=8, framealpha=0.85, edgecolor="none")
    shown = clean_text(company, 40) if company else ""
    title_name = f"{ticker} – {shown}" if shown and shown != "–" else ticker
    ax.set_title(
        f"{title_name}  |  Tageskerzen bis {dates[-1].isoformat()} (echte Bars), "
        "SMA50/SMA200, Swing-Levels",
        fontsize=9, loc="left",
    )  # fmt: skip

    colors = np.where(up, UP, DOWN)
    axv.bar(x, volume, width=0.8, color=colors, linewidth=0)
    vol_mean = (
        view["volume"].fillna(0.0).rolling(VOLUME_SMA, min_periods=VOLUME_SMA).mean()
    ).to_numpy()
    if np.isfinite(vol_mean).any():
        axv.plot(x, vol_mean, color=VOLUME_MEAN_COLOR, linewidth=0.9)
    axv.set_ylim(0, max(float(volume.max()), 1.0) * 1.08)
    axv.yaxis.tick_right()
    axv.yaxis.set_major_formatter(FuncFormatter(_fmt_volume))
    axv.tick_params(axis="both", labelsize=8)
    axv.grid(True, axis="y", color="#deddda", linewidth=0.5)
    axv.text(
        0.005, 0.9, "Volumen, Ø 20 T", transform=axv.transAxes, fontsize=7, va="top"
    )
    pos, labels = _month_ticks(dates)
    axv.set_xticks(pos, labels)

    canvas = fig.canvas
    canvas.draw()
    rgb = np.asarray(canvas.buffer_rgba())[..., :3]
    info = PngInfo()
    info.add_text("Description", chart_description(ticker, session))
    out = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(rgb), "RGB").save(
        out, format="PNG", optimize=True, pnginfo=info, dpi=(DPI, DPI)
    )
    return out.getvalue()


# ── ownership marker ─────────────────────────────────────────────────

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
#: The text chunks sit before the image data; this many bytes are enough.
_HEAD_BYTES = 64 * 1024


def png_text_chunks(head: bytes) -> dict[str, str]:
    """``tEXt`` chunks of the start of a PNG (stops at the image data)."""
    if not head.startswith(_PNG_SIGNATURE):
        return {}
    out: dict[str, str] = {}
    pos = len(_PNG_SIGNATURE)
    while pos + 8 <= len(head):
        length, kind = struct.unpack(">I4s", head[pos : pos + 8])
        if kind in (b"IDAT", b"IEND"):
            break
        data = head[pos + 8 : pos + 8 + length]
        if kind == b"tEXt" and b"\x00" in data and len(data) == length:
            key, _, value = data.partition(b"\x00")
            out[key.decode("latin-1")] = value.decode("latin-1")
        pos += 12 + length
    return out


def is_generated_chart(path: Path, ticker: str) -> bool:
    """True if ``path`` is a chart this generator wrote for ``ticker``.

    The ``Description`` chunk must carry :data:`CHART_MARKER` and
    ``ticker=<ticker>``; the file name alone never makes an image ours.
    """
    try:
        with path.open("rb") as fh:
            head = fh.read(_HEAD_BYTES)
    except OSError:
        return False
    text = png_text_chunks(head).get("Description", "")
    return text.startswith(CHART_MARKER) and f" ticker={ticker} " in f"{text} "


__all__ = [
    "CHART_FORMAT",
    "CHART_MARKER",
    "CHART_SUFFIX",
    "chart_description",
    "chart_file_name",
    "is_generated_chart",
    "png_text_chunks",
    "render_chart_png",
]
