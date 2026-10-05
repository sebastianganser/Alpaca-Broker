"""Debug script: Check NFLX holdings in ARKW across dates."""
import requests

for d in ["2026-04-15", "2026-04-16", "2026-04-17"]:
    data = requests.get(
        f"https://arkfunds.io/api/v2/etf/holdings?symbol=ARKW&date={d}"
    ).json()
    holdings = data.get("holdings", [])
    for h in holdings:
        if h.get("ticker") == "NFLX":
            print(
                f"Date {d}: shares={h['shares']}, "
                f"weight={h['weight']}, "
                f"api_date={h['date']}"
            )
            break
    else:
        print(f"Date {d}: NFLX not found in ARKW")
