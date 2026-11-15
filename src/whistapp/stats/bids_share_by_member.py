"""Meldinger: how a member's bids are spread over the bids."""

from .base import Cell, Column, Statistic, Table, ratio
from .bids_by_member import member_bids


class BidsShareByMember(Statistic):
    key = "meldinger_medlem_andel"
    title = "Hvem melder hvad %"
    topic = "meldinger"
    description = ("Hvor ofte hvert klubmedlem melder en melding, i procent af medlemmets egne meldinger. "
                   "Påskrig og gæsters meldinger tæller ikke med.")

    def compute(self, data):
        counts, names, labels, bids = member_bids(data)
        first_vip = next((b for b in bids if b.startswith("vip")), None)

        def shares(per):
            """per: bid -> number of bids. Each bid's share of them all, and VIP on all levels."""
            total = sum(per.values())
            row = {b: ratio(per.get(b, 0), total) for b in bids}
            row["vip"] = ratio(sum(n for b, n in per.items() if b.startswith("vip")), total)
            row["total"] = total
            return row

        rows, club = [], {}
        for mid, per in sorted(counts.items(), key=lambda item: names[item[0]].casefold()):
            own = {b: c[0] for b, c in per.items()}
            for b, n in own.items():
                club[b] = club.get(b, 0) + n
            rows.append(dict(name=Cell(names[mid], names[mid].casefold()), **shares(own)))
        columns = [Column("name", "Spiller", "Klubmedlemmet.", "text")]
        for b in bids:
            if b == first_vip:
                # VIP is one bid; the levels follow its total.
                columns.append(Column("vip", "VIP", "VIP-meldinger på alle niveauer, i procent af medlemmets meldinger.",
                                      "pct"))
            columns.append(Column(b, labels[b], f"{labels[b]}-meldinger, i procent af medlemmets meldinger.", "pct"))
        columns.append(Column("total", "I alt", "Antal meldinger i alt."))
        footer = dict(name="Klubben", **shares(club)) if rows else None
        return Table(columns, rows, footer=footer, empty="Ingen meldinger fra klubmedlemmer i perioden.")
