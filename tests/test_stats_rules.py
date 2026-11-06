"""Vis regel on the statistics pages: the code behind each statistic, for every member."""
from fastapi.testclient import TestClient

from whistapp.database import connect, init_db
from whistapp.main import app
from whistapp.stats.registry import TOPICS, rule_sources, statistic
from test_clubs import _activate, _new_login


def test_every_statistic_shows_its_own_code_and_helpers():
    for topic in TOPICS:
        for stat in topic.statistics:
            names = [name for name, _ in rule_sources(stat)]
            assert names[0] == type(stat).__module__.rsplit(".", 1)[-1] + ".py"
            assert f"class {type(stat).__name__}" in rule_sources(stat)[0][1]
            assert "base.py" not in names  # tables and formatting compute nothing
    # Placeringer uses the shared ranking and score, which are shown with it.
    assert [n for n, _ in rule_sources(statistic("placeringer"))] == ["placements.py", "night_results.py"]
    assert statistic("nonsense") is None


def test_rule_dialog_for_members():
    init_db()
    with TestClient(app) as admin:
        admin.post("/login", data={"username": "admin", "password": "a$$word"})
        page = admin.get("/statistics?topic=placeringer").text
        assert 'class="secondary stat-rule" data-key="placeringer"' in page and 'id="stat-rule-dialog"' in page
        member_id, temporary = _new_login(admin, "rulereader", "Regellæser")
    try:
        with TestClient(app) as member:
            _activate(member, "rulereader", temporary)
            rule = member.get("/statistics/rules/placeringer")
            assert rule.status_code == 200
            assert "Placeringer — beregningsregler" in rule.text and "def place_score" in rule.text
            assert member.get("/statistics/rules/nonsense").status_code == 404
    finally:
        with connect() as db:
            db.execute("UPDATE club_members SET status='paused' WHERE id=?", (member_id,))
