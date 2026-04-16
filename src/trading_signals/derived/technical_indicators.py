"""Technical Indicators Computer – calculates TA indicators from price data.

Computes all standard technical analysis indicators for each ticker in the
universe using the pandas-ta library. Results are stored in the
technical_indicators table (Derived Layer).

Indicators computed:
  - SMA 20, 50, 200 (Simple Moving Averages)
  - EMA 12, 26 (Exponential Moving Averages)
  - RSI 14 (Relative Strength Index)
  - MACD (line, signal, histogram)
  - Bollinger Bands (upper, lower) with 20-period, 2σ
  - ATR 14 (Average True Range)
  - Volume SMA 20
  - Relative Strength vs SPY (Excess Return over 20 trading days)

Design:
  - Runs after PriceCollectorAlpaca (daily at 22:30 MEZ)
  - Computes indicators for a single target date (daily mode)
  - Can also backfill all historical dates (backfill mode)
  - Min-data checks: indicators only computed when enough history exists
  - SPY prices cached once, reused for all tickers
  - Idempotent: UPSERT pattern (ON CONFLICT DO UPDATE)
"""

from datetime import date

import pandas as pd
import pandas_ta as ta
from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_signals.db.models.prices import PriceDaily
from trading_signals.db.models.technical_indicators import TechnicalIndicator
from trading_signals.db.models.universe import Universe
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

# Maximum lookback needed (SMA 200 + buffer for EMA warmup)
MAX_LOOKBACK_DAYS = 250

# Minimum data requirements per indicator group
MIN_DAYS_SMA20 = 20
MIN_DAYS_SMA50 = 50
MIN_DAYS_SMA200 = 200
MIN_DAYS_RSI = 14
MIN_DAYS_MACD = 35  # EMA 26 + 9-period signal line
MIN_DAYS_BOLLINGER = 20
MIN_DAYS_ATR = 14
MIN_DAYS_VOLUME_SMA = 20
MIN_DAYS_RELATIVE_STRENGTH = 20

# SPY ticker for relative strength calculation
SPY_TICKER = "SPY"


