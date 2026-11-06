"""Makkerpar: selvmakker, when the declarer chooses to be their own partner."""

from collections import defaultdict
from fractions import Fraction

from .base import PARTNERLESS, Column, Statistic, Table, ratio
from .member_specials import add_outcome, member_rows, new_outcome, outcome_columns


class SelfPartner(Statistic):
    key = "selvmakker"
    title = "Selvmakker"
    topic = "makkerpar"
    description = ("Meldinger, hvor melderen selv var makker. Mulige er egne meldinger, hvor "
                   "selvmakker er tilladt; Sol, Ren sol, Bordlægger og Påskrig tæller ikke med.")

    def compute(self, data):
        stats = defaultdict(new_outcome)
        eligible = defaultdict(int)
        for h in data.hands:
            if h.bid_key in PARTNERLESS or (mid := data.member(h.declarer)) is None:
                continue
            eligible[mid] += 1
            if h.partner is not None and h.partner == h.declarer:
                add_outcome(stats[mid], h.won, h.scores.get(h.declarer, Fraction()))
            else:
                stats[mid]  # a member who could have, but did not
        rows = member_rows(data, stats, lambda mid, v: dict(
            eligible=eligible[mid], share=ratio(v["count"], eligible[mid])))
        columns = [
            Column("name", "Spiller", "Klubmedlemmet.", "text"),
            Column("eligible", "Mulige", "Egne meldinger, hvor selvmakker er tilladt."),
            *outcome_columns("selvmakker-meldinger"),
            Column("share", "Andel", "Selvmakker af de mulige, i procent.", "pct"),
        ]
        return Table(columns, rows, empty="Ingen egne meldinger med mulig selvmakker i perioden.")
