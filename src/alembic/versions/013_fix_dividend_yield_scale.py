"""Fix dividend_yield scale: divide existing values by 100.

yfinance returns dividendYield in percent form (0.4 = 0.4%) while all
other ratio fields are in decimal form (0.451 = 45.1%). The collector
code now normalizes this on import, but existing data in the DB still
has the old (wrong) scale.

This migration divides all dividend_yield values > 0.25 (i.e., > 25%)
by 100 to correct them. Values <= 0.25 are already plausible and left
untouched (they may have been inserted after the code fix).

Revision ID: 013
Revises: 012
"""

from alembic import op

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Fix dividend_yield values that are clearly in percent form (> 0.25 = 25%).
    # Real dividend yields above 25% are virtually non-existent for US equities.
    # This safely corrects values like 0.92 (displayed as 92%) to 0.0092 (0.92%).
    op.execute(
        """
        UPDATE signals.fundamentals_snapshot
        SET dividend_yield = dividend_yield / 100
        WHERE dividend_yield > 0.25
        """
    )


def downgrade() -> None:
    # Reverse: multiply values < 0.0025 back by 100
    op.execute(
        """
        UPDATE signals.fundamentals_snapshot
        SET dividend_yield = dividend_yield * 100
        WHERE dividend_yield IS NOT NULL AND dividend_yield < 0.0025
        """
    )
