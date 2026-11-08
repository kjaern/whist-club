"""One-off migrations run only by hand, refuse unsafe states, and back up first."""
import pytest

import whistapp.database as database
from whistapp import migrations
from whistapp.database import connect, init_db
from whistapp.rule_versions import stamp_night

OLD_COLUMNS = ("placement_rates", "fejl40_rate", "money_mode", "point_rate", "bid_settings")


@pytest.fixture
def old_db(tmp_path, monkeypatch):
    """A database that still has the per-night rule columns, as one from before rule versions."""
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "data" / "whist.sqlite3")
    init_db()
    with connect() as db:
        for column in OLD_COLUMNS:
            db.execute(f"ALTER TABLE sessions ADD COLUMN {column} TEXT")
        sid = db.execute("INSERT INTO sessions(title,club_id,placement_rates) VALUES ('Gammel',1,'[1]')").lastrowid
    return tmp_path / "data", sid


def _columns():
    with connect() as db:
        return {r[1] for r in db.execute("PRAGMA table_info(sessions)")}


def test_starting_the_app_never_runs_a_migration(old_db):
    init_db()
    assert set(OLD_COLUMNS) <= _columns()


def test_drop_night_rule_columns_waits_until_every_night_has_a_version(old_db):
    folder, sid = old_db
    assert "drop-night-rule-columns: BLOCKED: nights without a rule version: " + str(sid) in migrations.status()
    with pytest.raises(SystemExit, match="not run"):
        migrations.run("drop-night-rule-columns")
    assert set(OLD_COLUMNS) <= _columns()

    with connect() as db:
        stamp_night(db, sid)
    result = migrations.run("drop-night-rule-columns")
    assert "removed sessions.placement_rates" in result
    assert not set(OLD_COLUMNS) & _columns()
    backups = list(folder.glob("whist-before-drop-night-rule-columns-*.sqlite3"))
    assert len(backups) == 1  # the database as it was, next to it
    assert "drop-night-rule-columns: done" in migrations.status()
    assert "already done" in migrations.run("drop-night-rule-columns")


def test_unknown_migration_is_refused(old_db):
    with pytest.raises(SystemExit, match="Unknown migration"):
        migrations.run("nonsense")
