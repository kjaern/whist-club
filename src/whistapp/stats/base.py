"""Shared building blocks: a statistic computes a Table that one template renders."""

from dataclasses import dataclass, field
from fractions import Fraction

# Bids that never have a partner; their outcomes are recorded per bidder.
PARTNERLESS = {"sol", "ren_sol", "bordlaegger", "paskrig"}
# Bids with a fixed trick limit where every bidder makes their own attempt.
SPECIAL = {"sol", "ren_sol", "bordlaegger"}


def points_text(value):
    """Points or kroner as the club writes them: 12,5 / -3 / 0."""
    return f"{float(value):.2f}".rstrip("0").rstrip(".").replace(".", ",")


def pct_text(value):
    return f"{float(100 * value):.1f}".replace(".", ",") + " %"


def ratio(part, whole):
    return Fraction(part, whole) if whole else None


@dataclass
class Cell:
    """A cell whose shown text differs from how it sorts."""
    text: str
    sort: object = None


@dataclass
class Column:
    key: str
    label: str
    description: str = ""
    fmt: str = "int"  # int | points | pct | signed | text


@dataclass
class Table:
    columns: list
    rows: list  # dicts keyed by column key
    footer: dict | None = None  # a totals row that stays put when sorting
    empty: str = "Ingen data i perioden."


def cell(value, fmt):
    """(text, sort key) for one value; None shows as a dash and sorts last."""
    if isinstance(value, Cell):
        return value.text, value.sort if value.sort is not None else value.text
    if value is None:
        return "—", ""
    if fmt == "points":
        return points_text(value), float(value)
    if fmt == "pct":
        return pct_text(value), float(value)
    if fmt == "signed":
        return f"{value:+d}", value
    if fmt == "int":
        return str(value), value
    return str(value), str(value).casefold()


class Statistic:
    key = ""
    title = ""
    topic = ""
    description = ""
    scopes = ("all", "year", "period", "night")

    def compute(self, data):
        raise NotImplementedError


@dataclass
class Topic:
    """A tab: registered statistics shown as tables, or a page of its own.

    A page topic has no statistics; `build(data)` computes what its template `page` shows,
    on the scope kinds in `scopes`.
    """
    key: str
    label: str
    statistics: list = field(default_factory=list)
    scopes: tuple = ()
    page: str = ""
    build: object = None
