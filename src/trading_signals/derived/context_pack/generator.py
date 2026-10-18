"""Context pack v2 generator (``docs/CONTEXT_PACK.md``, plan step 3).

``ContextPackGenerator(session).generate_daily(session_date)`` is the entry
point of the nightly chain step ``context_pack_generator``. It

1. loads the feature snapshots of the session (features only, no targets),
2. runs the neutral screen (``analysis/screen.py``: quality gate, evidence
   groups, buckets) - with exactly the gate the evaluation rebuilds
   (``forecast_review.gate_flags``), so ``screen.csv`` and the evaluation
   agree,
3. writes ``screen.csv`` (all gate tickers), the overview and one file per
   selected ticker (alphabetical, bucket NOT shown anywhere) plus its chart
   ``TICKER.png`` (``chart.py``),
4. stores the buckets in ``signals.pack_candidates`` (the evaluation compares
   Claude's forecasts with them; the caller commits).

All DB access goes through a :class:`~.loader.PackLoader`; everything else is
pure and unit tested without a database.
"""

from __future__ import annotations

import codecs
import re
import time
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from trading_signals.analysis.screen import (
    DEFAULT_CONFIG,
    GROUP_NAMES,
    ScreenConfig,
    screen_cross_section,
)
from trading_signals.analysis.stats import to_float as series_to_float
from trading_signals.config import get_settings
from trading_signals.derived.context_pack.chart import (
    CHART_SUFFIX,
    chart_file_name,
    is_generated_chart,
    render_chart_png,
)
from trading_signals.derived.context_pack.constants import (
    BARS_LOOKBACK_DAYS,
    FORECASTS_FILE,
    MAX_REQUESTED,
    OVERVIEW_FILE,
    RETURN_HORIZONS,
    RETURN_LOOKBACK_DAYS,
    SCREEN_FILE,
)
from trading_signals.derived.context_pack.loader import (
    FUNDAMENTALS_TEXT_COLUMNS,
    PackLoader,
    SqlPackLoader,
)
from trading_signals.derived.context_pack.model import (
    CrossSection,
    TickerContext,
    UniverseInfo,
    group_coverage,
    group_sizes,
    market_regime,
)
from trading_signals.derived.context_pack.overview import (
    FileEntry,
    OverviewData,
    render_overview,
)
from trading_signals.derived.context_pack.peers import select_peers
from trading_signals.derived.context_pack.pit import apply_pit
from trading_signals.derived.context_pack.rows import PeerRow, PeerTable
from trading_signals.derived.context_pack.screen_csv import screen_csv_text
from trading_signals.derived.context_pack.technicals import analyse_prices
from trading_signals.derived.context_pack.ticker_file import render_ticker_file
from trading_signals.derived.pack_candidates import (
    pack_candidate_rows,
    replace_pack_candidates,
)
from trading_signals.utils.logging import get_logger
from trading_signals.utils.market_calendar import trading_days

logger = get_logger(__name__)

_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
#: File names of ticker files: ``TICKER.md`` and the legacy ``NN_TICKER.md`` of
#: pack v1. A name alone never makes a file ours (``README.md`` has the same
#: shape): see :func:`_is_generated_ticker_file`.
_TICKER_FILE_RE = re.compile(r"^(?P<ticker>[A-Z][A-Z0-9.\-]{0,9})\.md$")
_LEGACY_FILE_RE = re.compile(r"^\d\d_(?P<ticker>[A-Z0-9.\-]+)\.md$")
#: ``TICKER.png`` - whether it is ours is decided by the marker inside the PNG
#: (``chart.is_generated_chart``).
_CHART_FILE_RE = re.compile(r"^(?P<ticker>[A-Z][A-Z0-9.\-]{0,9})\.png$")
#: Encoding of every file on the share (UTF-8 with BOM, as before).
ENCODING = "utf-8-sig"

