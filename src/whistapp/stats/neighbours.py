"""Naboer: who sat right before and after each member, and how the member did next to whom."""

from collections import Counter, defaultdict
from fractions import Fraction

from .base import Cell, Column, Statistic, Table, pct_text, points_text
from .seating import table_order

MIN_TOGETHER = 20  # hands side by side before a neighbour can be lucky or unlucky
MIN_TOGETHER_NIGHT = 5


def _most(counter, total, data):
    """The member seen most often, as 'Navn (37 %)'; ties go by name."""
    if not counter:
        return None
    mid, n = min(counter.items(), key=lambda item: (-item[1], data.name_of(item[0]).casefold()))
    share = Fraction(n, total)
    return Cell(f"{data.name_of(mid)} ({pct_text(share)})", float(share))


class Neighbours(Statistic):
    key = "naboer"
    title = "Naboer"
    topic = "bordet"
    description = ("Hvem sad lige før og lige efter hvert klubmedlem ved bordet i de hænder, medlemmet spillede, "
                   "og hvordan det gik ved siden af hvem. Den, der sidder til venstre, spiller lige efter dig. "
                   "Kun aftenens sidste bordplan kendes, og importerede aftener bruger rækkefølgen fra arket.")

    def compute(self, data):
        minimum = MIN_TOGETHER_NIGHT if data.scope.kind == "night" else MIN_TOGETHER
        hands = Counter()
        points = defaultdict(Fraction)
        after = defaultdict(Counter)
        before = defaultdict(Counter)
        together = defaultdict(Counter)
        together_points = defaultdict(lambda: defaultdict(Fraction))
        for h in data.hands:
            order = table_order(data, h)
            n = len(order)
            if n < 3:
                continue
            for i, pid in enumerate(order):
                mid = data.member(pid)
                if mid is None:
                    continue
                score = h.scores.get(pid, Fraction())
                hands[mid] += 1
                points[mid] += score
                left, right = data.member(order[(i + 1) % n]), data.member(order[i - 1])
                for side, nid in ((after, left), (before, right)):
                    if nid is not None and nid != mid:
                        side[mid][nid] += 1
                for nid in {left, right} - {None, mid}:
                    together[mid][nid] += 1
                    together_points[mid][nid] += score

        rows = []
        for mid, total in hands.items():
            name = data.name_of(mid)
            averages = {nid: together_points[mid][nid] / n for nid, n in together[mid].items() if n >= minimum}
            ranked = sorted(averages, key=lambda nid: (-averages[nid], data.name_of(nid).casefold()))

            def neighbour(nid):
                return Cell(f"{data.name_of(nid)} ({points_text(averages[nid])})", float(averages[nid]))

            rows.append(dict(
                name=Cell(name, name.casefold()), hands=total, average=points[mid] / total,
                after=_most(after[mid], total, data), before=_most(before[mid], total, data),
                lucky=neighbour(ranked[0]) if ranked else None,
                unlucky=neighbour(ranked[-1]) if len(ranked) > 1 else None,
            ))
        rows.sort(key=lambda r: r["name"].sort)
        columns = [
            Column("name", "Spiller", "Klubmedlemmet.", "text"),
            Column("hands", "Hænder", "Hænder, medlemmet spillede med en kendt plads ved bordet."),
            Column("after", "Oftest efter dig", "Klubmedlemmet, der oftest sad til venstre og spillede lige efter, "
                   "med andelen af medlemmets hænder.", "text", graph=False),
            Column("before", "Oftest før dig", "Klubmedlemmet, der oftest sad til højre og spillede lige før, "
                   "med andelen af medlemmets hænder.", "text", graph=False),
            Column("average", "Snit", "Medlemmets gennemsnitlige pointændring pr. hånd, uanset naboer.", "points"),
            Column("lucky", "Lykkenabo", f"Naboen (på en af siderne), ved siden af hvem medlemmet fik flest point pr. "
                   f"hånd i snit. Kræver mindst {minimum} hænder side om side.", "text", graph=False),
            Column("unlucky", "Uheldsnabo", f"Naboen, ved siden af hvem medlemmet fik færrest point pr. hånd i snit. "
                   f"Kræver mindst {minimum} hænder side om side og mindst to naboer at vælge imellem.", "text", graph=False),
        ]
        return Table(columns, rows, empty="Ingen hænder med kendte pladser i perioden.")
