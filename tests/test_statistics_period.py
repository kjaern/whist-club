"""Statistics for a period between two dates, beside the lifetime, a year and a night."""
import pytest
from fastapi.testclient import TestClient

from whistapp.database import connect, init_db
from whistapp.hall_of_fame import compute
from whistapp.main import app
from whistapp.stats.data import Scope, load
from whistapp.stats.trends import periods, sub_kind
from test_hall_of_fame import night, ranked


@pytest.fixture
def db(tmp_path, monkeypatch):
    import whistapp.database as database
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "period.sqlite3")
    init_db()
    with connect() as db:
        yield db


def period(db, start=None, end=None, origin="all"):
    return load(db, Scope("period", date_from=start, date_to=end, origin=origin))


def test_both_dates_are_included_and_either_end_may_be_open(db):
    night(db, 1, "2025-12-01", ranked(1, 2, 3, 4))
    night(db, 2, "2026-01-10", ranked(2, 1, 3, 4))
    night(db, 3, "2026-03-01", ranked(3, 1, 2, 4), origin="imported")
    assert set(period(db, "2026-01-10", "2026-03-01").nights) == {2, 3}
    assert set(period(db, "2026-01-11", "2026-02-28").nights) == set()
    assert set(period(db, "2026-01-01").nights) == {2, 3}
    assert set(period(db, end="2026-01-10").nights) == {1, 2}
    assert set(period(db, "2026-01-01", origin="live").nights) == {2}
    data = period(db, "2026-01-01", "2026-01-31")
    assert {h.session_id for h in data.hands} == {2}
    assert {p.session_id for p in data.players.values()} == {2}


def test_a_night_counts_on_its_danish_date(db):
    # 23:30 UTC on New Year's Eve is shortly after midnight in Denmark, and shown as 1 January.
    db.execute("INSERT INTO sessions(id,title,created_at,ended_at,is_club,club_id) "
               "VALUES(1,'Nytår','2025-12-31 23:30:00','2026-01-01 03:00:00',1,1)")
    assert set(period(db, "2026-01-01", "2026-01-01").nights) == {1}
    assert set(period(db, "2025-12-31", "2025-12-31").nights) == set()


def test_graphs_go_by_year_when_the_period_spans_years(db):
    night(db, 1, "2025-06-01", ranked(1, 2, 3, 4))
    night(db, 2, "2026-02-01", ranked(2, 1, 3, 4))
    night(db, 3, "2026-03-01", ranked(3, 1, 2, 4))
    spanning = period(db, "2025-01-01", "2026-12-31")
    assert sub_kind(spanning) == "year" and [x for x, _t, _d in periods(spanning)] == ["2025", "2026"]
    one_year = period(db, "2026-01-01", "2026-12-31")
    assert sub_kind(one_year) == "night" and [x for x, _t, _d in periods(one_year)] == ["1/2", "1/3"]


def test_hall_of_fame_names_the_period(db):
    night(db, 1, "2025-06-01", ranked(1, 2, 3, 4))
    night(db, 2, "2026-02-01", ranked(2, 1, 3, 4))
    hof = compute(period(db, "2026-01-01", "2026-06-30"))
    assert hof.nights == 1 and hof.period == "1/1/2026 – 30/6/2026"
    assert hof.award("konge").title == "Periodens konge"
    assert [h.name for h in hof.award("sejre").holders] == ["Bo"]


def test_the_statistics_page_shows_a_period():
    members = (9101, 9102)
    with TestClient(app) as client:  # starting the app creates the shared test database
        with connect() as db:
            night(db, 9101, "2041-03-01", ranked(9101, 9102), members=members)
            night(db, 9102, "2041-05-01", ranked(9102, 9101), members=members)
            db.execute("UPDATE club_members SET status='left' WHERE id IN (9101,9102)")
        try:
            client.post("/login", data={"username": "admin", "password": "a$$word"})
            page = client.get("/statistics?fra=2041-04-30&til=2041-02-01")  # swapped dates are fine
            assert page.status_code == 200
            assert "Klubstatistik · 1/2/2041 – 30/4/2041" in page.text
            assert "Spilaftener i perioden" in page.text and "Aften 9101" in page.text
            assert "Aften 9102" not in page.text
            assert 'value="2041-02-01"' in page.text and 'value="2041-04-30"' in page.text
            assert "fra=2041-02-01&amp;til=2041-04-30&amp;topic=hall-of-fame" in page.text
            hof = client.get("/statistics?fra=2041-02-01&til=2041-04-30&topic=hall-of-fame").text
            assert "Bygger på 1 afsluttede klubaftener i perioden 1/2/2041 – 30/4/2041" in hof
            assert client.get("/statistics?fra=2041-13-01").status_code == 400
            everything = client.get("/statistics?fra=&til=").text  # no dates: the club's lifetime
            assert "<h1>\n    Klubstatistik\n  </h1>" in everything
            year = client.get("/statistics/2041").text
            assert 'value="2041-01-01"' in year and 'action="/statistics" class="stats-compact stats-period"' in year
            live = client.get("/statistics?fra=2041-02-01&til=2041-04-30&origin=live").text
            assert '<input type="hidden" name="fra" value="2041-02-01">' in live  # the origin choice keeps the period
        finally:
            with connect() as db:
                db.execute("DELETE FROM sessions WHERE id IN (9101,9102)")