#: Pack folders are world-writable (setgid kept): the forecast VM writes
#: forecasts.yaml & co. as uid 1000 through virtiofs while the container runs as
#: 99:100. Group write (2775, the VM user is in gid 100) is not enough there:
#: virtiofsd switches to the caller's uid/gid but may drop its supplementary
#: groups, whereas other-write was verified from the VM on 2026-10-18. The
#: share root itself is 777, like every Unraid share.
PACK_DIR_MODE = 0o2777


def _make_group_writable(path: Path) -> None:
    """Set :data:`PACK_DIR_MODE` on a pack folder the process owns (best effort)."""
    try:
        if (path.stat().st_mode & 0o7777) != PACK_DIR_MODE:
            path.chmod(PACK_DIR_MODE)
    except OSError as exc:  # not the owner (e.g. a root-created folder) - keep going
        logger.warning(
            f"[context_pack] cannot set mode {PACK_DIR_MODE:o} on {path}: {exc}"
        )


def parse_requested(text: str) -> list[str]:
    """Tickers of a ``requested_tickers.txt`` (one per line, ``#`` comments).

    Case-insensitive, duplicates removed (order kept); invalid entries are
    skipped.
    """
    out: list[str] = []
    for line in text.splitlines():
        token = line.split("#", 1)[0].strip().upper()
        if not token:
            continue
        if _TICKER_RE.match(token):
            if token not in out:
                out.append(token)
        else:
            logger.warning(
                f"[context_pack] ignoring invalid requested ticker {token!r}"
            )
    return out


