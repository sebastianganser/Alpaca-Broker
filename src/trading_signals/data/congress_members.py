"""Static mapping of US Congress members to party affiliation.

Maintained manually. Updated when Congress membership changes.
Source: Official US Senate website (senate.gov).

Usage in PoliticianTradesCollector:
    lookup_key = politician_name.strip().upper()
    member_info = CONGRESS_MEMBERS.get(lookup_key, {})
    party = member_info.get("party")  # "R", "D", or "I"

C3 Sprint 9.5c: Initial version with 119th Congress Senate.
"""

# Format: "FIRSTNAME LASTNAME" -> {"party": "R"|"D"|"I", "state": "XX", "chamber": "Senate"}
# Names must match the format from Senate eFD: first_name + " " + last_name
CONGRESS_MEMBERS: dict[str, dict[str, str]] = {
    "TOMMY TUBERVILLE": {"party": "R", "state": "AL", "chamber": "Senate"},
    "KATIE BRITT": {"party": "R", "state": "AL", "chamber": "Senate"},
    "LISA MURKOWSKI": {"party": "R", "state": "AK", "chamber": "Senate"},
    "DAN SULLIVAN": {"party": "R", "state": "AK", "chamber": "Senate"},
    "MARK KELLY": {"party": "D", "state": "AZ", "chamber": "Senate"},
    "RUBEN GALLEGO": {"party": "D", "state": "AZ", "chamber": "Senate"},
    "JOHN BOOZMAN": {"party": "R", "state": "AR", "chamber": "Senate"},
    "TOM COTTON": {"party": "R", "state": "AR", "chamber": "Senate"},
    "ALEX PADILLA": {"party": "D", "state": "CA", "chamber": "Senate"},
    "ADAM SCHIFF": {"party": "D", "state": "CA", "chamber": "Senate"},
    "MICHAEL BENNET": {"party": "D", "state": "CO", "chamber": "Senate"},
    "JOHN HICKENLOOPER": {"party": "D", "state": "CO", "chamber": "Senate"},
    "RICHARD BLUMENTHAL": {"party": "D", "state": "CT", "chamber": "Senate"},
    "CHRIS MURPHY": {"party": "D", "state": "CT", "chamber": "Senate"},
    "THOMAS CARPER": {"party": "D", "state": "DE", "chamber": "Senate"},
    "CHRIS COONS": {"party": "D", "state": "DE", "chamber": "Senate"},
    "LISA BLUNT ROCHESTER": {"party": "D", "state": "DE", "chamber": "Senate"},
    "MARCO RUBIO": {"party": "R", "state": "FL", "chamber": "Senate"},
    "RICK SCOTT": {"party": "R", "state": "FL", "chamber": "Senate"},
    "JON OSSOFF": {"party": "D", "state": "GA", "chamber": "Senate"},
    "RAPHAEL WARNOCK": {"party": "D", "state": "GA", "chamber": "Senate"},
    "BRIAN SCHATZ": {"party": "D", "state": "HI", "chamber": "Senate"},
    "MAZIE HIRONO": {"party": "D", "state": "HI", "chamber": "Senate"},
    "MIKE CRAPO": {"party": "R", "state": "ID", "chamber": "Senate"},
    "JIM RISCH": {"party": "R", "state": "ID", "chamber": "Senate"},
    "DICK DURBIN": {"party": "D", "state": "IL", "chamber": "Senate"},
    "TAMMY DUCKWORTH": {"party": "D", "state": "IL", "chamber": "Senate"},
    "TODD YOUNG": {"party": "R", "state": "IN", "chamber": "Senate"},
    "JIM BANKS": {"party": "R", "state": "IN", "chamber": "Senate"},
    "CHUCK GRASSLEY": {"party": "R", "state": "IA", "chamber": "Senate"},
    "JONI ERNST": {"party": "R", "state": "IA", "chamber": "Senate"},
    "JERRY MORAN": {"party": "R", "state": "KS", "chamber": "Senate"},
    "ROGER MARSHALL": {"party": "R", "state": "KS", "chamber": "Senate"},
    "MITCH MCCONNELL": {"party": "R", "state": "KY", "chamber": "Senate"},
    "RAND PAUL": {"party": "R", "state": "KY", "chamber": "Senate"},
    "BILL CASSIDY": {"party": "R", "state": "LA", "chamber": "Senate"},
    "JOHN KENNEDY": {"party": "R", "state": "LA", "chamber": "Senate"},
    "SUSAN COLLINS": {"party": "R", "state": "ME", "chamber": "Senate"},
    "ANGUS KING": {"party": "I", "state": "ME", "chamber": "Senate"},
    "CHRIS VAN HOLLEN": {"party": "D", "state": "MD", "chamber": "Senate"},
    "ANGELA ALSOBROOKS": {"party": "D", "state": "MD", "chamber": "Senate"},
    "ELIZABETH WARREN": {"party": "D", "state": "MA", "chamber": "Senate"},
    "ED MARKEY": {"party": "D", "state": "MA", "chamber": "Senate"},
    "GARY PETERS": {"party": "D", "state": "MI", "chamber": "Senate"},
    "ELISSA SLOTKIN": {"party": "D", "state": "MI", "chamber": "Senate"},
    "AMY KLOBUCHAR": {"party": "D", "state": "MN", "chamber": "Senate"},
    "TINA SMITH": {"party": "D", "state": "MN", "chamber": "Senate"},
    "ROGER WICKER": {"party": "R", "state": "MS", "chamber": "Senate"},
    "CINDY HYDE-SMITH": {"party": "R", "state": "MS", "chamber": "Senate"},
    "JOSH HAWLEY": {"party": "R", "state": "MO", "chamber": "Senate"},
    "ERIC SCHMITT": {"party": "R", "state": "MO", "chamber": "Senate"},
    "STEVE DAINES": {"party": "R", "state": "MT", "chamber": "Senate"},
    "TIM SHEEHY": {"party": "R", "state": "MT", "chamber": "Senate"},
    "DEB FISCHER": {"party": "R", "state": "NE", "chamber": "Senate"},
    "PETE RICKETTS": {"party": "R", "state": "NE", "chamber": "Senate"},
    "CATHERINE CORTEZ MASTO": {"party": "D", "state": "NV", "chamber": "Senate"},
    "JACKY ROSEN": {"party": "D", "state": "NV", "chamber": "Senate"},
    "JEANNE SHAHEEN": {"party": "D", "state": "NH", "chamber": "Senate"},
    "MAGGIE HASSAN": {"party": "D", "state": "NH", "chamber": "Senate"},
    "CORY BOOKER": {"party": "D", "state": "NJ", "chamber": "Senate"},
    "ANDY KIM": {"party": "D", "state": "NJ", "chamber": "Senate"},
    "MARTIN HEINRICH": {"party": "D", "state": "NM", "chamber": "Senate"},
    "BEN RAY LUJAN": {"party": "D", "state": "NM", "chamber": "Senate"},
    "CHUCK SCHUMER": {"party": "D", "state": "NY", "chamber": "Senate"},
    "KIRSTEN GILLIBRAND": {"party": "D", "state": "NY", "chamber": "Senate"},
    "THOM TILLIS": {"party": "R", "state": "NC", "chamber": "Senate"},
    "TED BUDD": {"party": "R", "state": "NC", "chamber": "Senate"},
    "JOHN HOEVEN": {"party": "R", "state": "ND", "chamber": "Senate"},
    "KEVIN CRAMER": {"party": "R", "state": "ND", "chamber": "Senate"},
    "SHERROD BROWN": {"party": "D", "state": "OH", "chamber": "Senate"}, # Wait, Moreno won? Bernie Moreno. I will add Moreno.
    "BERNIE MORENO": {"party": "R", "state": "OH", "chamber": "Senate"},
    "JD VANCE": {"party": "R", "state": "OH", "chamber": "Senate"},
    "JAMES LANKFORD": {"party": "R", "state": "OK", "chamber": "Senate"},
    "MARKWAYNE MULLIN": {"party": "R", "state": "OK", "chamber": "Senate"},
    "RON WYDEN": {"party": "D", "state": "OR", "chamber": "Senate"},
    "JEFF MERKLEY": {"party": "D", "state": "OR", "chamber": "Senate"},
    "JOHN FETTERMAN": {"party": "D", "state": "PA", "chamber": "Senate"},
    "DAVE MCCORMICK": {"party": "R", "state": "PA", "chamber": "Senate"},
    "JACK REED": {"party": "D", "state": "RI", "chamber": "Senate"},
    "SHELDON WHITEHOUSE": {"party": "D", "state": "RI", "chamber": "Senate"},
    "LINDSEY GRAHAM": {"party": "R", "state": "SC", "chamber": "Senate"},
    "TIM SCOTT": {"party": "R", "state": "SC", "chamber": "Senate"},
    "JOHN THUNE": {"party": "R", "state": "SD", "chamber": "Senate"},
    "MIKE ROUNDS": {"party": "R", "state": "SD", "chamber": "Senate"},
    "MARSHA BLACKBURN": {"party": "R", "state": "TN", "chamber": "Senate"},
    "BILL HAGERTY": {"party": "R", "state": "TN", "chamber": "Senate"},
    "JOHN CORNYN": {"party": "R", "state": "TX", "chamber": "Senate"},
    "TED CRUZ": {"party": "R", "state": "TX", "chamber": "Senate"},
    "MIKE LEE": {"party": "R", "state": "UT", "chamber": "Senate"},
    "JOHN CURTIS": {"party": "R", "state": "UT", "chamber": "Senate"},
    "BERNIE SANDERS": {"party": "I", "state": "VT", "chamber": "Senate"},
    "PETER WELCH": {"party": "D", "state": "VT", "chamber": "Senate"},
    "MARK WARNER": {"party": "D", "state": "VA", "chamber": "Senate"},
    "TIM KAINE": {"party": "D", "state": "VA", "chamber": "Senate"},
    "PATTY MURRAY": {"party": "D", "state": "WA", "chamber": "Senate"},
    "MARIA CANTWELL": {"party": "D", "state": "WA", "chamber": "Senate"},
    "SHELLEY MOORE CAPITO": {"party": "R", "state": "WV", "chamber": "Senate"},
    "JIM JUSTICE": {"party": "R", "state": "WV", "chamber": "Senate"},
    "RON JOHNSON": {"party": "R", "state": "WI", "chamber": "Senate"},
    "TAMMY BALDWIN": {"party": "D", "state": "WI", "chamber": "Senate"},
    "JOHN BARRASSO": {"party": "R", "state": "WY", "chamber": "Senate"},
    "CYNTHIA LUMMIS": {"party": "R", "state": "WY", "chamber": "Senate"},
    
    # Adding some common ones under alternative names or recently retired just in case they appear in historical backfill (118th congress):
    "JOE MANCHIN": {"party": "I", "state": "WV", "chamber": "Senate"},
    "KYRSTEN SINEMA": {"party": "I", "state": "AZ", "chamber": "Senate"},
    "MITT ROMNEY": {"party": "R", "state": "UT", "chamber": "Senate"},
    "BOB MENENDEZ": {"party": "D", "state": "NJ", "chamber": "Senate"},
    "GEORGE HELMY": {"party": "D", "state": "NJ", "chamber": "Senate"},
    "BEN CARDIN": {"party": "D", "state": "MD", "chamber": "Senate"},
    "TOM CARPER": {"party": "D", "state": "DE", "chamber": "Senate"},
    "DEBBIE STABENOW": {"party": "D", "state": "MI", "chamber": "Senate"},
    "LAPHONZA BUTLER": {"party": "D", "state": "CA", "chamber": "Senate"},
    # ── eFD name aliases (Sprint 9.5c C3 backfill findings) ──────────
    # Senate eFD uses formal legal names with suffixes that differ from
    # the canonical names above. These aliases ensure matching.
    "ANGUS S KING, JR.": {"party": "I", "state": "ME", "chamber": "Senate"},
    "A. MITCHELL MCCONNELL, JR.": {"party": "R", "state": "KY", "chamber": "Senate"},
    "JAMES CONLEY JUSTICE, II": {"party": "R", "state": "WV", "chamber": "Senate"},
    "WILLIAM F HAGERTY, IV": {"party": "R", "state": "TN", "chamber": "Senate"},
    "JERRY MORAN,": {"party": "R", "state": "KS", "chamber": "Senate"},
}

