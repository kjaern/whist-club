"""Club-only statistics derived from recorded hands, never from current bid rules.

The statistics themselves live in `stats/`; this keeps the original per-year table API.
"""

from .stats.base import pct_text, points_text, ratio
from .stats.data import Scope, load
from .stats.overview import member_rows, sorted_rows


def club_statistics(db, roster, session_id=None, origin="all"):
    """Sections "Alle år" and each year (newest first), each with per-member rows."""
    if origin not in ("all", "live", "imported"):
        raise ValueError("Invalid origin")
    scope = Scope("night", session_id=session_id, origin=origin) if session_id is not None else Scope(origin=origin)
    data = load(db, scope, roster)
    periods = [("Alle år", data)] + [(year, data.for_year(year)) for year in data.years()]
    output = []
    for title, period in periods:
        rows = member_rows(period)
        if not rows:
            continue
        output.append(dict(
            title=title,
            night_count=len(set().union(*(r["nights"] for r in rows.values()))),
            players=[
                dict(
                    r, nights=len(r["nights"]), points=points_text(r["points"]),
                    amount=points_text(r["amount"]),
                    self_pct=pct_text(ratio(r["self_partner"], r["eligible"])) if r["eligible"] else "—",
                    bid_balance=r["bid_won"] - r["bid_lost"],
                )
                for r in sorted_rows(rows)
            ],
        ))
    return output
