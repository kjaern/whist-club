"""Placeringer: a year's nights side by side, each member's result per night."""

from fractions import Fraction

from .base import Cell, Column, Statistic, Table
from .night_results import night_totals


class NightGrid(Statistic):
    key = "aftener"
    title = "Aftener"
    topic = "placeringer"
    description = "Hvert medlems resultat pr. spilaften i året, i point; — betyder ikke med den aften."
    scopes = ("year",)

    def compute(self, data):
        totals = night_totals(data)
        members = {mid for night in totals.values() for mid in night}
        rows = []
        for mid in members:
            name = data.name_of(mid)
            row = dict(name=Cell(name, name.casefold()),
                       total=sum((night.get(mid, Fraction()) for night in totals.values()), Fraction()))
            row.update({f"n{sid}": night.get(mid) for sid, night in totals.items()})
            rows.append(row)
        rows.sort(key=lambda r: r["name"].sort)
        columns = [Column("name", "Spiller", "Klubmedlemmet.", "text")]
        for sid in totals:
            night = data.nights[sid]
            year, month, day = night.date.split("-")
            about = night.date if night.title in ("", night.date) else f"{night.date} · {night.title}"
            columns.append(Column(f"n{sid}", f"{int(day)}/{int(month)}", about, "points"))
        columns.append(Column("total", "Sum", "Summen af årets aftener.", "points"))
        return Table(columns, rows, empty="Ingen spilaftener med klubmedlemmer i året.")
