"""Faste pladser: how often the table sat the same way round."""

from collections import defaultdict

from .base import Cell, Column, Statistic, Table
from .seating import GUEST, danish_date


def arrangement(data, night, played):
    """The night's players round the table, starting from the member first by name, so the
    same table counts once whoever sat 'first'. None with fewer than two members.
    `played` is everyone who played a hand that night."""
    played = played | set(night.final_scores)
    order = [pid for pid in data.seats.get(night.id, ()) if pid in played]
    names = [data.name_of(data.member(pid)) if data.member(pid) is not None else GUEST for pid in order]
    members = [i for i, pid in enumerate(order) if data.member(pid) is not None]
    if len(members) < 2:
        return None
    start = min(members, key=lambda i: (names[i].casefold(), i))
    return tuple(names[start:] + names[:start])


class Seatings(Statistic):
    key = "faste-pladser"
    title = "Faste pladser"
    topic = "bordet"
    scopes = ("all", "year", "period")
    description = ("Hvordan bordet sad, rundt om bordet mod venstre, og hvor ofte. Det er det samme bord, "
                   "uanset hvem der sad 'først'. Kun aftenens sidste bordplan kendes, og importerede aftener "
                   "bruger rækkefølgen fra arket.")

    def compute(self, data):
        stats = defaultdict(lambda: dict(nights=0, last=None, streak=0))
        played = defaultdict(set)
        for h in data.hands:
            played[h.session_id] |= h.participants
        previous, run = None, 0
        for night in data.nights.values():
            table = arrangement(data, night, played[night.id])
            if table is None:
                previous = None  # a night without a table breaks the run
                continue
            run = run + 1 if table == previous else 1
            previous = table
            s = stats[table]
            s["nights"] += 1
            s["last"] = night.date
            s["streak"] = max(s["streak"], run)

        rows = [dict(table=Cell(" → ".join(table), " ".join(table).casefold()), nights=s["nights"],
                     last=Cell(danish_date(s["last"]), s["last"]), streak=s["streak"])
                for table, s in stats.items()]
        rows.sort(key=lambda r: (-r["nights"], -int(r["last"].sort.replace("-", "")), r["table"].sort))
        columns = [
            Column("table", "Bordet", "Spillerne i den rækkefølge, de sad, rundt om bordet mod venstre.", "text"),
            Column("nights", "Aftener", "Spilaftener, hvor bordet sad sådan."),
            Column("last", "Senest", "Seneste aften, bordet sad sådan.", "text"),
            Column("streak", "Længste stribe", "Flest aftener i træk med netop det bord."),
        ]
        return Table(columns, rows, empty="Ingen spilaftener med kendte pladser i perioden.")
