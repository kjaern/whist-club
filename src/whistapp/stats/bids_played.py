"""Meldinger: every bid played, per bid and number of tricks, with its outcome."""

from collections import defaultdict
from fractions import Fraction

from .base import SPECIAL, Cell, Column, Statistic, Table, bid_rank, ratio


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


def bid_name(h):
    if h.bid_key in SPECIAL or h.bid_key == "paskrig" or h.target is None:
        return h.bid_label
    return f"{h.target} {h.bid_label}"


class BidsPlayed(Statistic):
    key = "meldinger"
    title = "Meldinger"
    topic = "meldinger"
    description = ("Hver melding efter antal stik. Sol, Ren sol og Bordlægger tæller hver melders "
                   "eget forsøg. Point er meldersidens ændring.")

    def compute(self, data):
        groups = defaultdict(lambda: dict(count=0, won=0, lost=0, points=Fraction(), scored=0))
        names = {}
        for h in data.hands:
            special = h.bid_key in SPECIAL or h.bid_key == "paskrig"
            key = (bid_rank(h.bid_key), h.bid_key, None if special else h.target)
            names[key] = bid_name(h)
            g = groups[key]
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
        # Every bid the club plays is listed, also when nobody has bid it yet.
        played = {key[1] for key in groups}
        for bid, label in data.bids:
            if bid not in played:
                key = (bid_rank(bid), bid, None)
                names[key] = label
                groups[key]

        def values(g, label, order):
            no_outcome = not g["scored"]
            return dict(
                bid=Cell(label, order), count=g["count"], won=None if no_outcome else g["won"],
                lost=None if no_outcome else g["lost"], pct=ratio(g["won"], g["won"] + g["lost"]),
                points=None if no_outcome else g["points"],
                average=None if no_outcome else g["points"] / g["scored"],
            )

        rows = [values(groups[k], names[k], n) for n, k in enumerate(sorted(groups))]
        total = dict(count=0, won=0, lost=0, points=Fraction(), scored=0)
        for g in groups.values():
            for field in total:
                total[field] += g[field]
        columns = [
            Column("bid", "Melding", "Meldingen og antal budte stik.", "text"),
            Column("count", "Antal", "Antal gange meldingen er spillet; Sol m.fl. tæller hver melder."),
            Column("won", "Vundet", "Antal vundne meldinger."),
            Column("lost", "Tabt", "Antal tabte meldinger."),
            Column("pct", "Sejr %", "Vundne af de afgjorte meldinger, i procent.", "pct"),
            Column("points", "Point i alt", "Summen af melderens pointændring.", "points"),
            Column("average", "Gennemsnit", "Melderens gennemsnitlige pointændring pr. melding.", "points"),
        ]
        footer = values(total, "I alt", None) if rows else None
        return Table(columns, rows, footer=footer, empty="Ingen meldinger i perioden.")
