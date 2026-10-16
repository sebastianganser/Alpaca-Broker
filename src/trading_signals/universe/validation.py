"""Weekly universe validation against Alpaca (job ``universe_validation``).

Keeps ``universe.is_active`` honest: active tickers that Alpaca no longer
lists as tradable (delisted, acquired, renamed) are deactivated, so collectors
stop requesting them. Before this job existed, 10 delisted/renamed tickers
stayed active for months.

Rules
-----
* Not found / not tradable on Alpaca -> ``deactivate_ticker`` (never deleted).
* **Exception:** a ticker that still has an open ``index_membership`` interval
  is *not* deactivated. The index data is the source of truth for membership
  (the monthly index sync closes/re-keys it); the conflict is only reported.
* For every ticker found this way (deactivated or kept) Alpaca corporate
  actions of the last 365 days are looked up and written to the run notes as
  ``OLD->NEW (rename)`` / ``OLD->ACQ (merger)``. A rename whose new symbol is
  not in ``universe`` is a WARNING: the monthly index sync or the ARK
  collector normally onboards it.
* Safety nets: an implausibly small Alpaca asset list or a mass deactivation
  (> ``MAX_DEACTIVATION_SHARE`` of the active universe) aborts the run
  without touching anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_signals.db.models.index_membership import IndexMembership
from trading_signals.db.models.universe import Universe
from trading_signals.universe.alpaca_validator import AlpacaAssetValidator
from trading_signals.universe.corporate_actions import (
    AlpacaCorporateActions,
    CorporateEvents,
)
from trading_signals.universe.manager import UniverseManager
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

#: Corporate actions are looked up this many days back.
CORPORATE_ACTIONS_LOOKBACK_DAYS = 365

#: Alpaca lists several thousand active US equities; fewer means a broken response.
MIN_ASSET_COUNT = 1000

#: Deactivating more than this share of the active universe in one run is
#: treated as a data problem, not as delistings.
MAX_DEACTIVATION_SHARE = 0.10


class UniverseValidationError(RuntimeError):
    """Validation stopped before changing anything (implausible input)."""


@dataclass
class UniverseValidationResult:
    """Outcome of one validation run."""

    checked: int = 0
    deactivated: list[str] = field(default_factory=list)
    #: not tradable on Alpaca but with an open index_membership interval
    kept_open_interval: list[str] = field(default_factory=list)
    #: "OLD->NEW (rename)" / "OLD->ACQ (merger)" lines for the run notes
    corporate_actions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def notes(self) -> str:
        """One-line run summary (``collection_log.notes``)."""
        parts = [f"checked={self.checked}", f"deactivated={len(self.deactivated)}"]
        if self.deactivated:
            parts[-1] += f" ({', '.join(self.deactivated)})"
        if self.corporate_actions:
            parts.append("corporate actions: " + ", ".join(self.corporate_actions))
        if self.kept_open_interval:
            parts.append(
                "kept active (open index interval): "
                + ", ".join(self.kept_open_interval)
            )
        if self.warnings:
            parts.append("WARN: " + " | ".join(self.warnings))
        return "; ".join(parts)


def _open_interval_tickers(session: Session, tickers: list[str]) -> set[str]:
    """Tickers (of ``tickers``) with an open ``index_membership`` interval."""
    if not tickers:
        return set()
    rows = session.execute(
        select(IndexMembership.ticker)
        .where(IndexMembership.ticker.in_(tickers))
        .where(IndexMembership.valid_to.is_(None))
        .distinct()
    ).all()
    return {r[0] for r in rows}


def _universe_tickers(session: Session, tickers: list[str]) -> set[str]:
    """Tickers (of ``tickers``) that exist in ``universe`` (active or not)."""
    if not tickers:
        return set()
    rows = session.execute(
        select(Universe.ticker).where(Universe.ticker.in_(tickers))
    ).all()
    return {r[0] for r in rows}


def _describe(
    ticker: str, events: CorporateEvents
) -> tuple[list[str], list[str]]:
    """``(lines, new_symbols)`` for one ticker: renames first, then mergers."""
    lines: list[str] = []
    new_symbols: list[str] = []
    for rename in events.renames_of(ticker):
        new = rename.new_symbol.upper()
        lines.append(f"{ticker}->{new} (rename)")
        new_symbols.append(new)
    for merger in events.mergers_of(ticker):
        acquirer = (merger.acquirer_symbol or "cash").upper()
        lines.append(f"{ticker}->{acquirer} (merger)")
    return lines, new_symbols


def validate_universe(
    session: Session,
    validator: AlpacaAssetValidator | None = None,
    corporate_actions: AlpacaCorporateActions | None = None,
    today: date | None = None,
    deactivate: bool = True,
) -> UniverseValidationResult:
    """Validate the active universe against Alpaca (see module docstring).

    Args:
        session: Open session; the caller commits.
        validator / corporate_actions: Injectable clients (tests).
        today: Reference date for the corporate-action window.
        deactivate: ``False`` = report only.

    Raises:
        UniverseValidationError: implausible Alpaca response or mass
            deactivation -- nothing was changed.
    """
    today = today or date.today()
    manager = UniverseManager(session)
    result = UniverseValidationResult()

    tickers = [t.ticker for t in manager.get_active_tickers()]
    result.checked = len(tickers)

    validator = validator or AlpacaAssetValidator()
    assets = validator.fetch_all_assets()
    if len(assets) < MIN_ASSET_COUNT:
        raise UniverseValidationError(
            f"Alpaca returned only {len(assets)} assets (< {MIN_ASSET_COUNT}) -- "
            "response looks broken, nothing deactivated"
        )

    validation = validator.validate_tickers(tickers)
    problem = list(validation.not_found) + list(validation.not_tradeable)
    if tickers and len(problem) > MAX_DEACTIVATION_SHARE * len(tickers):
        raise UniverseValidationError(
            f"{len(problem)} of {len(tickers)} active tickers would be deactivated "
            f"(> {MAX_DEACTIVATION_SHARE:.0%}) -- aborted, check the Alpaca "
            "response / run scripts/validate_universe.py manually"
        )

    members = _open_interval_tickers(session, problem)
    to_deactivate = sorted(t for t in problem if t not in members)
    result.kept_open_interval = sorted(members)

    for ticker in to_deactivate:
        detail = validation.details.get(ticker, {}).get("status", "unknown")
        if deactivate:
            manager.deactivate_ticker(ticker)
        result.deactivated.append(ticker)
        logger.info(f"[universe_validation] deactivated {ticker} ({detail})")
    for ticker in result.kept_open_interval:
        msg = (
            f"{ticker} is not tradable on Alpaca but still has an open "
            "index_membership interval -- kept active (index data is the source "
            "of truth; the monthly index sync closes or re-keys it)"
        )
        logger.warning(f"[universe_validation] {msg}")
        result.warnings.append(msg)

    lookup = sorted(set(to_deactivate) | members)
    if lookup:
        _add_corporate_actions(session, result, lookup, corporate_actions, today)
    return result


def _add_corporate_actions(
    session: Session,
    result: UniverseValidationResult,
    tickers: list[str],
    client: AlpacaCorporateActions | None,
    today: date,
) -> None:
    """Fill ``result.corporate_actions`` and the rename warnings (never raises)."""
    try:
        client = client or AlpacaCorporateActions()
        events = client.fetch(
            tickers,
            start=today - timedelta(days=CORPORATE_ACTIONS_LOOKBACK_DAYS),
            end=today,
        )
    except Exception as e:
        msg = f"corporate actions lookup failed: {type(e).__name__}: {e}"
        logger.warning(f"[universe_validation] {msg}")
        result.warnings.append(msg)
        return

    renames: list[tuple[str, str]] = []
    for ticker in tickers:
        lines, new_symbols = _describe(ticker, events)
        result.corporate_actions.extend(lines)
        renames.extend((ticker, new) for new in new_symbols)

    known = _universe_tickers(session, sorted({new for _, new in renames}))
    for old, new in renames:
        if new not in known:
            msg = (
                f"{old}->{new} (rename): {new} is not in universe -- the monthly "
                "index sync or the ARK collector normally onboards it"
            )
            logger.warning(f"[universe_validation] {msg}")
            result.warnings.append(msg)
