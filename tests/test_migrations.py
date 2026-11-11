"""One-off migrations run only by hand, refuse unsafe states, and back up first."""
import pytest

import whistapp.database as database
from whistapp import backups, migrations
from whistapp.database import connect, init_db
from whistapp.rule_versions import stamp_night

OLD_COLUMNS = ("placement_rates", "fejl40_rate", "money_mode", "point_rate", "bid_settings")


@pytest.fixture
def old_db(tmp_path, monkeypatch):
    """A database that still has the per-night rule columns, as one from before rule versions."""
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "data" / "whist.sqlite3")
    monkeypatch.setenv("WHIST_BACKUP_DIR", str(tmp_path / "backups"))
    init_db()
    with connect() as db:
        for column in OLD_COLUMNS:
            db.execute(f"ALTER TABLE sessions ADD COLUMN {column} TEXT")
        sid = db.execute("INSERT INTO sessions(title,club_id,placement_rates) VALUES ('Gammel',1,'[1]')").lastrowid
    return tmp_path / "backups", sid


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
    made = list(folder.glob("whist-migration-*.sqlite3"))
    assert len(made) == 1  # the database as it was, in the backup folder
    assert str(made[0]) in result
    assert "drop-night-rule-columns: done" in migrations.status()
    assert "already done" in migrations.run("drop-night-rule-columns")


def test_unknown_migration_is_refused(old_db):
    with pytest.raises(SystemExit, match="Unknown migration"):
        migrations.run("nonsense")


def test_migration_backup_is_listed_with_the_other_backups(old_db):
    folder, sid = old_db
    with connect() as db:
        stamp_night(db, sid)
    migrations.run("drop-night-rule-columns")
    (row,) = backups.list_backups()
    assert row["kind"] == "Før migrering" and row["name"].startswith("whist-migration-")


def test_migration_is_not_run_when_the_backup_fails(old_db):
    folder, sid = old_db
    with connect() as db:
        stamp_night(db, sid)
    folder.write_bytes(b"not a folder")  # the backup folder cannot be made
    with pytest.raises(SystemExit, match="not run, the backup failed"):
        migrations.run("drop-night-rule-columns")
    assert set(OLD_COLUMNS) <= _columns()


def test_migration_leaves_a_half_written_server_backup_alone(old_db):
    folder, sid = old_db
    with connect() as db:
        stamp_night(db, sid)
    folder.mkdir()
    writing = folder / f"{backups.PARTIAL_PREFIX}whist-monthly-2026-11.sqlite3"
    writing.write_bytes(b"the server is copying")  # `whistapp migrate` runs next to the server
    migrations.run("drop-night-rule-columns")
    assert writing.exists()
