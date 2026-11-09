"""Graphs for a table's columns: the same statistic computed for each period one level down.

The club's lifetime is graphed year by year, a year night by night; a period between two
dates by year when it spans several years, else night by night; a night has no graph.
Each row of the table (a member, a pair, a bid) is one line, followed through the periods
by the text of its first column. A statistic that does not make sense for the smaller
period (fx Aftener, which is itself night by night) gets no graphs.
"""

from dataclasses import replace
from fractions import Fraction

from .base import Cell

SUB_KIND = {"all": "year", "year": "night"}


def sub_kind(data):
    """The period one level down; a period between two dates goes by year when it spans
    several years, else night by night."""
    if data.scope.kind == "period":
        return "year" if len(data.years()) > 1 else "night"
    return SUB_KIND.get(data.scope.kind)


def periods(data):
    """(x label, tooltip title, StatsData) per period inside the scope, oldest first."""
    kind = sub_kind(data)
    if kind == "year":
        return [(year, year, replace(data.for_year(year), scope=replace(data.scope, kind="year", year=int(year))))
                for year in sorted(data.years())]
    if kind == "night":
        result = []
        for night in data.nights_with_members():
            _year, month, day = night.date.split("-")
            title = night.date if night.title in ("", night.date) else f"{night.date} · {night.title}"
            result.append((f"{int(day)}/{int(month)}", title, data.for_night(night.id)))
        return result
    return []


def number(value):
    """A graphable value as float, None for a blank, or False when the value is not a number."""
    if isinstance(value, Cell):
        value = value.sort
    if value is None:
        return None
    if isinstance(value, (int, float, Fraction)) and not isinstance(value, bool):
        return float(value)
    return False


def label(value):
    return value.text if isinstance(value, Cell) else str(value)


def trend(stat, table, data, sub_periods=None):
    """{x, titles, columns: {key: {label, fmt, series: [{name, values, total}]}}}, or None."""
    sub_periods = periods(data) if sub_periods is None else sub_periods
    if sub_kind(data) not in stat.scopes or len(sub_periods) < 2 or not table.columns:
        return None
    first = table.columns[0].key
    tables = [stat.compute(period) for _x, _title, period in sub_periods]

    # Lines in the table's own order, the totals row last; rows only seen in a period follow.
    names, totals = [], set()
    def note(name, total=False):
        if name not in names:
            names.append(name)
        if total:
            totals.add(name)
    for t in [table, *tables]:
        for row in t.rows:
            note(label(row[first]))
    for t in [table, *tables]:
        if t.footer:
            note(label(t.footer[first]), total=True)

    columns = {}
    for c in table.columns[1:]:
        if not c.graph:
            continue
        values = {name: [None] * len(tables) for name in names}
        graphable, seen = True, False
        for i, t in enumerate(tables):
            for row in [*t.rows, *([t.footer] if t.footer else [])]:
                if c.key not in row:
                    continue
                v = number(row[c.key])
                if v is False:
                    graphable = False
                    break
                if v is not None:
                    values[label(row[first])][i], seen = v, True
            if not graphable:
                break
        if graphable and seen:
            series = [dict(name=n, values=values[n], total=n in totals)
                      for n in names if any(v is not None for v in values[n])]
            columns[c.key] = dict(label=c.label, fmt=c.fmt, series=series)
    if not columns:
        return None
    return dict(x=[x for x, _t, _d in sub_periods], titles=[t for _x, t, _d in sub_periods],
                columns=columns, per="år" if sub_kind(data) == "year" else "aften")