def decode_requested(raw: bytes) -> str | None:
    """Text of a ``requested_tickers.txt``; ``None`` if no encoding fits.

    UTF-8 (with or without BOM) first, then UTF-16 (PowerShell and Notepad on
    Windows write it with a BOM), then Windows-1252 (the "ANSI" of Notepad). The
    file is typed by hand, so an umlaut in a comment must not make the whole
    list silently disappear.
    """
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        try:
            return raw.decode("utf-16")  # consumes the BOM
        except UnicodeDecodeError:
            return None
    for encoding in (ENCODING, "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def read_requested_tickers(root: Path) -> list[str]:
    """Requested tickers from ``<root>/requested_tickers.txt`` (empty if absent).

    An unreadable or undecodable file is logged and ignored: the pack is
    generated without requested tickers instead of the whole nightly step
    failing.
    """
    from trading_signals.derived.context_pack.constants import REQUESTED_FILE

    path = Path(root) / REQUESTED_FILE
    if not path.is_file():
        return []
    try:
        raw = path.read_bytes()
    except OSError as e:
        logger.warning(f"[context_pack] cannot read {path}: {e}")
        return []
    text = decode_requested(raw)
    if text is None:
        logger.warning(
            f"[context_pack] cannot decode {path} (not UTF-8, UTF-16 or "
            "Windows-1252) - requested tickers ignored"
        )
        return []
    return parse_requested(text)


def return_reference_dates(session_date: date) -> dict[int, date]:
    """``{h: session h sessions before the pack day}`` (``h = 0``: the day itself)."""
    sessions = trading_days(
        session_date - timedelta(days=RETURN_LOOKBACK_DAYS), session_date
    )
    if not sessions or sessions[-1] != session_date:
        raise ValueError(f"{session_date} is not an NYSE session")
    refs = {0: session_date}
    for h in RETURN_HORIZONS:
        if len(sessions) > h:
            refs[h] = sessions[-1 - h]
    return refs


def compute_returns(
    closes: pd.DataFrame, refs: Mapping[int, date]
) -> tuple[pd.DataFrame, pd.Series]:
    """Trailing returns per ticker and the closes of the pack day.

    Args:
        closes: Long frame ``ticker, trade_date, close`` with the reference
            dates (real closes only).
        refs: Output of :func:`return_reference_dates`.

    Returns:
        ``(returns, close_d)``: ``returns`` has one column ``ret_<h>d`` per
        horizon (NaN where a close is missing), indexed by ticker.
    """
    if closes.empty:
        return pd.DataFrame(), pd.Series(dtype="float64")
    wide = closes.pivot_table(
        index="ticker", columns="trade_date", values="close", aggfunc="last"
    )
    now = wide.get(refs[0])
    if now is None:
        return pd.DataFrame(index=wide.index), pd.Series(dtype="float64")
    out = pd.DataFrame(index=wide.index)
    for h in RETURN_HORIZONS:
        past = wide.get(refs.get(h)) if h in refs else None
        out[f"ret_{h}d"] = (now / past - 1.0) if past is not None else np.nan
    return out, now


def _numeric_table(snap: pd.DataFrame) -> pd.DataFrame:
    """Snapshot features as float columns indexed by ticker."""
    table = snap.drop(columns=["snapshot_date"], errors="ignore").set_index("ticker")
    return table.apply(series_to_float)


def _row_dict(row: pd.Series) -> dict[str, object]:
    """Row as dict with NaN / NA as None (bools stay bool)."""
    return {k: (None if pd.isna(v) else v) for k, v in row.items()}


class ContextPackGenerator:
    """Generates the daily context pack (pack format 2)."""

    def __init__(
        self,
        session: Session,
        output_dir: str | None = None,
        loader: PackLoader | None = None,
        config: ScreenConfig = DEFAULT_CONFIG,
    ):
        self.session = session
        self.output_dir = Path(output_dir or get_settings().CONTEXT_PACK_PATH)
        self.loader: PackLoader = loader or SqlPackLoader(session)
        self.config = config
        #: Why the last :meth:`generate_daily` call left an existing pack alone
        #: (``None``: it generated the pack).
        self.skip_reason: str | None = None

    # ── public ───────────────────────────────────────────────────────

    def generate_daily(
        self,
        target_date: date,
        requested: Iterable[str] | None = None,
        force: bool = False,
    ) -> int:
        """Write the pack of ``target_date``; return the ticker files written.

        Args:
            requested: Tickers that always get a full file; default: the
                file ``requested_tickers.txt`` in the pack root (if present).
            force: Regenerate although the pack has been assessed (operator
                override for a repair; the nightly chain never sets it).

        A pack that Claude may already have worked on is left alone: if the
        session folder holds a ``forecasts.yaml`` or ``pack_candidates`` rows of
        the session exist, nothing is written, replaced or deleted -
        regenerating would change the files and the shortlist the forecasts
        are judged against. The call then returns 0 and sets
        :attr:`skip_reason`.

        The pack candidates are written to the session (flushed, not
        committed: the caller owns the transaction).
        """
        started = time.monotonic()
        self.skip_reason = None
        if not force:
            kept = self._assessed_reason(target_date)
            if kept:
                self.skip_reason = kept
                logger.warning(
                    f"[context_pack] {target_date}: pack kept, not regenerated "
                    f"({kept})"
                )
                return 0
        snap = self.loader.snapshots(target_date)
        if snap.empty:
            logger.warning(
                f"[context_pack] No feature snapshots for {target_date} — skipping"
            )
            return 0
        snap = snap.reset_index(drop=True)

        wanted = (
            parse_requested("\n".join(requested))
            if requested is not None
            else read_requested_tickers(self.output_dir)
        )
        req_used, req_ignored = wanted[:MAX_REQUESTED], wanted[MAX_REQUESTED:]
        snap_tickers = set(snap["ticker"])
        missing_requested = [t for t in req_used if t not in snap_tickers]

        screened = screen_cross_section(
            snap, as_of=target_date, config=self.config, requested=req_used
        )
        gated = screened.set_index("ticker")["gate_ok"]
        selected = sorted(screened.loc[screened["bucket"] != "", "ticker"])

        info = self.loader.universe(sorted(snap_tickers))
        fund = self.loader.fundamentals(target_date)
        refs = return_reference_dates(target_date)
        returns, close_d = compute_returns(
            self.loader.closes(sorted(set(refs.values()))), refs
        )
        raw = self.loader.raw_sources(target_date, selected)

        sectors = pd.Series({t: i.sector for t, i in info.items()}, dtype="object")
        table = _numeric_table(snap)
        for col in returns.columns:
            table[col] = returns[col].reindex(table.index)
        for col in fund.columns:
            if col not in (
                "ticker",
                "snapshot_date",
                "most_recent_quarter",
                *FUNDAMENTALS_TEXT_COLUMNS,
            ):
                table[f"fund_{col}"] = pd.to_numeric(
                    fund[col], errors="coerce"
                ).reindex(table.index)
        xs = CrossSection(table, gated, sectors)

        cov = group_coverage(snap)
        cov.index = snap["ticker"]
        sizes = group_sizes(self.config)
        market = market_regime(snap)
        spy = {h: _ret(returns, "SPY", h) for h in RETURN_HORIZONS}

        by_ticker = screened.set_index("ticker")
        snap_rows = snap.set_index("ticker", drop=False)
        sessions = trading_days(
            target_date - timedelta(days=BARS_LOOKBACK_DAYS + 10), target_date
        )
        gate_tickers = set(gated[gated].index)
        caps = _column_values(table, "fund_market_cap")
        files: dict[str, str] = {}
        charts: dict[str, bytes] = {}
        chart_seconds = 0.0
        for ticker in selected:
            fund_row = fund.loc[ticker].to_dict() if ticker in fund.index else {}
            raw_t = apply_pit(raw.get(ticker), target_date)
            price = analyse_prices(raw_t.bars, sessions, target_date)
            started_chart = time.monotonic()
            png = self._chart(ticker, info, target_date, raw_t, price)
            chart_seconds += time.monotonic() - started_chart
            if png is not None:
                charts[ticker] = png
            ctx = TickerContext(
                ticker=ticker,
                session=target_date,
                row=_row_dict(snap_rows.loc[ticker]),
                info=info.get(ticker, UniverseInfo()),
                gate_ok=bool(by_ticker.loc[ticker, "gate_ok"]),
                gate_reasons=str(by_ticker.loc[ticker, "gate_reasons"] or ""),
                xs=xs,
                fund=fund_row,
                fund_date=_to_date(fund_row.get("snapshot_date")),
                close=_none(close_d.get(ticker)) if len(close_d) else None,
                returns={h: _ret(returns, ticker, h) for h in RETURN_HORIZONS},
                spy_returns=spy,
                raw=raw_t,
                market=market,
                coverage={
                    g: (int(cov.loc[ticker, g]), sizes[g][0]) for g in GROUP_NAMES
                },
                sessions=sessions,
                price=price,
                chart_file=chart_file_name(ticker) if png is not None else None,
                peer_table=_peer_table(ticker, info, gate_tickers, caps, table),
            )
            files[ticker] = render_ticker_file(ctx)

        overview = render_overview(
            self._overview_data(
                target_date,
                snap,
                screened,
                cov,
                sizes,
                market,
                spy,
                selected,
                info,
                missing_requested,
                req_ignored,
                len(charts),
            )
        )
        csv_text = screen_csv_text(
            screened, self._csv_extra(table, info, close_d, returns)
        )

        # DB rows first (flushed): a failing file write raises and the caller's
        # transaction is rolled back, so no candidate exists without its file.
        replace_pack_candidates(
            self.session, target_date, pack_candidate_rows(screened, target_date)
        )
        self._write(target_date, files, overview, csv_text, charts)
        logger.info(
            f"[context_pack] {target_date}: {len(files)} ticker files, "
            f"{len(charts)} charts ({chart_seconds:.1f} s), "
            f"{int(gated.sum())} gate tickers in {SCREEN_FILE} "
            f"({time.monotonic() - started:.1f} s)"
        )
        return len(files)

    # ── helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _chart(ticker, info, target_date, raw_t, price) -> bytes | None:
        """The chart of one ticker; a failure is logged and costs only the image."""
        try:
            return render_chart_png(
                ticker,
                info.get(ticker, UniverseInfo()).company_name,
                target_date,
                raw_t.bars,
                price,
            )
        except Exception as e:  # never let a drawing problem fail the whole pack
            logger.warning(
                f"[context_pack] {target_date}: no chart for {ticker}: "
                f"{type(e).__name__}: {e}"
            )
            return None

    def _assessed_reason(self, target_date: date) -> str | None:
        """Why the pack of ``target_date`` must not be regenerated (or ``None``)."""
        if (self.output_dir / target_date.isoformat() / FORECASTS_FILE).exists():
            return f"{FORECASTS_FILE} exists"
        if self.loader.has_pack_candidates(target_date):
            return "pack_candidates exist"
        return None

    def _overview_data(
        self,
        target_date,
        snap,
        screened,
        cov,
        sizes,
        market,
        spy,
        selected,
        info,
        missing_requested,
        ignored_requested,
        n_charts=0,
    ) -> OverviewData:
        gate_ok = screened["gate_ok"].to_numpy()
        gate_failures: dict[str, int] = {}
        for reasons in screened.loc[~screened["gate_ok"], "gate_reasons"]:
            for r in str(reasons).split(";"):
                if r:
                    gate_failures[r] = gate_failures.get(r, 0) + 1
        gated_tickers = screened.loc[gate_ok, "ticker"]
        group_cov = {}
        for g in GROUP_NAMES:
            counts = cov.loc[cov.index.isin(gated_tickers), g]
            group_cov[g] = float((counts >= sizes[g][1]).mean()) if len(counts) else 0.0
        return OverviewData(
            session=target_date,
            n_snapshots=len(snap),
            n_gated=int(gate_ok.sum()),
            gate_failures=gate_failures,
            group_coverage=group_cov,
            market=market,
            spy_returns=spy,
            files=[
                FileEntry(
                    t,
                    info.get(t, UniverseInfo()).company_name,
                    info.get(t, UniverseInfo()).sector,
                )
                for t in selected
            ],
            missing_requested=missing_requested,
            ignored_requested=ignored_requested,
            n_charts=n_charts,
        )

    @staticmethod
    def _csv_extra(table, info, close_d, returns) -> pd.DataFrame:
        """Per-ticker columns of ``screen.csv`` besides the screen result."""
        idx = table.index
        extra = pd.DataFrame(index=idx)
        for col, attr in (
            ("company", "company_name"),
            ("sector", "sector"),
        ):
            extra[col] = [getattr(info.get(t, UniverseInfo()), attr) for t in idx]
        extra["close"] = close_d.reindex(idx) if len(close_d) else np.nan
        for h in (20, 60):
            col = f"ret_{h}d"
            extra[col] = returns[col].reindex(idx) if col in returns else np.nan
        extra["dollar_volume_20d"] = table.get("dollar_volume_20d")
        extra["earnings_days_until"] = table.get("earnings_days_until")
        return extra

    def _write(
        self,
        target_date: date,
        files: Mapping[str, str],
        overview: str,
        csv_text: str,
        charts: Mapping[str, bytes] | None = None,
    ) -> None:
        day_dir = self.output_dir / target_date.isoformat()
        day_dir.mkdir(parents=True, exist_ok=True)
        _make_group_writable(self.output_dir)
        _make_group_writable(day_dir)
        # drop the ticker files and charts of an earlier, unpublished run - only
        # files this generator wrote (never forecasts.yaml, README.md, a
        # hand-made image or any other file)
        for old in day_dir.glob("*.md"):
            if old.name != OVERVIEW_FILE and _is_generated_ticker_file(old):
                old.unlink()
        for old in day_dir.glob(f"*{CHART_SUFFIX}"):
            if _is_generated_chart_file(old):
                old.unlink()
        for ticker, png in (charts or {}).items():
            (day_dir / chart_file_name(ticker)).write_bytes(png)
        for ticker, text in files.items():
            (day_dir / f"{ticker}.md").write_text(text, encoding=ENCODING)
        (day_dir / SCREEN_FILE).write_text(csv_text, encoding=ENCODING)
        # the overview is the entry point and is written last: a folder that
        # has it is complete
        (day_dir / OVERVIEW_FILE).write_text(overview, encoding=ENCODING)


def _is_generated_ticker_file(path: Path) -> bool:
    """True if ``path`` is a ticker file written by the pack generator.

    The name must match (``TICKER.md``, or the ``NN_TICKER.md`` of pack v1) AND
    the frontmatter must carry the generator's marker: ``pack_format`` plus the
    ticker of the file name (v1: ``rank``). ``README.md`` and every other
    hand-made file fail the content check and are kept.
    """
    match = _TICKER_FILE_RE.match(path.name)
    legacy = _LEGACY_FILE_RE.match(path.name) if match is None else None
    if match is None and legacy is None:
        return False
    ticker = (match or legacy).group("ticker")
    try:
        with path.open("r", encoding=ENCODING, errors="replace") as fh:
            head = [fh.readline().strip() for _ in range(6)]
    except OSError:
        return False
    if not head or head[0] != "---":
        return False
    if match is not None:
        return head[1].startswith("pack_format:") and f"ticker: {ticker}" in head
    return any(line.startswith("rank:") for line in head)


def _is_generated_chart_file(path: Path) -> bool:
    """True if ``path`` is a ``TICKER.png`` written by the pack generator.

    Name pattern AND the ownership marker inside the PNG (``chart.py``): a
    hand-made image, even one named like a chart, is kept.
    """
    match = _CHART_FILE_RE.match(path.name)
    return match is not None and is_generated_chart(path, match.group("ticker"))


def _column_values(table: pd.DataFrame, column: str) -> dict[str, float | None]:
    """``{ticker: value}`` of a numeric column (NaN -> None)."""
    if column not in table.columns:
        return {}
    return {t: _none(v) for t, v in table[column].items()}


def _peer_table(
    ticker: str,
    info: Mapping[str, UniverseInfo],
    gate: set[str],
    caps: Mapping[str, float | None],
    table: pd.DataFrame,
) -> PeerTable:
    """The peer section data of ``ticker`` (``peers.select_peers``)."""
    selection = select_peers(ticker, info, gate, caps)

    def row(t: str) -> PeerRow:
        def value(col: str) -> float | None:
            if col not in table.columns or t not in table.index:
                return None
            return _none(table.at[t, col])

        return PeerRow(
            ticker=t,
            company=info.get(t, UniverseInfo()).company_name,
            market_cap=caps.get(t),
            ret_60d=value("ret_60d"),
            forward_pe=value("forward_pe"),
            eps_revision_30d=value("eps_revision_pct_30d"),
        )

    return PeerTable(
        industry=selection.industry,
        sector=selection.sector,
        n_industry_pool=selection.n_industry_pool,
        n_from_industry=len(selection.industry_peers),
        n_from_sector=len(selection.sector_peers),
        own=row(ticker),
        peers=tuple(row(t) for t in selection.tickers),
    )


def _ret(returns: pd.DataFrame, ticker: str, h: int) -> float | None:
    col = f"ret_{h}d"
    if col not in returns.columns or ticker not in returns.index:
        return None
    return _none(returns.loc[ticker, col])


def _none(x) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if np.isfinite(v) else None


def _to_date(x) -> date | None:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return None
    if isinstance(x, pd.Timestamp):
        return x.date()
    return x if isinstance(x, date) else None


__all__ = [
    "ContextPackGenerator",
    "compute_returns",
    "decode_requested",
    "parse_requested",
    "read_requested_tickers",
    "return_reference_dates",
]
