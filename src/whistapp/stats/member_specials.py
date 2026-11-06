"""Makkerpar: each member alone — selvmakker, Sol/Ren sol/Bordlægger and Fejl40."""

from collections import defaultdict
from fractions import Fraction

from .base import PARTNERLESS, SPECIAL, Cell, Column, Statistic, Table, ratio
from .bids_played import attempts

GROUPS = (("self", "Selvmakker", "meldinger, hvor melderen selv var makker"),
          ("sol", "Sol m.fl.", "egne Sol-, Ren sol- og Bordlægger-meldinger"))


class MemberSpecials(Statistic):
    key = "alene"
    title = "Alene"
    topic = "makkerpar"
    description = ("Når medlemmet spiller alene: selvmakker og Sol/Ren sol/Bordlægger, "
                   "plus Fejl40. Fejl40 tæller kun aftener, hvor der opkræves penge.")

    def compute(self, data):
        stats = defaultdict(lambda: {g: dict(count=0, won=0, lost=0, points=Fraction()) for g, *_ in GROUPS}
                            | dict(fejl40=0, fejl40_kr=0))

        def add(mid, group, won, points):
            s = stats[mid][group]
            s["count"] += 1
            if won is not None:
                s["won" if won else "lost"] += 1
            s["points"] += points

        for h in data.hands:
            if h.bid_key not in PARTNERLESS and h.partner is not None and h.partner == h.declarer:
                if (mid := data.member(h.declarer)) is not None:
                    add(mid, "self", h.won, h.scores.get(h.declarer, Fraction()))
            elif h.bid_key in SPECIAL:
                for pid, won in attempts(h):
                    if (mid := data.member(pid)) is not None:
                        add(mid, "sol", won, h.scores.get(pid, Fraction()))
        for night in data.nights.values():
            for pid, amount in night.penalties:
                if (mid := data.member(pid)) is not None:
                    stats[mid]["fejl40"] += 1
                    stats[mid]["fejl40_kr"] += amount

        rows = []
        for mid, s in stats.items():
            name = data.name_of(mid)
            row = dict(name=Cell(name, name.casefold()), fejl40=s["fejl40"], fejl40_kr=s["fejl40_kr"])
            for g, *_ in GROUPS:
                v = s[g]
                row.update({f"{g}_count": v["count"], f"{g}_won": v["won"], f"{g}_lost": v["lost"],
                            f"{g}_pct": ratio(v["won"], v["won"] + v["lost"]),
                            f"{g}_points": v["points"] if v["count"] else None,
                            f"{g}_average": v["points"] / v["count"] if v["count"] else None})
            rows.append(row)
        rows.sort(key=lambda r: r["name"].sort)

        columns = [Column("name", "Spiller", "Klubmedlemmet.", "text")]
        for g, label, what in GROUPS:
            columns += [
                Column(f"{g}_count", label, f"Antal {what}."),
                Column(f"{g}_won", "Vundet", f"Vundne {what}."),
                Column(f"{g}_lost", "Tabt", f"Tabte {what}."),
                Column(f"{g}_pct", "Sejr %", f"Vundne af de afgjorte {what}, i procent.", "pct"),
                Column(f"{g}_points", "Point", f"Summen af pointændringen for {what}.", "points"),
                Column(f"{g}_average", "Snit", f"Gennemsnitlig pointændring for {what}.", "points"),
            ]
        columns += [
            Column("fejl40", "Fejl40", "Antal registrerede Fejl40."),
            Column("fejl40_kr", "Fejl40 kr.", "Betalt for Fejl40 i alt.", "points"),
        ]
        return Table(columns, rows, empty="Ingen selvmakker, Sol-meldinger eller Fejl40 i perioden.")
