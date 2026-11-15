"""Spillerækkefølge: the players of each hand in the order they sat, and how they did."""

from collections import defaultdict
from fractions import Fraction

from .base import Cell, Column, Statistic, Table
from .seating import SEAT_NOTE, danish_date, table_order


def playing_order(data, hand):
    """The members who played the hand, in seat order, starting from the member first by name,
    so the same order counts once whoever sat 'first'. None when a guest played the hand."""
    members = [data.member(pid) for pid in table_order(data, hand)]
    if len(members) < 2 or None in members:
        return None
    start = min(range(len(members)), key=lambda i: (data.name_of(members[i]).casefold(), i))
    return tuple(members[start:] + members[:start])


class Seatings(Statistic):
    key = "spilleraekkefolge"
    title = "Spillerækkefølge"
    topic = "bordet"
    description = ("Hvem der spillede hånden, i den rækkefølge de sad rundt om bordet mod venstre, hvor mange hænder "
                   "der blev spillet sådan, og hvert medlems gennemsnitlige pointændring pr. hånd i netop den "
                   "rækkefølge. Den, der sidder over, er ikke med i håndens rækkefølge, så en aften med fem ved bordet "
                   "giver flere rækker. Det er den samme rækkefølge, uanset hvem der sad 'først'. Hænder med en gæst "
                   "tæller ikke med. " + SEAT_NOTE)

    def compute(self, data):
        stats = defaultdict(lambda: dict(hands=0, nights=set(), last="", points=defaultdict(Fraction)))
        for h in data.hands:
            order = playing_order(data, h)
            if order is None:
                continue
            s = stats[order]
            s["hands"] += 1
            s["nights"].add(h.session_id)
            s["last"] = max(s["last"], data.nights[h.session_id].date)
            for pid in h.participants:
                s["points"][data.member(pid)] += h.scores.get(pid, Fraction())

        names = {mid: data.name_of(mid) for order in stats for mid in order}
        members = sorted(names, key=lambda mid: (names[mid].casefold(), mid))
        rows = []
        for order, s in stats.items():
            text = [names[mid] for mid in order]
            row = dict(table=Cell(" → ".join(text), " ".join(text).casefold()), hands=s["hands"],
                       nights=len(s["nights"]), last=Cell(danish_date(s["last"]), s["last"]))
            row.update({f"m{mid}": s["points"][mid] / s["hands"] if mid in order else None for mid in members})
            rows.append(row)
        rows.sort(key=lambda r: (-r["hands"], -int(r["last"].sort.replace("-", "")), r["table"].sort))
        columns = [
            Column("table", "Rækkefølge", "Håndens spillere i den rækkefølge, de sad, rundt om bordet mod venstre: "
                   "den næste i rækken spiller lige efter.", "text"),
            Column("hands", "Hænder", "Hænder spillet i den rækkefølge."),
            Column("nights", "Aftener", "Spilaftener med mindst én hånd i den rækkefølge."),
            Column("last", "Senest", "Seneste aften med en hånd i den rækkefølge.", "text"),
        ]
        columns += [Column(f"m{mid}", names[mid], f"{names[mid]}s gennemsnitlige pointændring pr. hånd i den "
                           "rækkefølge.", "points") for mid in members]
        return Table(columns, rows, empty="Ingen hænder med kendte pladser i perioden.")
