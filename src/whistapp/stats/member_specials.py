"""Makkerpar: each member alone with their own Sol, Ren sol and Bordlægger bids."""

from collections import defaultdict
from fractions import Fraction

from .base import SPECIAL, Cell, Column, Statistic, Table, ratio
from .bids_played import attempts


def new_outcome():
    return dict(count=0, won=0, lost=0, points=Fraction())


def add_outcome(v, won, points):
    v["count"] += 1
    if won is not None:
        v["won" if won else "lost"] += 1
    v["points"] += points


def outcome_values(v):
    """Antal, vundet, tabt, sejr %, point and average for one member."""
    return dict(count=v["count"], won=v["won"], lost=v["lost"], pct=ratio(v["won"], v["won"] + v["lost"]),
                points=v["points"] if v["count"] else None,
                average=v["points"] / v["count"] if v["count"] else None)


def outcome_columns(what):
    return [
        Column("count", "Antal", f"Antal {what}."),
        Column("won", "Vundet", f"Vundne {what}."),
        Column("lost", "Tabt", f"Tabte {what}."),
        Column("pct", "Sejr %", f"Vundne af de afgjorte {what}, i procent.", "pct"),
        Column("points", "Point", f"Summen af pointændringen for {what}.", "points"),
        Column("average", "Snit", f"Gennemsnitlig pointændring for {what}.", "points"),
    ]


def member_rows(data, stats, extra=lambda mid, v: {}):
    rows = []
    for mid, v in stats.items():
        name = data.name_of(mid)
        rows.append(dict(name=Cell(name, name.casefold()), **outcome_values(v), **extra(mid, v)))
    return sorted(rows, key=lambda r: r["name"].sort)


class MemberSpecials(Statistic):
    key = "alene"
    title = "Alene"
    topic = "makkerpar"
    description = "Medlemmets egne Sol-, Ren sol- og Bordlægger-meldinger, hvor man spiller alene."

    def compute(self, data):
        stats = defaultdict(new_outcome)
        for h in data.hands:
            if h.bid_key in SPECIAL:
                for pid, won in attempts(h):
                    if (mid := data.member(pid)) is not None:
                        add_outcome(stats[mid], won, h.scores.get(pid, Fraction()))
        columns = [Column("name", "Spiller", "Klubmedlemmet.", "text"),
                   *outcome_columns("egne Sol-, Ren sol- og Bordlægger-meldinger")]
        return Table(columns, member_rows(data, stats),
                     empty="Ingen Sol-, Ren sol- eller Bordlægger-meldinger i perioden.")
