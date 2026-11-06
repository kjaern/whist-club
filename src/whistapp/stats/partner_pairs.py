"""Makkerpar: how each pair of club members does as declarer and partner."""

from collections import defaultdict
from fractions import Fraction

from .base import PARTNERLESS, Cell, Column, Statistic, Table, ratio


def ranks(rows, key):
    """Competition ranking (1, 2, 2, 4) by `key`, best first; rows without a key get none."""
    ranked = sorted((r for r in rows if key(r) is not None), key=key, reverse=True)
    for i, r in enumerate(ranked):
        r["rank"] = ranked[i - 1]["rank"] if i and key(ranked[i - 1]) == key(r) else i + 1


class PartnerPairs(Statistic):
    key = "makkerpar"
    title = "Makkerpar"
    topic = "makkerpar"
    description = ("Melder og makker som par, uanset hvem der meldte. Point er hver spillers "
                   "ændring. Placering efter sejrsprocent, ved lighed efter gennemsnit, som i det gamle regnskab.")

    def compute(self, data):
        pairs = defaultdict(lambda: dict(count=0, won=0, lost=0, points=Fraction()))
        for h in data.hands:
            if h.bid_key in PARTNERLESS or h.partner is None or h.partner == h.declarer:
                continue
            members = (data.member(h.declarer), data.member(h.partner))
            if None in members or members[0] == members[1]:
                continue  # a guest in the pair
            p = pairs[frozenset(members)]
            p["count"] += 1
            if h.won is not None:
                p["won" if h.won else "lost"] += 1
            p["points"] += h.scores.get(h.declarer, Fraction())

        rows = []
        for members, p in pairs.items():
            label = " + ".join(sorted((data.name_of(m) for m in members), key=str.casefold))
            rows.append(dict(pair=Cell(label, label.casefold()), count=p["count"], won=p["won"], lost=p["lost"],
                             pct=ratio(p["won"], p["won"] + p["lost"]), points=p["points"],
                             average=p["points"] / p["count"], rank=None))
        ranks(rows, lambda r: None if r["pct"] is None else (r["pct"], r["average"]))
        rows.sort(key=lambda r: (r["rank"] is None, r["rank"] or 0, r["pair"].sort))
        columns = [
            Column("pair", "Makkerpar", "De to klubmedlemmer, der spillede sammen som melder og makker.", "text"),
            Column("count", "Antal", "Antal hænder parret spillede sammen."),
            Column("won", "Vundet", "Antal vundne hænder."),
            Column("lost", "Tabt", "Antal tabte hænder."),
            Column("pct", "Sejr %", "Vundne af de afgjorte hænder, i procent.", "pct"),
            Column("points", "Point", "Summen af hver spillers pointændring.", "points"),
            Column("average", "Gennemsnit", "Gennemsnitlig pointændring pr. hånd.", "points"),
            Column("rank", "Placering", "Placering efter sejrsprocent; ved lighed efter gennemsnit."),
        ]
        return Table(columns, rows, empty="Ingen makkerpar af klubmedlemmer i perioden.")
