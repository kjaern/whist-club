"""Makkerens plads: does a bid go better with the partner to the left, opposite or to the right?"""

from collections import defaultdict

from .base import PARTNERLESS, Cell, Column, Statistic, Table, pct_text, ratio
from .seating import SEAT_NOTE, offset, table_order

SEATS = ((1, "left", "Makker til venstre", "Makkeren sad til venstre for melderen og spillede lige efter."),
         (2, "opposite", "Makker overfor", "Makkeren sad overfor melderen."),
         (3, "right", "Makker til højre", "Makkeren sad til højre for melderen og spillede lige før."))


def _result(won, lost):
    pct = ratio(won, won + lost)
    if pct is None:
        return None
    return Cell(f"{pct_text(pct)} ({won + lost})", float(pct))


class PartnerSeat(Statistic):
    key = "makkerens-plads"
    title = "Makkerens plads"
    topic = "bordet"
    description = ("Sejrsprocent for meldinger med makker, efter hvor makkeren sad i forhold til melderen. "
                   "Pladserne regnes blandt de fire, der spiller hånden; den, der sidder over, tæller ikke med. "
                   "Tallet i parentes er afgjorte hænder. " + SEAT_NOTE)

    def compute(self, data):
        tally = defaultdict(lambda: defaultdict(lambda: [0, 0]))  # member (None = club) -> seat -> [won, lost]
        counts = defaultdict(int)
        for h in data.hands:
            if h.bid_key in PARTNERLESS or h.partner is None or h.partner == h.declarer or h.declarer is None:
                continue
            order = table_order(data, h)
            if len(order) != 4 or h.declarer not in order or h.partner not in order:
                continue
            seat = offset(order, h.declarer, h.partner)
            mid = data.member(h.declarer)
            for who in ({mid, None} if mid is not None else {None}):
                counts[who] += 1
                if h.won is not None:
                    tally[who][seat][0 if h.won else 1] += 1

        def row(who):
            return dict(count=counts[who], **{key: _result(*tally[who][seat]) for seat, key, _, _ in SEATS})

        rows = []
        for mid in counts:
            if mid is not None:
                name = data.name_of(mid)
                rows.append(dict(name=Cell(name, name.casefold()), **row(mid)))
        rows.sort(key=lambda r: r["name"].sort)
        columns = [Column("name", "Melder", "Klubmedlemmet, der meldte.", "text"),
                   Column("count", "Antal", "Meldinger med makker og fire ved bordet.")]
        columns += [Column(key, label, f"{about} Sejrsprocent og antal afgjorte hænder.", "text", graph=False)
                    for _, key, label, about in SEATS]
        footer = dict(name=Cell("Klubben"), **row(None)) if counts else None
        return Table(columns, rows, footer=footer, empty="Ingen meldinger med makker og kendte pladser i perioden.")
