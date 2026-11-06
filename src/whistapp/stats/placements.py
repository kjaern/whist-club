"""Placeringer: how often each member finished 1st, 2nd, ... on a night."""

from collections import Counter, defaultdict
from fractions import Fraction

from .base import Cell, Column, Statistic, Table
from .night_results import night_totals, places


def label_order(label):
    """1, 1/2, 2, 2/3, 3, ...: by first place, a single place before a shared one."""
    first = label.split("/")
    return int(first[0]), len(first)


class Placements(Statistic):
    key = "placeringer"
    title = "Placeringer"
    topic = "placeringer"
    description = ("Placering på aftenen blandt de klubmedlemmer, der spillede, efter aftenens resultat. "
                   "Ved lige resultat deles pladserne, fx 1/2; snittet regner en delt plads som midten.")

    def compute(self, data):
        counts = defaultdict(Counter)
        place_sum = defaultdict(Fraction)
        for totals in night_totals(data).values():
            for mid, (label, place) in places(totals).items():
                counts[mid][label] += 1
                place_sum[mid] += place

        labels = sorted({label for c in counts.values() for label in c}, key=label_order)
        rows = []
        for mid, c in counts.items():
            name = data.name_of(mid)
            nights = sum(c.values())
            rows.append(dict(name=Cell(name, name.casefold()), nights=nights,
                             average=place_sum[mid] / nights,
                             **{f"p{label}": c[label] for label in labels}))
        rows.sort(key=lambda r: (r["average"], r["name"].sort))
        columns = [Column("name", "Spiller", "Klubmedlemmet.", "text"),
                   Column("nights", "Aftener", "Spilaftener, medlemmet spillede.")]
        columns += [Column(f"p{label}", f"Nr. {label}", f"Antal aftener som nr. {label}.") for label in labels]
        columns.append(Column("average", "Snit", "Gennemsnitlig placering; lavest er bedst.", "points"))
        return Table(columns, rows, empty="Ingen spilaftener med klubmedlemmer i perioden.")
