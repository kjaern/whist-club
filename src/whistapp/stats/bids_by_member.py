"""Meldinger: who bids what, and how often it succeeds."""

from collections import defaultdict

from .base import Cell, Column, Statistic, Table, bid_rank, pct_text, ratio
from .bids_played import attempts


class BidsByMember(Statistic):
    key = "meldinger_medlem"
    title = "Hvem melder hvad"
    topic = "meldinger"
    description = ("Antal meldinger pr. klubmedlem og melding, med sejrsprocent i parentes. "
                   "Påskrig og gæsters meldinger tæller ikke med.")

    def compute(self, data):
        counts = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))  # member -> bid -> [n, won, lost]
        labels, names = {}, {}
        for h in data.hands:
            for pid, won in attempts(h):
                mid = data.member(pid)
                if mid is None:
                    continue
                names[mid] = data.member_name(mid, data.players[pid].name)
                labels.setdefault(h.bid_key, h.bid_label)
                c = counts[mid][h.bid_key]
                c[0] += 1
                if won is not None:
                    c[1 if won else 2] += 1

        def text(n, won, lost):
            share = ratio(won, won + lost)
            return Cell(f"{n} ({pct_text(share)})" if share is not None else str(n), n)

        # Every bid a member can make gets a column, also when nobody has bid it yet.
        for bid, label in data.bids:
            if bid != "paskrig":
                labels.setdefault(bid, label)
        bids = sorted(labels, key=bid_rank)
        all_bids = sum(c[0] for per in counts.values() for c in per.values())
        rows = []
        for mid, per in sorted(counts.items(), key=lambda item: names[item[0]].casefold()):
            n, won, lost = (sum(c[i] for c in per.values()) for i in range(3))
            row = dict(name=Cell(names[mid], names[mid].casefold()), total=n,
                       share=ratio(n, all_bids), pct=ratio(won, won + lost))
            row.update({b: text(*per[b]) if b in per else Cell("0", 0) for b in bids})
            rows.append(row)
        columns = [Column("name", "Spiller", "Klubmedlemmet.", "text")]
        columns += [Column(b, labels[b], f"Antal {labels[b]}-meldinger (sejrsprocent).", "text") for b in bids]
        columns += [
            Column("total", "I alt", "Antal meldinger i alt."),
            Column("share", "Andel", "Medlemmets andel af alle klubmedlemmers meldinger.", "pct"),
            Column("pct", "Sejr %", "Vundne af de afgjorte meldinger, i procent.", "pct"),
        ]
        return Table(columns, rows, empty="Ingen meldinger fra klubmedlemmer i perioden.")
