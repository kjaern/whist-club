"""Rekorder: each member's points per hand, best and worst single hand, and zero hands."""

from fractions import Fraction

from .base import Cell, Column, Statistic, Table
from .member_hands import member_hands


class Extremes(Statistic):
    key = "enkelthaender"
    title = "Pr. hånd"
    topic = "rekorder"
    description = "Gennemsnit pr. spillet hånd, største gevinst og største tab på én hånd, og hænder uden ændring."

    def compute(self, data):
        rows = []
        for mid, hands in member_hands(data).items():
            changes = [change for _h, change in hands]
            name = data.name_of(mid)
            rows.append(dict(
                name=Cell(name, name.casefold()), hands=len(changes),
                average=sum(changes, Fraction()) / len(changes),
                best=max(changes) if max(changes) > 0 else None,
                worst=min(changes) if min(changes) < 0 else None,
                zero=sum(1 for c in changes if c == 0),
            ))
        rows.sort(key=lambda r: r["name"].sort)
        columns = [
            Column("name", "Spiller", "Klubmedlemmet.", "text"),
            Column("hands", "Hænder", "Antal spillede hænder."),
            Column("average", "Point pr. hånd", "Gennemsnitlig pointændring pr. spillet hånd.", "points"),
            Column("best", "Største gevinst", "Største pointgevinst på én hånd.", "points"),
            Column("worst", "Største tab", "Største pointtab på én hånd.", "points"),
            Column("zero", "Nul", "Hænder uden pointændring for spilleren."),
        ]
        return Table(columns, rows, empty="Ingen hænder fra klubmedlemmer i perioden.")
