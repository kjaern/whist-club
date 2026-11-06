"""Meldinger: hands where several players bid Sol, Ren sol or Bordlægger at once."""

from collections import Counter

from .base import SPECIAL, Cell, Column, Statistic, Table


class SpecialMulti(Statistic):
    key = "flere_meldere"
    title = "Flere meldere på samme hånd"
    topic = "meldinger"
    description = "Hænder hvor to eller flere spillere meldte Sol, Ren sol eller Bordlægger samtidig."

    def compute(self, data):
        by_size = Counter(len(h.specials) for h in data.hands
                          if h.bid_key in SPECIAL and len(h.specials) >= 2)
        rows = [dict(size=Cell(f"{n} meldere", n), hands=by_size[n]) for n in sorted(by_size)]
        columns = [
            Column("size", "Meldere", "Antal spillere med egen melding på hånden.", "text"),
            Column("hands", "Hænder", "Antal hænder."),
        ]
        return Table(columns, rows, empty="Ingen hænder med flere meldere i perioden.")
