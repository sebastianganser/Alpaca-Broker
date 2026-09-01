"""One-time backfill: enrich existing politician_trades with party affiliation.

Run via: uv run python scripts/backfill_party_mapping.py
Sprint 9.5c C3.
"""
from trading_signals.data.congress_members import CONGRESS_MEMBERS, lookup_member
from trading_signals.db.session import get_session
from trading_signals.db.models.politicians import PoliticianTrade
from sqlalchemy import select, update

def backfill():
    with get_session() as session:
        # Get all distinct politician names with NULL party
        names = session.execute(
            select(PoliticianTrade.politician_name)
            .where(PoliticianTrade.party.is_(None))
            .distinct()
        ).scalars().all()
        
        updated = 0
        not_found = []
        for name in names:
            info = lookup_member(name)
            if info.get("party"):
                result = session.execute(
                    update(PoliticianTrade)
                    .where(PoliticianTrade.politician_name == name)
                    .where(PoliticianTrade.party.is_(None))
                    .values(party=info["party"], state=info.get("state"))
                )
                updated += result.rowcount
                print(f"  Updated {result.rowcount} rows for {name} -> {info['party']}")
            else:
                not_found.append(name)
        
        session.commit()
        
        print(f"\nTotal updated: {updated} rows")
        if not_found:
            print(f"Not found ({len(not_found)}): {not_found}")
            print("Add these to congress_members.py manually.")

if __name__ == "__main__":
    backfill()
