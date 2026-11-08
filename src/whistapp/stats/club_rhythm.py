"""Rekorder: the club's rhythm over a year or its lifetime, as in the old score book."""

from datetime import date
from fractions import Fraction

from .base import PARTNERLESS, SPECIAL, Cell, Column, Statistic, Table, points_text


class ClubRhythm(Statistic):
    key = "rytme"
    title = "Klubbens rytme"
    topic = "rekorder"
    description = "Gennemsnit pr. spilaften med klubmedlemmer i perioden."
    scopes = ("all", "year", "period")

    def compute(self, data):
        nights = data.nights_with_members()
        if not nights:
            return Table([], [], empty="Ingen klubspilaftener i perioden.")
        ids = {n.id for n in nights}
        hands = [h for h in data.hands if h.session_id in ids]
        self_partner = sum(1 for h in hands if h.bid_key not in PARTNERLESS
                           and h.partner is not None and h.partner == h.declarer)
        special = sum(1 for h in hands if h.bid_key in SPECIAL)
        days = sorted({date.fromisoformat(n.date) for n in nights})
        gaps = [(b - a).days for a, b in zip(days, days[1:])]

        def per_night(n):
            return Cell(points_text(Fraction(n, len(nights))), float(Fraction(n, len(nights))))

        rows = [
            dict(what=Cell("Spilaftener", 0), value=Cell(str(len(nights)), len(nights))),
            dict(what=Cell("Hænder pr. aften", 1), value=per_night(len(hands))),
            dict(what=Cell("Selvmakker pr. aften", 2), value=per_night(self_partner)),
            dict(what=Cell("Sol, Ren sol og Bordlægger pr. aften", 3), value=per_night(special)),
            dict(what=Cell("Dage mellem aftener", 4),
                 value=Cell(points_text(Fraction(sum(gaps), len(gaps))), sum(gaps) / len(gaps)) if gaps else None),
        ]
        columns = [
            Column("what", "Mål", "Hvad der måles.", "text"),
            Column("value", "Værdi", "Gennemsnit pr. spilaften; dage er gennemsnittet mellem to aftener.", "text"),
        ]
        return Table(columns, rows)
