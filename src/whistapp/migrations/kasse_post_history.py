"""Post the kasse charges of nights that were final before nights were posted.

Before posting existed, a closed night with Klubkasse counted in the saldo straight away. Now a
night counts in Afventer until the kasse posts it, REOPEN_HOURS after it was last closed. The app
posts live nights by itself (their ended_at is when they were closed), but an imported night saved
before sessions.saved_at existed has no time it was saved, so the app never guesses when it became
final: it stays in Afventer. This migration posts those nights once, with the amounts the old saldo
already counted, so every member's I alt is the same before and after.

It posts only what it can classify: a club whose Kassens start is saved, an imported night whose
date can be read, and live nights the app would post anyway. Nights it cannot classify are listed
and left in Afventer for a person to look at. Running it again posts nothing twice
(kasse_night_postings.session_id is unique).
"""

from datetime import datetime

from ..kasse import aftener, ledger

NAME = "kasse-post-history"
DESCRIPTION = ("Post the kasse charges of nights that were already final, so they move from Afventer to "
               "Bogført; member totals stay the same. Imported nights with an unreadable date are listed, not posted.")


def _readable_date(night):
    try:
        datetime.strptime(aftener.night_date(night), "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _plan(db):
    """(club id, night, source) to post, and Danish lines about nights left for review."""
    todo, review = [], []
    for c in db.execute("SELECT id,name,kasse_start,kasse_started_at FROM clubs ORDER BY id").fetchall():
        nights = [n for n in ledger._night_rows(db, c["id"]) if not aftener.before_start(n, c["kasse_start"])]
        if not c["kasse_started_at"]:
            if nights:
                review.append(f"{c['name']}: kassens start er ikke sat; {len(nights)} aftener afventer, til den er sat")
            continue
        due = {n["id"] for n in ledger.due_nights(db, c["id"])}
        for n in nights:
            if n["id"] in due:
                todo.append((c["id"], n, "spilaften"))
            elif aftener.closed_at(n) is not None:
                continue  # still inside its window; the app posts it when it is due
            elif n["origin"] == "imported" and n["saved_at"] is None and _readable_date(n):
                todo.append((c["id"], n, "historisk"))
            else:
                review.append(f"{c['name']}: aften #{n['id']} {n['title']!r} ({n['origin']}, dato {n['created_at']!r}, "
                              f"afsluttet {n['ended_at']!r}) kan ikke klassificeres sikkert og afventer fortsat")
    return todo, review


def needed(db):
    return bool(_plan(db)[0])


def blockers(db):
    return []


def run(db):
    todo, review = _plan(db)
    posted = sum(ledger.post_night(db, club_id, night, source) for club_id, night, source in todo)
    lines = [f"posted {posted} nights"]
    if review:
        lines.append("left in Afventer for review:\n    " + "\n    ".join(review))
    return "\n".join(lines)
