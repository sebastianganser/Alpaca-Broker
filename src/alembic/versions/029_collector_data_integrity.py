"""Collector data-integrity fixes (13F, Form 4, insider clusters, alt-data keys).

Revision ID: 029
Revises: 028
Create Date: 2026-10-05

Contents
--------
* form13f_holdings (C1/C2):
    - new columns accession_number, form_type, amendment_type
    - C1 data fix: since Jan 2023 SEC reports market value in whole dollars;
      the collector multiplied every value by 1000 → divide rows filed on/after
      2023-01-03 (or, without filing_date, report_period >= 2022-12-31) by
      1000. Guarded by ``accession_number IS NULL`` (set in the same
      statement) → idempotent, rows written by the fixed collector are never
      touched.
    - put_call NOT NULL DEFAULT 'SH'; unique key
      (filer_cik, report_period, cusip, put_call) replaces
      (filer_cik, report_period, cusip). The new key is a superset of the old
      one, so no de-duplication is needed.
* insider_trades (H1/M1):
    - new columns accession_number, form_type, row_index,
      acceptance_datetime (TIMESTAMPTZ), insider_cik, owner_count
    - exact duplicates (NULLs treated equal) removed, keeping min(id)
    - accession_number back-filled from form4_url for legacy rows
      (row_index stays NULL → legacy rows never collide with the new index)
    - old unique constraint uq_insider_trade_dedup dropped; new partial
      unique index (accession_number, is_derivative, row_index)
      WHERE accession_number IS NOT NULL
* insider_clusters: known_date DATE NULL (filled by the ML/derived layer).
* Market-data collectors (M4/M6):
    - politician_trades.owner normalised ('Self'), NOT NULL DEFAULT 'Self',
      added to uq_politician_trade_dedup (key only widens → no dedupe)
    - earnings_calendar.first_seen / last_seen (back-filled from fetched_at)
    - fundamentals_snapshot.most_recent_quarter
"""

import sqlalchemy as sa

from alembic import op

revision = "029"
down_revision = "028"

SCHEMA = "signals"


# ── 13F + Form 4 + insider clusters ──────────────────────────────────────
def _upgrade_sec() -> None:
    t13, ti = "form13f_holdings", "insider_trades"
    op.add_column(t13, sa.Column("accession_number", sa.String(25)), schema=SCHEMA)
    op.add_column(t13, sa.Column("form_type", sa.String(12)), schema=SCHEMA)
    op.add_column(t13, sa.Column("amendment_type", sa.String(20)), schema=SCHEMA)
    # C1 + accession backfill in ONE statement guarded by accession IS NULL
    # -> idempotent
    op.execute(r"""
        UPDATE signals.form13f_holdings
        SET market_value = CASE WHEN filing_date >= DATE '2023-01-03'
                 OR (filing_date IS NULL AND report_period >= DATE '2022-12-31')
               THEN market_value / 1000 ELSE market_value END,
            accession_number = COALESCE(regexp_replace(
               substring(source_url FROM '/data/[0-9]+/([0-9]{18})/'),
               '^([0-9]{10})([0-9]{2})([0-9]{6})$', '\1-\2-\3'), 'legacy')
        WHERE accession_number IS NULL""")
    op.execute(
        "UPDATE signals.form13f_holdings "
        "SET put_call = COALESCE(NULLIF(upper(trim(put_call)), ''), 'SH')"
    )
    op.alter_column(
        t13, "put_call", existing_type=sa.String(10), nullable=False,
        server_default=sa.text("'SH'"), schema=SCHEMA,
    )
    op.drop_constraint("uq_13f_holding_dedup", t13, type_="unique", schema=SCHEMA)
    op.create_unique_constraint(
        "uq_13f_holding_key", t13,
        ["filer_cik", "report_period", "cusip", "put_call"], schema=SCHEMA,
    )

    for name, typ in [
        ("accession_number", sa.String(25)),
        ("form_type", sa.String(10)),
        ("row_index", sa.Integer()),
        ("acceptance_datetime", sa.DateTime(timezone=True)),
        ("insider_cik", sa.String(20)),
        ("owner_count", sa.SmallInteger()),
    ]:
        op.add_column(ti, sa.Column(name, typ, nullable=True), schema=SCHEMA)
    # Exact duplicates (PARTITION BY treats NULLs as equal) → keep min(id)
    op.execute("""
        DELETE FROM signals.insider_trades t USING (
          SELECT id, row_number() OVER (
                   PARTITION BY cik, insider_name, transaction_date,
                                transaction_type, shares, price_per_share,
                                is_derivative, form4_url
                   ORDER BY id) AS rn
          FROM signals.insider_trades) d
        WHERE t.id = d.id AND d.rn > 1""")
    # legacy accession (row_index stays NULL) -> collector skips re-downloading
    op.execute(r"""
        UPDATE signals.insider_trades
        SET accession_number = regexp_replace(
              substring(form4_url FROM '/data/[0-9]+/([0-9]{18})/'),
              '^([0-9]{10})([0-9]{2})([0-9]{6})$', '\1-\2-\3')
        WHERE accession_number IS NULL
          AND form4_url ~ '/data/[0-9]+/[0-9]{18}/'""")
    op.drop_constraint("uq_insider_trade_dedup", ti, type_="unique", schema=SCHEMA)
    op.create_index(
        "uq_insider_trade_filing_row", ti,
        ["accession_number", "is_derivative", "row_index"], unique=True,
        schema=SCHEMA, postgresql_where=sa.text("accession_number IS NOT NULL"),
    )
    op.add_column(
        "insider_clusters", sa.Column("known_date", sa.Date(), nullable=True),
        schema=SCHEMA,
    )