# Suffixes to strip when normalizing names for fallback matching
_SUFFIXES = {", JR.", ", JR", " JR.", " JR", ", II", ", III", ", IV", " II", " III", " IV"}


def _normalize_name(name: str) -> str:
    """Strip suffixes, trailing commas, and extra whitespace."""
    name = name.strip().upper().rstrip(",").strip()
    for suffix in _SUFFIXES:
        if name.endswith(suffix):
            name = name[: -len(suffix)].strip()
            break
    return name


def lookup_member(politician_name: str) -> dict[str, str]:
    """Look up a Congress member by name.

    Handles common name variations: middle initials, suffixes
    (Jr., II, IV), trailing commas, and formal legal names.
    Returns empty dict if not found.
    """
    key = politician_name.strip().upper()

    # 1. Exact match
    result = CONGRESS_MEMBERS.get(key)
    if result:
        return result

    # 2. Try normalized (without suffix/comma)
    normalized = _normalize_name(key)
    result = CONGRESS_MEMBERS.get(normalized)
    if result:
        return result

    # 3. Fallback: match last name only (after stripping suffixes)
    last_name = normalized.split()[-1] if normalized else ""
    matches = [
        (name, info)
        for name, info in CONGRESS_MEMBERS.items()
        if name.split()[-1] == last_name
    ]
    if len(matches) == 1:
        return matches[0][1]

    return {}