class TechnicalIndicatorsComputer:
    """Compute technical analysis indicators from price data."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self._spy_df: pd.DataFrame | None = None  # Cached SPY prices

    def compute_all(
        self,
        target_date: date | None = None,
        backfill: bool = False,
    ) -> int:
        """Compute indicators for all active tickers.

        Args:
            target_date: Date to compute indicators for. Defaults to today.
            backfill: If True, compute indicators for ALL dates with price
                      data (used for initial historical computation).

        Returns:
            Total number of indicator records written.
        """
        if target_date is None:
            target_date = date.today()

        # Get all active tickers
        stmt = (
            select(Universe.ticker)
            .where(Universe.is_active.is_(True))
            .order_by(Universe.ticker)
        )
        tickers = [row[0] for row in self.session.execute(stmt).all()]
        logger.info(
            f"[technical_indicators] Computing for {len(tickers)} tickers "
            f"(target_date={target_date}, backfill={backfill})"
        )

        # Pre-load SPY prices for relative strength calculation
        self._spy_df = self._load_price_history(SPY_TICKER)
        if self._spy_df is None or len(self._spy_df) == 0:
            logger.warning(
                "[technical_indicators] No SPY data found – "
                "relative_strength_spy will be NULL"
            )

        total_written = 0
        errors = 0

        for i, ticker in enumerate(tickers, 1):
            try:
                if backfill:
                    written = self._compute_backfill(ticker)
                else:
                    written = 1 if self._compute_for_date(ticker, target_date) else 0
                total_written += written

                if i % 100 == 0:
                    self.session.flush()
                    logger.info(
                        f"[technical_indicators] Progress: "
                        f"{i}/{len(tickers)} tickers, "
                        f"{total_written} records written"
                    )
            except Exception as e:
                errors += 1
                logger.error(
                    f"[technical_indicators] Error for {ticker}: {e}"
                )
                continue

        self.session.flush()
        logger.info(
            f"[technical_indicators] Complete: {total_written} records written "
            f"across {len(tickers)} tickers ({errors} errors)"
        )
        return total_written

    def compute_catchup(self) -> int:
        """Compute indicators for all missing dates since last computation.

        Compares MAX(trade_date) in technical_indicators with
        MAX(trade_date) in prices_daily and computes indicators
        for every trading day in between.

        This prevents gaps when the daily job misses a run
        (container restart, scheduler issue, etc.).

        Returns:
            Total number of indicator records written.
        """
        from trading_signals.db.models.prices import PriceDaily
        from trading_signals.db.models.technical_indicators import TechnicalIndicator

        # Find latest dates in both tables
        last_ta = self.session.execute(
            select(func.max(TechnicalIndicator.trade_date))
        ).scalar()
        last_price = self.session.execute(
            select(func.max(PriceDaily.trade_date))
        ).scalar()

        if last_price is None:
            logger.warning("[technical_indicators] No price data found – nothing to compute")
            return 0

        if last_ta is None:
            # No TA data at all – compute for latest price date only
            logger.info(
                f"[technical_indicators] No existing TA data. "
                f"Computing for {last_price}"
            )
            return self.compute_all(target_date=last_price)

        if last_ta >= last_price:
            logger.info(
                f"[technical_indicators] Already up-to-date "
                f"(TA: {last_ta}, Prices: {last_price})"
            )
            return 0

        # Find all trading dates in prices_daily that are missing from
        # technical_indicators
        missing_dates_stmt = (
            select(PriceDaily.trade_date)
            .where(PriceDaily.trade_date > last_ta)
            .where(PriceDaily.trade_date <= last_price)
            .distinct()
            .order_by(PriceDaily.trade_date)
        )
        missing_dates = [
            row[0] for row in self.session.execute(missing_dates_stmt).all()
        ]

        if not missing_dates:
            logger.info("[technical_indicators] No missing dates to compute")
            return 0

        logger.info(
            f"[technical_indicators] Catch-up: computing {len(missing_dates)} "
            f"missing dates ({missing_dates[0]} → {missing_dates[-1]})"
        )

        total_written = 0
        for target_date in missing_dates:
            written = self.compute_all(target_date=target_date)
            total_written += written
            logger.info(
                f"[technical_indicators] Catch-up {target_date}: "
                f"{written} records"
            )

        return total_written

    def _compute_backfill(self, ticker: str) -> int:
        """Compute indicators for all available dates for a single ticker."""
        df = self._load_price_history(ticker)
        if df is None or len(df) < MIN_DAYS_RSI:
            return 0

        # Calculate all indicator columns on the full DataFrame
        indicators_df = self._calculate_indicators_dataframe(df)
        if indicators_df is None or indicators_df.empty:
            return 0

        written = 0
        for _, row in indicators_df.iterrows():
            if self._store_indicators(ticker, row):
                written += 1

        return written

    def _compute_for_date(self, ticker: str, target_date: date) -> bool:
        """Compute indicators for a single ticker on a single date.

        Returns True if a record was written.
        """
        df = self._load_price_history(ticker)
        if df is None or len(df) == 0:
            return False

        # Convert to Timestamp for DatetimeIndex lookup
        # (target_date from SQL is datetime.date, df.index is DatetimeIndex)
        ts = pd.Timestamp(target_date)

        # Check if target_date has price data
        if ts not in df.index:
            return False

        # Calculate all indicators on full history
        indicators_df = self._calculate_indicators_dataframe(df)
        if indicators_df is None or ts not in indicators_df.index:
            return False

        row = indicators_df.loc[ts]
        return self._store_indicators(ticker, row)

    def _load_price_history(
        self, ticker: str, since: date | None = None
    ) -> pd.DataFrame | None:
        """Load OHLCV price history for a ticker from the database.

        Returns a DataFrame indexed by trade_date with columns:
        open, high, low, close, volume.
        Returns None if no data found.
        """
        conditions = [PriceDaily.ticker == ticker]
        if since is not None:
            conditions.append(PriceDaily.trade_date >= since)

        stmt = (
            select(
                PriceDaily.trade_date,
                PriceDaily.open,
                PriceDaily.high,
                PriceDaily.low,
                PriceDaily.close,
                PriceDaily.volume,
            )
            .where(and_(*conditions))
            .order_by(PriceDaily.trade_date)
        )
        rows = self.session.execute(stmt).all()
        if not rows:
            return None

        df = pd.DataFrame(
            rows, columns=["trade_date", "open", "high", "low", "close", "volume"]
        )
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.set_index("trade_date")

        # Convert to float for pandas-ta
        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)

        return df

    def _calculate_indicators_dataframe(
        self, df: pd.DataFrame
    ) -> pd.DataFrame | None:
        """Calculate all TA indicators on a price DataFrame.

        Returns a DataFrame with the same index as the input, containing
        all indicator columns. Indicator values are NaN where insufficient
        data exists (handled naturally by pandas-ta).
        """
        n = len(df)
        if n < MIN_DAYS_RSI:
            return None

        result = pd.DataFrame(index=df.index)
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        # ── Moving Averages ──────────────────────────────────────────
        if n >= MIN_DAYS_SMA20:
            result["sma_20"] = ta.sma(close, length=20)
        if n >= MIN_DAYS_SMA50:
            result["sma_50"] = ta.sma(close, length=50)
        if n >= MIN_DAYS_SMA200:
            result["sma_200"] = ta.sma(close, length=200)

        result["ema_12"] = ta.ema(close, length=12)
        result["ema_26"] = ta.ema(close, length=26)

        # ── RSI ──────────────────────────────────────────────────────
        result["rsi_14"] = ta.rsi(close, length=14)

        # ── MACD ─────────────────────────────────────────────────────
        if n >= MIN_DAYS_MACD:
            macd_df = ta.macd(close, fast=12, slow=26, signal=9)
            if macd_df is not None and not macd_df.empty:
                result["macd"] = macd_df.iloc[:, 0]        # MACD line
                result["macd_histogram"] = macd_df.iloc[:, 1]  # Histogram
                result["macd_signal"] = macd_df.iloc[:, 2]  # Signal line

        # ── Bollinger Bands ──────────────────────────────────────────
        if n >= MIN_DAYS_BOLLINGER:
            bbands = ta.bbands(close, length=20, std=2)
            if bbands is not None and not bbands.empty:
                result["bollinger_lower"] = bbands.iloc[:, 0]  # Lower band
                # bbands.iloc[:, 1] = Mid band (= SMA 20, skip)
                result["bollinger_upper"] = bbands.iloc[:, 2]  # Upper band

        # ── ATR ──────────────────────────────────────────────────────
        if n >= MIN_DAYS_ATR:
            result["atr_14"] = ta.atr(high, low, close, length=14)

        # ── Volume SMA ───────────────────────────────────────────────
        if n >= MIN_DAYS_VOLUME_SMA:
            result["volume_sma_20"] = ta.sma(volume, length=20)

        # ── Relative Strength vs SPY ─────────────────────────────────
        if n >= MIN_DAYS_RELATIVE_STRENGTH:
            result["relative_strength_spy"] = self._calculate_relative_strength(
                df, MIN_DAYS_RELATIVE_STRENGTH
            )

        # Drop rows where ALL indicators are NaN (early rows with insufficient data)
        indicator_cols = [
            c for c in result.columns
            if c not in ("trade_date",)
        ]
        result = result.dropna(subset=indicator_cols, how="all")

        return result

    def _calculate_relative_strength(
        self, ticker_df: pd.DataFrame, period: int = 20
    ) -> pd.Series | None:
        """Calculate relative strength vs SPY (excess return).

        Formula: RS = ticker_return_Nd - spy_return_Nd
        where N = period (default 20 trading days).

        Positive values -> ticker outperformed SPY.
        Negative values -> ticker underperformed SPY.
        """
        if self._spy_df is None or len(self._spy_df) < period:
            return None

        spy_close = self._spy_df["close"]
        ticker_close = ticker_df["close"]

        # Percent returns over `period` trading days
        ticker_returns = ticker_close.pct_change(periods=period)
        spy_returns = spy_close.pct_change(periods=period)

        # Align indices (some tickers may not have data on all SPY dates)
        aligned_ticker, aligned_spy = ticker_returns.align(
            spy_returns, join="left"
        )

        # Excess return = ticker return - SPY return
        relative_strength = aligned_ticker - aligned_spy

        return relative_strength

    def _store_indicators(self, ticker: str, row: pd.Series) -> bool:
        """Store a single indicator row via UPSERT.

        Returns True if a record was written or updated.
        """
        trade_date = row.name
        if isinstance(trade_date, pd.Timestamp):
            trade_date = trade_date.date()

        values = {
            "ticker": ticker,
            "trade_date": trade_date,
        }

        # Add all indicator columns, converting NaN to None
        indicator_cols = [
            "sma_20", "sma_50", "sma_200", "ema_12", "ema_26",
            "rsi_14", "macd", "macd_signal", "macd_histogram",
            "bollinger_upper", "bollinger_lower", "atr_14",
            "volume_sma_20", "relative_strength_spy",
        ]

        for col in indicator_cols:
            val = row.get(col)
            if val is not None and pd.notna(val):
                values[col] = round(float(val), 4)
            else:
                values[col] = None

        # UPSERT: update all indicator values if row already exists
        update_cols = {
            k: v for k, v in values.items()
            if k not in ("ticker", "trade_date")
        }

        stmt = (
            pg_insert(TechnicalIndicator)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["ticker", "trade_date"],
                set_=update_cols,
            )
        )
        result = self.session.execute(stmt)
        return result.rowcount > 0