def _downgrade_sec() -> None:
    t13, ti = "form13f_holdings", "insider_trades"
    op.drop_column("insider_clusters", "known_date", schema=SCHEMA)
    op.drop_index("uq_insider_trade_filing_row", table_name=ti, schema=SCHEMA)
    op.execute("""
        DELETE FROM signals.insider_trades t USING (
          SELECT id, row_number() OVER (
                   PARTITION BY cik, insider_name, transaction_date,
                                transaction_type, shares, price_per_share
                   ORDER BY id) AS rn
          FROM signals.insider_trades
          WHERE cik IS NOT NULL AND insider_name IS NOT NULL
            AND transaction_date IS NOT NULL AND transaction_type IS NOT NULL
            AND shares IS NOT NULL AND price_per_share IS NOT NULL) d
        WHERE t.id = d.id AND d.rn > 1""")
    op.create_unique_constraint(
        "uq_insider_trade_dedup", ti,
        ["cik", "insider_name", "transaction_date", "transaction_type", "shares",
         "price_per_share"],
        schema=SCHEMA,
    )
    for c in ("owner_count", "insider_cik", "acceptance_datetime", "row_index",
              "form_type", "accession_number"):
        op.drop_column(ti, c, schema=SCHEMA)
    op.drop_constraint("uq_13f_holding_key", t13, type_="unique", schema=SCHEMA)
    op.execute("""
        DELETE FROM signals.form13f_holdings t USING (
          SELECT id, row_number() OVER (
                   PARTITION BY filer_cik, report_period, cusip
                   ORDER BY (put_call = 'SH') DESC, id) AS rn
          FROM signals.form13f_holdings
          WHERE filer_cik IS NOT NULL AND report_period IS NOT NULL
            AND cusip IS NOT NULL) d
        WHERE t.id = d.id AND d.rn > 1""")
    op.alter_column(
        t13, "put_call", existing_type=sa.String(10), nullable=True,
        server_default=None, schema=SCHEMA,
    )
    op.execute(
        "UPDATE signals.form13f_holdings SET put_call = NULL WHERE put_call = 'SH'"
    )
    op.create_unique_constraint(
        "uq_13f_holding_dedup", t13, ["filer_cik", "report_period", "cusip"],
        schema=SCHEMA,
    )
    for c in ("amendment_type", "form_type", "accession_number"):
        op.drop_column(t13, c, schema=SCHEMA)
    # market_value stays in whole dollars (data fix intentionally not reverted)


