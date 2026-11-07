"""Graphs per column on /statistics (per year) and /statistics/{year} (per night), and the test club."""
import json
import re
from collections import Counter
from datetime import date
from fractions import Fraction

from fastapi.testclient import TestClient

from whistapp.database import connect
from whistapp.main import app
from whistapp.stats.base import Cell, Column, Statistic, Table
from whistapp.stats.data import NightRecord, Scope, StatsData
from test_clubs import _activate, _new_login, cleanup_clubs  # noqa: F401


class Sample(Statistic):
    key = "sample"
    scopes = ("all", "year", "night")

    def compute(self, data):
        rows = [dict(name=Cell("A", "a"), n=len(data.nights),
                     text="x", pct=Fraction(1, 2))]
        return Table([Column("name", "Spiller", fmt="text"), Column("n", "Antal"),
                      Column("text", "Tekst", fmt="text"), Column("pct", "Pct", fmt="pct")],
                     rows, footer=dict(name=Cell("I alt", 0), n=1, text="y", pct=None))


def _data(kind="all"):
    nights = {i: NightRecord(i, "", f"{2024 + i // 2}-0{i % 2 + 1}-01", "live", str(2024 + i // 2),
                             f"{2024 + i // 2}-0{i % 2 + 1}-01", True) for i in range(4)}
    return StatsData(Scope(kind, year=2024 if kind == "year" else None), [], nights, {}, {})


def test_trend_follows_rows_through_periods_and_skips_text_columns():
    from whistapp.stats.trends import periods, trend

    data = _data()
    stat = Sample()
    with_members = lambda self: list(self.nights.values())  # noqa: E731
    StatsData.nights_with_members, original = with_members, StatsData.nights_with_members
    try:
        result = trend(stat, stat.compute(data), data)
        assert result["x"] == ["2024", "2025"] and result["per"] == "år"
        assert set(result["columns"]) == {"n", "pct"}  # text columns are not graphed
        series = result["columns"]["n"]["series"]
        assert [(s["name"], s["values"], s["total"]) for s in series] == [("A", [2, 2], False), ("I alt", [1, 1], True)]
        assert [s["name"] for s in result["columns"]["pct"]["series"]] == ["A"]  # the blank total has no line

        year = data.for_year(2024)
        year = StatsData(Scope("year", year=2024), [], year.nights, {}, {})
        assert [x for x, _t, _d in periods(year)] == ["1/1", "1/2"]
        assert trend(stat, stat.compute(year), year)["per"] == "aften"
        night = year.for_night(0)
        assert night.scope.kind == "night" and list(night.nights) == [0]
        assert trend(stat, stat.compute(night), night) is None  # a single night has no graph
    finally:
        StatsData.nights_with_members = original


def test_test_club_has_closed_dummy_nights_and_graphs(cleanup_clubs):  # noqa: F811
    with TestClient(app) as admin:
        admin.post("/login", data={"username": "admin", "password": "a$$word"})
        _member, temporary = _new_login(admin, "tester", "Testeren")
        with TestClient(app) as tester:
            _activate(tester, "tester", temporary)
            assert "Opret testklub" in tester.get("/clubs").text
            created = tester.post("/clubs/new-test", follow_redirects=False)
            assert created.status_code == 303 and created.headers["location"] == "/statistics"
            with connect() as db:
                club = db.execute("SELECT id FROM clubs WHERE name='Testklub'").fetchone()["id"]
                nights = db.execute("SELECT * FROM sessions WHERE club_id=?", (club,)).fetchall()
                per_year = Counter(n["created_at"][:4] for n in nights)
                this_year = date.today().year
                assert sorted(per_year) == [str(y) for y in range(this_year - 4, this_year + 1)]
                assert all(n <= 15 for n in per_year.values())
                assert all(10 <= per_year[str(y)] for y in range(this_year - 4, this_year))
                assert all(n["ended_at"] and n["is_club"] for n in nights)
                assert all(n["created_at"][:10] <= date.today().isoformat() for n in nights)
                # Every hand is zero-sum, as scored by the bids, and every night is settled.
                sums = db.execute("""SELECT h.id FROM hands h JOIN sessions s ON s.id=h.session_id
                    WHERE s.club_id=?""", (club,)).fetchall()
                assert len(sums) > 10 * 5 * 15
                bad = [hid for (hid,) in sums if sum(
                    (Fraction(n, d) for n, d in db.execute(
                        "SELECT numerator,denominator FROM hand_scores WHERE hand_id=?", (hid,))), Fraction())]
                assert not bad
                settled = {r[0] for r in db.execute("""SELECT DISTINCT session_id FROM club_member_settlements
                    WHERE session_id IN (SELECT id FROM sessions WHERE club_id=?)""", (club,))}
                assert settled == {n["id"] for n in nights}
                other = db.execute("SELECT COUNT(*) FROM sessions WHERE club_id=1 AND title LIKE '____-__-__'").fetchone()[0]

            page = tester.get("/statistics")
            assert page.status_code == 200 and 'class="stat-graph"' in page.text
            trend = json.loads(re.search(r'id="trend-overblik">(.*?)</script>', page.text).group(1))
            assert trend["x"] == [str(y) for y in range(this_year - 4, this_year + 1)]
            assert "points" in trend["columns"] and "name" not in trend["columns"]

            year = tester.get(f"/statistics/{this_year - 1}?topic=placeringer")
            trend = json.loads(re.search(r'id="trend-placeringer">(.*?)</script>', year.text).group(1))
            assert trend["per"] == "aften" and len(trend["x"]) == per_year[str(this_year - 1)]
            assert 'id="trend-aftener"' not in year.text  # Aftener is already night by night

            night = tester.get(f"/sessions/{nights[0]['id']}/statistics")
            assert night.status_code == 200 and "stat-graph" not in night.text
        with connect() as db:
            assert db.execute("SELECT COUNT(*) FROM sessions WHERE club_id=1 AND title LIKE '____-__-__'").fetchone()[0] == other
