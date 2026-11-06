"""Placeringer: how often each member finished 1st, 2nd, ... on a night, and a fair score."""

from collections import Counter, defaultdict
from fractions import Fraction

from .base import Cell, Column, Statistic, Table
from .night_results import night_totals, place_score, places


def label_order(label):
    """1, 1/2, 2, 2/3, 3, ...: by first place, a single place before a shared one."""
    first = label.split("/")
    return int(first[0]), len(first)


class Placements(Statistic):
    key = "placeringer"
    title = "Placeringer"
    topic = "placeringer"
    description = ("Placering på aftenen blandt de klubmedlemmer, der spillede, efter aftenens resultat. "
                   "Ved lige resultat deles pladserne, fx 1/2, som i det gamle regnskab. "
                   "Placeringsscoren er fair på tværs af aftener med forskelligt antal spillere.")

    def compute(self, data):
        counts = defaultdict(Counter)
        place_sum = defaultdict(Fraction)
        scores = defaultdict(list)
        for totals in night_totals(data).values():
            for mid, (label, place) in places(totals).items():
                counts[mid][label] += 1
                place_sum[mid] += place
                if (score := place_score(place, len(totals))) is not None:
                    scores[mid].append(score)

        labels = sorted({label for c in counts.values() for label in c}, key=label_order)
        rows = []
        for mid, c in counts.items():
            name = data.name_of(mid)
            nights = sum(c.values())
            rows.append(dict(name=Cell(name, name.casefold()), nights=nights,
                             score=sum(scores[mid], Fraction()) / len(scores[mid]) if scores[mid] else None,
                             average=place_sum[mid] / nights,
                             **{f"p{label}": c[label] for label in labels}))
        # Best score first; it is fair across table sizes. Average place, then name, break ties.
        rows.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0), r["average"], r["name"].sort))
        columns = [
            Column("name", "Spiller", "Klubmedlemmet.", "text"),
            Column("nights", "Aftener", "Spilaftener, medlemmet spillede."),
            Column("score", "Placeringsscore",
                   "Hver aften er en lille kamp mod hver anden spiller: foran = 1, bagved = 0, lige = ½. "
                   "Scoren er andelen af kampene, i gennemsnit over aftenerne: 100 % er alene først, 0 % alene sidst, "
                   "uanset hvor mange der spillede. To delt først af fem får (3 + ½) / 4 = 87,5 %, midt mellem 1. og 2. plads.",
                   "pct"),
            Column("average", "Snit plads", "Gennemsnitlig plads; en delt plads tæller som midten, fx 1/2 = 1,5. "
                   "Afhænger af, hvor mange der spillede.", "points"),
        ]
        columns += [Column(f"p{label}", f"Nr. {label}", f"Antal aftener som nr. {label}.") for label in labels]
        return Table(columns, rows, empty="Ingen spilaftener med klubmedlemmer i perioden.")