# ── Market-data collectors (earnings, fundamentals, politicians) ─────────
_POL_OLD_COLS = [
    "politician_name",
    "ticker",
    "transaction_date",
    "transaction_type",
    "amount_range",
]


def _upgrade_market_data() -> None:
    # politician_trades.owner (M6): normalise, NOT NULL DEFAULT 'Self',
    # part of the dedup key. Key only gets wider -> no dedupe needed.
    op.execute(
        "UPDATE signals.politician_trades SET owner = 'Self' "
        "WHERE owner IS NULL OR btrim(owner) IN ('', '--') "
        "OR upper(btrim(owner)) = 'SELF'"
    )
    op.alter_column(
        "politician_trades", "owner", existing_type=sa.String(50),
        nullable=False, server_default=sa.text("'Self'"), schema=SCHEMA,
    )
    op.drop_constraint(
        "uq_politician_trade_dedup", "politician_trades", type_="unique",
        schema=SCHEMA,
    )
    op.create_unique_constraint(
        "uq_politician_trade_dedup", "politician_trades",
        [*_POL_OLD_COLS, "owner"], schema=SCHEMA,
    )

    # earnings_calendar.first_seen / last_seen (M4)
    for col in ("first_seen", "last_seen"):
        op.add_column(
            "earnings_calendar",
            sa.Column(col, sa.DateTime(), server_default=sa.func.now()),
            schema=SCHEMA,
        )
    # Best available history: the old fetched_at (time of last upsert).
    op.execute(
        "UPDATE signals.earnings_calendar "
        "SET first_seen = COALESCE(fetched_at, first_seen), "
        "    last_seen  = COALESCE(fetched_at, last_seen)"
    )

    # fundamentals_snapshot.most_recent_quarter (M4)
    op.add_column(
        "fundamentals_snapshot",
        sa.Column("most_recent_quarter", sa.Date(), nullable=True),
        schema=SCHEMA,
    )
    # NOTE: historic options_iv_snapshot / estimates_snapshot labels (Berlin
    # fetch date = next calendar day) are intentionally NOT relabelled here:
    # relabelling needs the NYSE calendar and could collide on the PK. New
    # rows use market_calendar.last_completed_session().


def _downgrade_market_data() -> None:
    op.drop_column("fundamentals_snapshot", "most_recent_quarter", schema=SCHEMA)
    op.drop_column("earnings_calendar", "last_seen", schema=SCHEMA)
    op.drop_column("earnings_calendar", "first_seen", schema=SCHEMA)

    # Rows differing only by owner would violate the old 5-col key.
    op.execute(
        "DELETE FROM signals.politician_trades a "
        "USING signals.politician_trades b "
        "WHERE a.id > b.id "
        "AND a.politician_name = b.politician_name "
        "AND a.ticker IS NOT DISTINCT FROM b.ticker "
        "AND a.transaction_date = b.transaction_date "
        "AND a.transaction_type IS NOT DISTINCT FROM b.transaction_type "
        "AND a.amount_range IS NOT DISTINCT FROM b.amount_range"
    )
    op.drop_constraint(
        "uq_politician_trade_dedup", "politician_trades", type_="unique",
        schema=SCHEMA,
    )
    op.create_unique_constraint(
        "uq_politician_trade_dedup", "politician_trades", _POL_OLD_COLS,
        schema=SCHEMA,
    )
    op.alter_column(
        "politician_trades", "owner", existing_type=sa.String(50),
        nullable=True, server_default=None, schema=SCHEMA,
    )


def upgrade() -> None:
    _upgrade_sec()
    _upgrade_market_data()


def downgrade() -> None:
    _downgrade_market_data()
    _downgrade_sec()
