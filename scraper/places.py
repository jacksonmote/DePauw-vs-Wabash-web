"""
Display style for place names: the state at the end of "City, ST" is written the AP way ("Ind.", "Ohio",
"Ill.") wherever a location is shown on the page.

The two schools write states differently (DePauw "Greencastle, Ind.", Wabash "Greencastle, IN"), and the
data files keep each source's words exactly as read. This is display only: it never touches the data,
games.csv or the links. Anything that does not end in a recognised state is returned untouched, so a
venue string like "Crawfordsville High School" or "College of Wooster; Wooster, Ohio" is never guessed at.
"""

from __future__ import annotations

# The two schools' towns, in the AP style the page shows. A home or an away game that names no site is shown
# there (render.py; neutral sites are never guessed), and a found ruling's place is checked against them (merge.py).
HOME_SITE = "Greencastle, Ind."         # DePauw
AWAY_SITE = "Crawfordsville, Ind."      # Wabash

# postal code -> (AP abbreviation, full name)
STATES = {
    "AL": ("Ala.", "Alabama"), "AK": ("Alaska", "Alaska"), "AZ": ("Ariz.", "Arizona"),
    "AR": ("Ark.", "Arkansas"), "CA": ("Calif.", "California"), "CO": ("Colo.", "Colorado"),
    "CT": ("Conn.", "Connecticut"), "DE": ("Del.", "Delaware"), "DC": ("D.C.", "District of Columbia"),
    "FL": ("Fla.", "Florida"), "GA": ("Ga.", "Georgia"), "HI": ("Hawaii", "Hawaii"),
    "ID": ("Idaho", "Idaho"), "IL": ("Ill.", "Illinois"), "IN": ("Ind.", "Indiana"),
    "IA": ("Iowa", "Iowa"), "KS": ("Kan.", "Kansas"), "KY": ("Ky.", "Kentucky"),
    "LA": ("La.", "Louisiana"), "ME": ("Maine", "Maine"), "MD": ("Md.", "Maryland"),
    "MA": ("Mass.", "Massachusetts"), "MI": ("Mich.", "Michigan"), "MN": ("Minn.", "Minnesota"),
    "MS": ("Miss.", "Mississippi"), "MO": ("Mo.", "Missouri"), "MT": ("Mont.", "Montana"),
    "NE": ("Neb.", "Nebraska"), "NV": ("Nev.", "Nevada"), "NH": ("N.H.", "New Hampshire"),
    "NJ": ("N.J.", "New Jersey"), "NM": ("N.M.", "New Mexico"), "NY": ("N.Y.", "New York"),
    "NC": ("N.C.", "North Carolina"), "ND": ("N.D.", "North Dakota"), "OH": ("Ohio", "Ohio"),
    "OK": ("Okla.", "Oklahoma"), "OR": ("Ore.", "Oregon"), "PA": ("Pa.", "Pennsylvania"),
    "RI": ("R.I.", "Rhode Island"), "SC": ("S.C.", "South Carolina"), "SD": ("S.D.", "South Dakota"),
    "TN": ("Tenn.", "Tennessee"), "TX": ("Texas", "Texas"), "UT": ("Utah", "Utah"),
    "VT": ("Vt.", "Vermont"), "VA": ("Va.", "Virginia"), "WA": ("Wash.", "Washington"),
    "WV": ("W.Va.", "West Virginia"), "WI": ("Wis.", "Wisconsin"), "WY": ("Wyo.", "Wyoming"),
}

_AP_BY_POSTAL = {code: ap for code, (ap, _) in STATES.items()}
_AP_BY_NAME = {name.lower(): ap for ap, name in STATES.values()}


def ap_state(token: str) -> str | None:
    """The AP form for a postal code ("IN") or a full state name ("Indiana"), or None when it is neither."""
    token = token.strip()
    return _AP_BY_POSTAL.get(token) or _AP_BY_NAME.get(token.lower())


def ap_place(text):
    """'Goodrich Ballpark, Crawfordsville, IN' -> 'Goodrich Ballpark, Crawfordsville, Ind.'. The state is
    recognised only as the last comma-separated part; anything else comes back unchanged."""
    if not isinstance(text, str) or "," not in text:
        return text
    head, _, tail = text.rpartition(",")
    state = ap_state(tail)
    return f"{head}, {state}" if state else text
