from whistapp.club import club_players
from whistapp.database import init_db, connect
import whistapp.database as database


def test_club_ids_are_stable_and_unique(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'test.db')
    init_db()
    names = ['Anne', 'Bo', 'Clara', 'Dan', 'Eva', 'Finn']
    with connect() as db:
        db.executemany('INSERT INTO club_members(name) VALUES (?)', [(name,) for name in names])
        roster = club_players(db)
    assert [name for _, name in roster] == names
    assert len({pid for pid, _ in roster}) == len(roster)


def test_additive_migration_keeps_existing_player_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'test.db')
    init_db()
    with connect() as db:
        sid = db.execute("INSERT INTO sessions(title) VALUES ('Old')").lastrowid
        pid = db.execute('INSERT INTO players(session_id,name) VALUES (?,?)', (sid, 'Anne')).lastrowid
    init_db()
    with connect() as db:
        p = db.execute('SELECT id,club_id,seat_order FROM players WHERE id=?', (pid,)).fetchone()
        assert p['id'] == pid
        assert p['club_id'] is None  # Never match guests by display name
        assert p['seat_order'] == 0
