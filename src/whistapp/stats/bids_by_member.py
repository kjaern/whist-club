"""Meldinger: who bids what, and how often it succeeds."""

from collections import defaultdict

from .base import Cell, Column, Statistic, Table, pct_text, ratio
from .bids_played import attempts


def member_bids(data):
    """(counts, names, labels, bids): member -> bid -> [n, won, lost], member names, bid labels
    and the bid keys in score-book order. Påskrig and guests' bids are left out."""
    counts = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))
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
    # Every bid a member can make is listed, also when nobody has bid it yet.
    for bid, label in data.bids:
        if bid != "paskrig":
            labels.setdefault(bid, label)
    return counts, names, labels, sorted(labels, key=data.bid_rank)


class BidsByMember(Statistic):
    key = "meldinger_medlem"
    title = "Hvem melder hvad"
    topic = "meldinger"
    description = ("Antal meldinger pr. klubmedlem og melding, med sejrsprocent i parentes. "
                   "Klubben er alle medlemmers meldinger lagt sammen. "
                   "Påskrig og gæsters meldinger tæller ikke med.")

    def compute(self, data):
        counts, names, labels, bids = member_bids(data)

        def text(n, won, lost):
            share = ratio(won, won + lost)
            return Cell(f"{n} ({pct_text(share)})" if share is not None else str(n), n)

        all_bids = sum(c[0] for per in counts.values() for c in per.values())
        rows = []
        for mid, per in sorted(counts.items(), key=lambda item: names[item[0]].casefold()):
            n, won, lost = (sum(c[i] for c in per.values()) for i in range(3))
            row = dict(name=Cell(names[mid], names[mid].casefold()), total=n,
                       share=ratio(n, all_bids), pct=ratio(won, won + lost))
            row.update({b: text(*per[b]) if b in per else Cell("0", 0) for b in bids})
            vip = [per[b] for b in bids if b.startswith("vip") and b in per]
            row["vip"] = text(*(sum(c[i] for c in vip) for i in range(3))) if vip else Cell("0", 0)
            rows.append(row)
        footer = None
        if rows:
            # Klubben: all members' bids together.
            club = {b: [sum(per[b][i] for per in counts.values() if b in per) for i in range(3)] for b in bids}
            n, won, lost = (sum(c[i] for c in club.values()) for i in range(3))
            footer = dict(name="Klubben", total=n, share=ratio(n, all_bids), pct=ratio(won, won + lost))
            footer.update({b: text(*c) for b, c in club.items()})
            footer["vip"] = text(*(sum(c[i] for b, c in club.items() if b.startswith("vip")) for i in range(3)))
        columns =[Column("name", "Spiller", "Klubmedlemmet.", "text")]
        for b in bids:
            if b == next((x for x in bids if x.startswith("vip")), None):
                # VIP is one bid; the levels follow its total.
                columns.append(Column("vip", "VIP", "Antal VIP-meldinger på alle niveauer (sejrsprocent).", "text"))
            columns.append(Column(b, labels[b], f"Antal {labels[b]}-meldinger (sejrsprocent).", "text"))
        columns += [
            Column("total", "I alt", "Antal meldinger i alt."),
            Column("share", "Andel", "Medlemmets andel af alle klubmedlemmers meldinger.", "pct"),
            Column("pct", "Sejr %", "Vundne af de afgjorte meldinger, i procent.", "pct"),
        ]
        return Table(columns, rows, footer=footer, empty="Ingen meldinger fra klubmedlemmer i perioden.")
