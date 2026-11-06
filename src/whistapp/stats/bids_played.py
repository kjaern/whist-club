"""Meldinger: every bid the club plays, with how often it was bid and its outcome."""

from collections import defaultdict
from fractions import Fraction

from .base import SPECIAL, Cell, Column, Statistic, Table, ratio

FIELDS = ("count", "won", "lost", "points", "scored")


def attempts(h):
    """(bidder player id, won) for each attempt in a hand; paskrig has none.

    A special bid (Sol, Ren sol, Bordlægger) is one attempt per bidder. Hands imported
    without per-bidder results fall back to the declarer and the hand's outcome.
    """
    if h.bid_key == "paskrig":
        return []
    if h.bid_key in SPECIAL and h.specials:
        return [(pid, won) for pid, _actual, won in h.specials]
    return [(h.declarer, h.won)]


class BidsPlayed(Statistic):
    key = "meldinger"
    title = "Meldinger"
    topic = "meldinger"
    description = ("Hver melding, med VIP fordelt på niveau. Sol, Ren sol og Bordlægger tæller hver "
                   "melders eget forsøg. Point er meldersidens ændring.")

    def compute(self, data):
        groups = defaultdict(lambda: dict.fromkeys(FIELDS, 0) | dict(points=Fraction()))
        labels = dict(data.bids)  # every bid the club plays, also when nobody has bid it yet
        for h in data.hands:
            labels.setdefault(h.bid_key, h.bid_label)
            g = groups[h.bid_key]
            if h.bid_key == "paskrig":
                g["count"] += 1
                continue
            for pid, won in attempts(h):
                g["count"] += 1
                if won is not None:
                    g["won" if won else "lost"] += 1
                if pid is not None:
                    g["points"] += h.scores.get(pid, Fraction())
                    g["scored"] += 1

        def summed(bids):
            total = dict.fromkeys(FIELDS, 0) | dict(points=Fraction())
            for bid in bids:
                for field in FIELDS:
                    total[field] += groups[bid][field] if bid in groups else 0
            return total

        rows = []

        def add(g, label, level=None):
            no_outcome = not g["scored"]
            rows.append(dict(
                bid=Cell(label, len(rows)), count=g["count"], won=None if no_outcome else g["won"],
                lost=None if no_outcome else g["lost"], pct=ratio(g["won"], g["won"] + g["lost"]),
                points=None if no_outcome else g["points"],
                average=None if no_outcome else g["points"] / g["scored"],
                _class=level,
            ))

        bids = sorted(labels, key=data.bid_rank)
        vip = [b for b in bids if b.startswith("vip")]
        for bid in bids:
            if bid in vip[1:]:
                continue
            if bid in vip:
                # VIP is one bid; its level (1-3) is an input, so each level follows it.
                add(summed(vip), "VIP", "group")
                for level in vip:
                    add(summed([level]), labels[level], "sub1")
            else:
                add(summed([bid]), labels[bid])
        columns = [
            Column("bid", "Melding", "Meldingen.", "text"),
            Column("count", "Antal", "Antal gange meldingen er spillet; Sol m.fl. tæller hver melder."),
            Column("won", "Vundet", "Antal vundne meldinger."),
            Column("lost", "Tabt", "Antal tabte meldinger."),
            Column("pct", "Sejr %", "Vundne af de afgjorte meldinger, i procent.", "pct"),
            Column("points", "Point i alt", "Summen af melderens pointændring.", "points"),
            Column("average", "Gennemsnit", "Melderens gennemsnitlige pointændring pr. melding.", "points"),
        ]
        add(summed(list(groups)), "I alt")
        footer = rows.pop()
        return Table(columns, rows, footer=footer, empty="Ingen meldinger i perioden.")
