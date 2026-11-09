"""Plads ift. giver: how each seat counted from the dealer does, for the whole club."""

from collections import Counter, defaultdict
from fractions import Fraction

from .base import SPECIAL, Cell, Column, Statistic, Table, ratio
from .seating import offset, table_order

SEATS = {1: "Forhånd (1. efter giver)", 2: "2. efter giver", 3: "3. efter giver", 0: "Giver"}


class DealerSeat(Statistic):
    key = "plads-giver"
    title = "Plads ift. giver"
    topic = "placeringer"
    description = ("Hvordan det går på hver plads, regnet fra giveren, i hænder med fire ved bordet. "
                   "Forhånd sidder til venstre for giveren og spiller ud. Giveren gemmes først fra 7/11/2026 "
                   "og kun på aftener spillet i appen.")

    def compute(self, data):
        hands = 0
        points = defaultdict(Fraction)
        declared = Counter()
        won = Counter()
        lost = Counter()
        for h in data.hands:
            order = table_order(data, h)
            if h.dealer is None or len(order) != 4 or h.dealer not in order:
                continue
            hands += 1
            for pid in order:
                points[offset(order, h.dealer, pid)] += h.scores.get(pid, Fraction())
            if h.bid_key in SPECIAL and h.specials:
                bidders = [(pid, w) for pid, _actual, w in h.specials]
            elif h.bid_key != "paskrig" and h.declarer is not None:
                bidders = [(h.declarer, h.won)]
            else:
                bidders = []
            for pid, w in bidders:
                if pid not in order:
                    continue
                seat = offset(order, h.dealer, pid)
                declared[seat] += 1
                if w is not None:
                    (won if w else lost)[seat] += 1

        if not hands:
            return Table([], [], empty="Ingen hænder med kendt giver i perioden. Giveren gemmes først fra 7/11/2026 "
                                       "og kun på aftener spillet i appen.")
        rows = [dict(seat=Cell(label, i), declared=declared[seat], share=ratio(declared[seat], hands),
                     pct=ratio(won[seat], won[seat] + lost[seat]), average=points[seat] / hands)
                for i, (seat, label) in enumerate(SEATS.items())]
        columns = [
            Column("seat", "Plads", "Pladsen regnet fra giveren, i den rækkefølge der spilles ud.", "text"),
            Column("declared", "Meldinger", "Gange, spilleren på pladsen meldte (Sol og lignende tæller pr. melder)."),
            Column("share", "Andel", "Meldinger i forhold til antal hænder.", "pct"),
            Column("pct", "Sejr %", "Vundne af de afgjorte meldinger fra pladsen.", "pct"),
            Column("average", "Snit point", "Gennemsnitlig pointændring pr. hånd for spilleren på pladsen.", "points"),
        ]
        return Table(columns, rows, footer=dict(seat=Cell(f"{hands} hænder"), declared=sum(declared.values()),
                                                share=None, pct=None, average=None))
