"""Initialize the ticker universe with S&P 100 constituents.

This script populates the signals.universe table with the starting set
of tickers. It is idempotent – running it multiple times is safe.

Usage:
    uv run python scripts/init_universe.py
"""

import sys
from pathlib import Path

# Ensure src/ is in the path when running as a script
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from trading_signals.db.session import get_session
from trading_signals.universe.manager import UniverseManager
from trading_signals.utils.logging import get_logger, setup_logging

setup_logging()
logger = get_logger(__name__)

# S&P 100 constituents (as of April 2026)
# Source: https://en.wikipedia.org/wiki/S%26P_100
SP100_TICKERS = [
    {"ticker": "AAPL", "company_name": "Apple Inc.", "sector": "Technology", "exchange": "NASDAQ"},
    {"ticker": "ABBV", "company_name": "AbbVie Inc.", "sector": "Healthcare", "exchange": "NYSE"},
    {"ticker": "ABT", "company_name": "Abbott Laboratories", "sector": "Healthcare", "exchange": "NYSE"},
    {"ticker": "ACN", "company_name": "Accenture plc", "sector": "Technology", "exchange": "NYSE"},
    {"ticker": "ADBE", "company_name": "Adobe Inc.", "sector": "Technology", "exchange": "NASDAQ"},
    {"ticker": "AIG", "company_name": "American International Group", "sector": "Financials", "exchange": "NYSE"},
    {"ticker": "AMD", "company_name": "Advanced Micro Devices", "sector": "Technology", "exchange": "NASDAQ"},
    {"ticker": "AMGN", "company_name": "Amgen Inc.", "sector": "Healthcare", "exchange": "NASDAQ"},
    {"ticker": "AMT", "company_name": "American Tower Corp.", "sector": "Real Estate", "exchange": "NYSE"},
    {"ticker": "AMZN", "company_name": "Amazon.com Inc.", "sector": "Consumer Discretionary", "exchange": "NASDAQ"},
    {"ticker": "AVGO", "company_name": "Broadcom Inc.", "sector": "Technology", "exchange": "NASDAQ"},
    {"ticker": "AXP", "company_name": "American Express Co.", "sector": "Financials", "exchange": "NYSE"},
    {"ticker": "BA", "company_name": "Boeing Co.", "sector": "Industrials", "exchange": "NYSE"},
    {"ticker": "BAC", "company_name": "Bank of America Corp.", "sector": "Financials", "exchange": "NYSE"},
    {"ticker": "BK", "company_name": "Bank of New York Mellon", "sector": "Financials", "exchange": "NYSE"},
    {"ticker": "BLK", "company_name": "BlackRock Inc.", "sector": "Financials", "exchange": "NYSE"},
    {"ticker": "BMY", "company_name": "Bristol-Myers Squibb", "sector": "Healthcare", "exchange": "NYSE"},
    {"ticker": "BRK.B", "company_name": "Berkshire Hathaway Inc.", "sector": "Financials", "exchange": "NYSE"},
    {"ticker": "C", "company_name": "Citigroup Inc.", "sector": "Financials", "exchange": "NYSE"},
    {"ticker": "CAT", "company_name": "Caterpillar Inc.", "sector": "Industrials", "exchange": "NYSE"},
    {"ticker": "CHTR", "company_name": "Charter Communications", "sector": "Communication Services", "exchange": "NASDAQ"},
    {"ticker": "CL", "company_name": "Colgate-Palmolive Co.", "sector": "Consumer Staples", "exchange": "NYSE"},
    {"ticker": "CMCSA", "company_name": "Comcast Corp.", "sector": "Communication Services", "exchange": "NASDAQ"},
    {"ticker": "COF", "company_name": "Capital One Financial", "sector": "Financials", "exchange": "NYSE"},
    {"ticker": "COP", "company_name": "ConocoPhillips", "sector": "Energy", "exchange": "NYSE"},
    {"ticker": "COST", "company_name": "Costco Wholesale Corp.", "sector": "Consumer Staples", "exchange": "NASDAQ"},
    {"ticker": "CRM", "company_name": "Salesforce Inc.", "sector": "Technology", "exchange": "NYSE"},
    {"ticker": "CSCO", "company_name": "Cisco Systems Inc.", "sector": "Technology", "exchange": "NASDAQ"},
    {"ticker": "CVS", "company_name": "CVS Health Corp.", "sector": "Healthcare", "exchange": "NYSE"},
    {"ticker": "CVX", "company_name": "Chevron Corp.", "sector": "Energy", "exchange": "NYSE"},
    {"ticker": "DE", "company_name": "Deere & Co.", "sector": "Industrials", "exchange": "NYSE"},
    {"ticker": "DHR", "company_name": "Danaher Corp.", "sector": "Healthcare", "exchange": "NYSE"},
    {"ticker": "DIS", "company_name": "Walt Disney Co.", "sector": "Communication Services", "exchange": "NYSE"},
    {"ticker": "DOW", "company_name": "Dow Inc.", "sector": "Materials", "exchange": "NYSE"},
    {"ticker": "DUK", "company_name": "Duke Energy Corp.", "sector": "Utilities", "exchange": "NYSE"},
    {"ticker": "EMR", "company_name": "Emerson Electric Co.", "sector": "Industrials", "exchange": "NYSE"},
    {"ticker": "EXC", "company_name": "Exelon Corp.", "sector": "Utilities", "exchange": "NASDAQ"},
    {"ticker": "F", "company_name": "Ford Motor Co.", "sector": "Consumer Discretionary", "exchange": "NYSE"},
    {"ticker": "FDX", "company_name": "FedEx Corp.", "sector": "Industrials", "exchange": "NYSE"},
    {"ticker": "GD", "company_name": "General Dynamics Corp.", "sector": "Industrials", "exchange": "NYSE"},
    {"ticker": "GE", "company_name": "GE Aerospace", "sector": "Industrials", "exchange": "NYSE"},
    {"ticker": "GILD", "company_name": "Gilead Sciences Inc.", "sector": "Healthcare", "exchange": "NASDAQ"},
    {"ticker": "GM", "company_name": "General Motors Co.", "sector": "Consumer Discretionary", "exchange": "NYSE"},
    {"ticker": "GOOG", "company_name": "Alphabet Inc. (Class C)", "sector": "Communication Services", "exchange": "NASDAQ"},
    {"ticker": "GOOGL", "company_name": "Alphabet Inc. (Class A)", "sector": "Communication Services", "exchange": "NASDAQ"},
    {"ticker": "GS", "company_name": "Goldman Sachs Group", "sector": "Financials", "exchange": "NYSE"},
    {"ticker": "HD", "company_name": "Home Depot Inc.", "sector": "Consumer Discretionary", "exchange": "NYSE"},
    {"ticker": "HON", "company_name": "Honeywell International", "sector": "Industrials", "exchange": "NASDAQ"},
    {"ticker": "IBM", "company_name": "International Business Machines", "sector": "Technology", "exchange": "NYSE"},
    {"ticker": "INTC", "company_name": "Intel Corp.", "sector": "Technology", "exchange": "NASDAQ"},
    {"ticker": "INTU", "company_name": "Intuit Inc.", "sector": "Technology", "exchange": "NASDAQ"},
    {"ticker": "JNJ", "company_name": "Johnson & Johnson", "sector": "Healthcare", "exchange": "NYSE"},
    {"ticker": "JPM", "company_name": "JPMorgan Chase & Co.", "sector": "Financials", "exchange": "NYSE"},
    {"ticker": "KHC", "company_name": "Kraft Heinz Co.", "sector": "Consumer Staples", "exchange": "NASDAQ"},
    {"ticker": "KO", "company_name": "Coca-Cola Co.", "sector": "Consumer Staples", "exchange": "NYSE"},
    {"ticker": "LIN", "company_name": "Linde plc", "sector": "Materials", "exchange": "NASDAQ"},
    {"ticker": "LLY", "company_name": "Eli Lilly and Co.", "sector": "Healthcare", "exchange": "NYSE"},
    {"ticker": "LMT", "company_name": "Lockheed Martin Corp.", "sector": "Industrials", "exchange": "NYSE"},
    {"ticker": "LOW", "company_name": "Lowe's Companies Inc.", "sector": "Consumer Discretionary", "exchange": "NYSE"},
    {"ticker": "MA", "company_name": "Mastercard Inc.", "sector": "Financials", "exchange": "NYSE"},
    {"ticker": "MCD", "company_name": "McDonald's Corp.", "sector": "Consumer Discretionary", "exchange": "NYSE"},
    {"ticker": "MDLZ", "company_name": "Mondelez International", "sector": "Consumer Staples", "exchange": "NASDAQ"},
    {"ticker": "MDT", "company_name": "Medtronic plc", "sector": "Healthcare", "exchange": "NYSE"},
    {"ticker": "MET", "company_name": "MetLife Inc.", "sector": "Financials", "exchange": "NYSE"},
    {"ticker": "META", "company_name": "Meta Platforms Inc.", "sector": "Communication Services", "exchange": "NASDAQ"},
    {"ticker": "MMM", "company_name": "3M Co.", "sector": "Industrials", "exchange": "NYSE"},
    {"ticker": "MO", "company_name": "Altria Group Inc.", "sector": "Consumer Staples", "exchange": "NYSE"},
    {"ticker": "MRK", "company_name": "Merck & Co. Inc.", "sector": "Healthcare", "exchange": "NYSE"},
    {"ticker": "MS", "company_name": "Morgan Stanley", "sector": "Financials", "exchange": "NYSE"},
    {"ticker": "MSFT", "company_name": "Microsoft Corp.", "sector": "Technology", "exchange": "NASDAQ"},
    {"ticker": "NEE", "company_name": "NextEra Energy Inc.", "sector": "Utilities", "exchange": "NYSE"},
    {"ticker": "NFLX", "company_name": "Netflix Inc.", "sector": "Communication Services", "exchange": "NASDAQ"},
    {"ticker": "NKE", "company_name": "Nike Inc.", "sector": "Consumer Discretionary", "exchange": "NYSE"},
    {"ticker": "NVDA", "company_name": "NVIDIA Corp.", "sector": "Technology", "exchange": "NASDAQ"},
    {"ticker": "ORCL", "company_name": "Oracle Corp.", "sector": "Technology", "exchange": "NYSE"},
    {"ticker": "PEP", "company_name": "PepsiCo Inc.", "sector": "Consumer Staples", "exchange": "NASDAQ"},
    {"ticker": "PFE", "company_name": "Pfizer Inc.", "sector": "Healthcare", "exchange": "NYSE"},
    {"ticker": "PG", "company_name": "Procter & Gamble Co.", "sector": "Consumer Staples", "exchange": "NYSE"},
    {"ticker": "PM", "company_name": "Philip Morris International", "sector": "Consumer Staples", "exchange": "NYSE"},
    {"ticker": "PYPL", "company_name": "PayPal Holdings Inc.", "sector": "Financials", "exchange": "NASDAQ"},
    {"ticker": "QCOM", "company_name": "Qualcomm Inc.", "sector": "Technology", "exchange": "NASDAQ"},
    {"ticker": "RTX", "company_name": "RTX Corp.", "sector": "Industrials", "exchange": "NYSE"},
    {"ticker": "SBUX", "company_name": "Starbucks Corp.", "sector": "Consumer Discretionary", "exchange": "NASDAQ"},
    {"ticker": "SCHW", "company_name": "Charles Schwab Corp.", "sector": "Financials", "exchange": "NYSE"},
    {"ticker": "SO", "company_name": "Southern Co.", "sector": "Utilities", "exchange": "NYSE"},
    {"ticker": "SPG", "company_name": "Simon Property Group", "sector": "Real Estate", "exchange": "NYSE"},
    {"ticker": "T", "company_name": "AT&T Inc.", "sector": "Communication Services", "exchange": "NYSE"},
    {"ticker": "TGT", "company_name": "Target Corp.", "sector": "Consumer Discretionary", "exchange": "NYSE"},
    {"ticker": "TMO", "company_name": "Thermo Fisher Scientific", "sector": "Healthcare", "exchange": "NYSE"},
    {"ticker": "TMUS", "company_name": "T-Mobile US Inc.", "sector": "Communication Services", "exchange": "NASDAQ"},
    {"ticker": "TSLA", "company_name": "Tesla Inc.", "sector": "Consumer Discretionary", "exchange": "NASDAQ"},
    {"ticker": "TXN", "company_name": "Texas Instruments Inc.", "sector": "Technology", "exchange": "NASDAQ"},
    {"ticker": "UNH", "company_name": "UnitedHealth Group Inc.", "sector": "Healthcare", "exchange": "NYSE"},
    {"ticker": "UNP", "company_name": "Union Pacific Corp.", "sector": "Industrials", "exchange": "NYSE"},
    {"ticker": "UPS", "company_name": "United Parcel Service", "sector": "Industrials", "exchange": "NYSE"},
    {"ticker": "USB", "company_name": "U.S. Bancorp", "sector": "Financials", "exchange": "NYSE"},
    {"ticker": "V", "company_name": "Visa Inc.", "sector": "Financials", "exchange": "NYSE"},
    {"ticker": "VZ", "company_name": "Verizon Communications", "sector": "Communication Services", "exchange": "NYSE"},
    {"ticker": "WBA", "company_name": "Walgreens Boots Alliance", "sector": "Healthcare", "exchange": "NASDAQ"},
    {"ticker": "WFC", "company_name": "Wells Fargo & Co.", "sector": "Financials", "exchange": "NYSE"},
    {"ticker": "WMT", "company_name": "Walmart Inc.", "sector": "Consumer Staples", "exchange": "NYSE"},
    {"ticker": "XOM", "company_name": "Exxon Mobil Corp.", "sector": "Energy", "exchange": "NYSE"},
    # Benchmark
    {"ticker": "SPY", "company_name": "SPDR S&P 500 ETF Trust", "sector": "ETF", "exchange": "NYSE ARCA"},
]


def main() -> None:
    """Initialize the universe with S&P 100 tickers."""
    logger.info("Starting universe initialization...")
    logger.info(f"Tickers to load: {len(SP100_TICKERS)}")

    with get_session() as session:
        manager = UniverseManager(session)
        count = manager.add_tickers_bulk(SP100_TICKERS, added_by="manual")
        logger.info(f"Processed {count} tickers")

    # Verify
    with get_session() as session:
        manager = UniverseManager(session)
        active = manager.count_active()
        logger.info(f"Active tickers in universe: {active}")

    logger.info("Universe initialization complete!")


if __name__ == "__main__":
    main()
