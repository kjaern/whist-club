"""Rekorder: each member's longest winning and losing streaks."""

from fractions import Fraction

from .base import Cell, Column, Statistic, Table
from .member_hands import member_hands


def longest(changes, sign):
    """(longest run of hands whose change has this sign, most points in one such run).

    The points keep their sign: a losing run's most is negative.
    """
    best_len, best_size, run, size = 0, Fraction(), 0, Fraction()
    for change in changes:
        if change * sign > 0:
            run, size = run + 1, size + abs(change)
            best_len, best_size = max(best_len, run), max(best_size, size)
        else:
            run, size = 0, Fraction()
    return best_len, best_size * sign


class Streaks(Statistic):
    key = "rekker"
    title = "Vinder- og taberrækker"
    topic = "rekorder"
    description = ("Flest hænder i træk med gevinst eller tab, på tværs af aftener i datoorden. "
                   "En hånd uden ændring bryder rækken; en hånd man sidder over gør ikke.")

    def compute(self, data):
        rows = []
        for mid, hands in member_hands(data).items():
            changes = [change for _h, change in hands]
            win, won = longest(changes, 1)
            loss, lost = longest(changes, -1)
            name = data.name_of(mid)
            rows.append(dict(name=Cell(name, name.casefold()), win=win, won=won if win else None,
                             loss=loss, lost=lost if loss else None))
        rows.sort(key=lambda r: r["name"].sort)
        columns = [
            Column("name", "Spiller", "Klubmedlemmet.", "text"),
            Column("win", "Vinderrække", "Flest hænder i træk med gevinst."),
            Column("won", "Mest vundet i træk", "Flest point vundet i én vinderrække.", "points"),
            Column("loss", "Taberrække", "Flest hænder i træk med tab."),
            Column("lost", "Mest tabt i træk", "Flest point tabt i én taberrække.", "points"),
        ]
        return Table(columns, rows, empty="Ingen hænder fra klubmedlemmer i perioden.")
