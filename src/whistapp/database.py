import os
import sqlite3
from uuid import uuid4
from contextvars import ContextVar

_audit_actor = ContextVar("whist_audit_actor", default=("system", None))

_audit_operation = ContextVar("whist_audit_operation", default=None)

def audit_operation_set():
    return _audit_operation.set(uuid4().hex)

def audit_operation_reset(token):
    _audit_operation.reset(token)

def audit_actor_set(username, account_id=None):
    return _audit_actor.set((username, account_id))

def audit_actor_reset(token):
    _audit_actor.reset(token)

from pathlib import Path

from .club import initial_club_name, initial_finance, initial_roster
from .rule_versions import migrate as migrate_rule_versions
from .settings import DATA

DB_PATH = Path(os.environ.get("WHIST_DB", str(DATA / "whist.sqlite3")))


def db_path():
    return DB_PATH


def connect():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.create_function("audit_username", 0, lambda: _audit_actor.get()[0])
    db.create_function("audit_account_id", 0, lambda: _audit_actor.get()[1])
    db.create_function("audit_operation_id", 0, lambda: _audit_operation.get())
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA busy_timeout = 10000")
    return db


def init_db():
    with connect() as db:
        db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            owner_subject TEXT
        );
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS hands (
            id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            bid_key TEXT NOT NULL, bid_label TEXT NOT NULL, target INTEGER NOT NULL,
            actual INTEGER, won INTEGER, contract_points INTEGER,
            rules_snapshot TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS hand_declarers (
            hand_id INTEGER NOT NULL REFERENCES hands(id) ON DELETE CASCADE,
            player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
            PRIMARY KEY(hand_id, player_id)
        );
        CREATE TABLE IF NOT EXISTS hand_scores (
            hand_id INTEGER NOT NULL REFERENCES hands(id) ON DELETE CASCADE,
            player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
            numerator INTEGER NOT NULL, denominator INTEGER NOT NULL,
            PRIMARY KEY(hand_id, player_id)
        );
        """)

        db.execute("""CREATE TABLE IF NOT EXISTS session_photos (
            id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            stored_name TEXT NOT NULL, original_name TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS session_source_files (
            id INTEGER PRIMARY KEY,
            session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            stored_name TEXT NOT NULL,
            original_name TEXT NOT NULL,
            kind TEXT NOT NULL,
            media_type TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS evening_log (
            id INTEGER PRIMARY KEY,
            evening_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            user_id TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            description TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS historical_final_scores (
            session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
            numerator INTEGER NOT NULL, denominator INTEGER NOT NULL,
            PRIMARY KEY(session_id,player_id)
        )""")

        db.execute("""CREATE TABLE IF NOT EXISTS penalty_payments (
            id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            player_id INTEGER NOT NULL REFERENCES players(id), amount INTEGER NOT NULL,
            note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")

        db.execute("""CREATE TABLE IF NOT EXISTS session_settlements (
            session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            player_id INTEGER NOT NULL REFERENCES players(id), place INTEGER NOT NULL,
            placement_amount INTEGER NOT NULL, PRIMARY KEY(session_id, player_id)
        )""")

        # Aliases are unique within a club; older databases are rebuilt below.
        db.execute("""CREATE TABLE IF NOT EXISTS club_member_aliases (
            member_id INTEGER NOT NULL REFERENCES club_members(id) ON DELETE CASCADE,
            club_id INTEGER NOT NULL,
            alias TEXT NOT NULL COLLATE NOCASE,
            PRIMARY KEY(member_id, alias),
            UNIQUE(club_id, alias)
        )""")

        scols = {r[1] for r in db.execute("PRAGMA table_info(sessions)")}
        if "ended_at" not in scols:
            db.execute("ALTER TABLE sessions ADD COLUMN ended_at TEXT")
        if "origin" not in scols:
            db.execute(
                "ALTER TABLE sessions ADD COLUMN origin TEXT NOT NULL DEFAULT 'live' CHECK(origin IN ('live','imported'))"
            )

        # Snapshot club rates at night creation; existing closed settlements stay authoritative.
        scols = {r[1] for r in db.execute("PRAGMA table_info(sessions)")}
        if "placement_rates" not in scols:
            db.execute("ALTER TABLE sessions ADD COLUMN placement_rates TEXT")
        if "fejl40_rate" not in scols:
            db.execute("ALTER TABLE sessions ADD COLUMN fejl40_rate INTEGER")

        # Additive migration of databases from earlier releases.
        hand_cols = {r[1] for r in db.execute("PRAGMA table_info(hands)")}
        if "partner_id" not in hand_cols:
            db.execute("ALTER TABLE hands ADD COLUMN partner_id INTEGER")
        if "origin_client_id" not in hand_cols:
            db.execute("ALTER TABLE hands ADD COLUMN origin_client_id TEXT")
        # The dealer and the round are decided when a live hand is saved; older and imported
        # hands stay NULL. first_player_id was the first name of dealer_id (7 Nov 2026).
        if "first_player_id" in hand_cols and "dealer_id" not in hand_cols:
            db.execute("ALTER TABLE hands RENAME COLUMN first_player_id TO dealer_id")
        elif "dealer_id" not in hand_cols:
            db.execute("ALTER TABLE hands ADD COLUMN dealer_id INTEGER")
        if "round_no" not in hand_cols:
            db.execute("ALTER TABLE hands ADD COLUMN round_no INTEGER")
        if "round_end" not in hand_cols:
            db.execute("ALTER TABLE hands ADD COLUMN round_end INTEGER")

        db.execute("""CREATE TABLE IF NOT EXISTS special_bids (
            hand_id INTEGER NOT NULL REFERENCES hands(id) ON DELETE CASCADE,
            player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
            actual INTEGER, won INTEGER,
            PRIMARY KEY(hand_id, player_id))""")

        session_cols = {r[1] for r in db.execute("PRAGMA table_info(sessions)")}
        if "is_club" not in session_cols:
            db.execute(
                "ALTER TABLE sessions ADD COLUMN is_club INTEGER NOT NULL DEFAULT 0"
            )
        if "rotation_mode" not in session_cols:
            db.execute(
                "ALTER TABLE sessions ADD COLUMN rotation_mode TEXT NOT NULL DEFAULT 'consecutive'"
            )

        # Additive migration of databases from the original release.
        cols = {r[1] for r in db.execute("PRAGMA table_info(players)")}
        if "active" not in cols:
            db.execute(
                "ALTER TABLE players ADD COLUMN active INTEGER NOT NULL DEFAULT 1"
            )

        db.execute("""CREATE TABLE IF NOT EXISTS hand_participants (
            hand_id INTEGER NOT NULL REFERENCES hands(id) ON DELETE CASCADE,
            player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
            PRIMARY KEY(hand_id,player_id))""")

        player_cols = {r[1] for r in db.execute("PRAGMA table_info(players)")}
        if "club_id" not in player_cols:
            db.execute("ALTER TABLE players ADD COLUMN club_id TEXT")
        if "seat_order" not in player_cols:
            db.execute("ALTER TABLE players ADD COLUMN seat_order INTEGER")

        # Preserve old session order and existing scores. Do not guess club identity
        # from a matching guest name; old players remain unlinked until explicitly migrated.
        for session in db.execute("SELECT id FROM sessions"):
            rows = db.execute(
                "SELECT id FROM players WHERE session_id=? ORDER BY COALESCE(seat_order,id),id",
                (session[0],),
            ).fetchall()
            for position, player in enumerate(rows):
                db.execute(
                    "UPDATE players SET seat_order=? WHERE id=? AND seat_order IS NULL",
                    (position, player[0]),
                )

        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_session_club_id ON players(session_id,club_id) WHERE club_id IS NOT NULL"
        )

        # Global login identities are separate from scoring-only club members.
        db.execute("""CREATE TABLE IF NOT EXISTS user_accounts (
            id INTEGER PRIMARY KEY,
            username TEXT NOT NULL COLLATE NOCASE UNIQUE,
            password_hash TEXT NOT NULL,
            must_change_password INTEGER NOT NULL DEFAULT 1,
            session_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS member_accounts (
            member_id INTEGER PRIMARY KEY REFERENCES club_members(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES user_accounts(id) ON DELETE CASCADE
        )""")

        # Permanent identities, independent of attendance at individual nights.
        db.execute("""CREATE TABLE IF NOT EXISTS club_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            legacy_id TEXT UNIQUE,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active','paused','left')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")

        # Single-club role; membership exists independently of account activation.
        role_columns = {r[1] for r in db.execute("PRAGMA table_info(club_members)")}
        if "is_club_admin" not in role_columns:
            db.execute("ALTER TABLE club_members ADD COLUMN is_club_admin INTEGER NOT NULL DEFAULT 0")

        # Club 1 is the original club; existing members, nights and settings belong to it.
        db.execute("""CREATE TABLE IF NOT EXISTS clubs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_by_user_id INTEGER REFERENCES user_accounts(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        db.execute("INSERT OR IGNORE INTO clubs(id,name) VALUES (1,?)", (initial_club_name(),))
        # Names identify clubs in the picker. The app already refuses duplicates; the index
        # backs that up, and is skipped rather than failing startup if old data has duplicates.
        if not db.execute("SELECT 1 FROM clubs GROUP BY name COLLATE NOCASE HAVING COUNT(*)>1").fetchone():
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_clubs_name ON clubs(name COLLATE NOCASE)")
        if "club_id" not in role_columns:
            db.execute("ALTER TABLE club_members ADD COLUMN club_id INTEGER NOT NULL DEFAULT 1")
        db.execute("CREATE INDEX IF NOT EXISTS idx_club_members_club ON club_members(club_id,id)")

        # One settings row per club, keyed by the club id.
        db.execute("""CREATE TABLE IF NOT EXISTS club_settings (
            id INTEGER PRIMARY KEY REFERENCES clubs(id) ON DELETE CASCADE, placements TEXT NOT NULL,
            fejl40 INTEGER NOT NULL, allow_ai_import INTEGER NOT NULL DEFAULT 0
        )""")

        settings_columns = {r[1] for r in db.execute("PRAGMA table_info(club_settings)")}
        if "allow_ai_import" not in settings_columns:
            db.execute("ALTER TABLE club_settings ADD COLUMN allow_ai_import INTEGER NOT NULL DEFAULT 0")

        # Explicit migration: the single-club table allowed only id=1. Rebuild, keeping the row.
        settings_sql = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='club_settings'").fetchone()[0]
        if "CHECK(id=1)" in settings_sql.replace(" ", ""):
            db.execute("""CREATE TABLE club_settings_multi (
                id INTEGER PRIMARY KEY REFERENCES clubs(id) ON DELETE CASCADE, placements TEXT NOT NULL,
                fejl40 INTEGER NOT NULL, allow_ai_import INTEGER NOT NULL DEFAULT 0
            )""")
            db.execute("""INSERT INTO club_settings_multi(id,placements,fejl40,allow_ai_import)
                SELECT id,placements,fejl40,allow_ai_import FROM club_settings""")
            db.execute("DROP TABLE club_settings")
            db.execute("ALTER TABLE club_settings_multi RENAME TO club_settings")

        # Money mode and bid values per club; an empty bid_settings means the original house rules.
        settings_columns = {r[1] for r in db.execute("PRAGMA table_info(club_settings)")}
        if "money_mode" not in settings_columns:
            db.execute("ALTER TABLE club_settings ADD COLUMN money_mode TEXT NOT NULL DEFAULT 'placement'")
        if "point_rate" not in settings_columns:
            db.execute("ALTER TABLE club_settings ADD COLUMN point_rate TEXT NOT NULL DEFAULT '1'")
        if "bid_settings" not in settings_columns:
            db.execute("ALTER TABLE club_settings ADD COLUMN bid_settings TEXT NOT NULL DEFAULT '{}'")
        # Club-specific context for AI photo import, kept in the database rather than the public prompt.
        if "ai_notes" not in settings_columns:
            db.execute("ALTER TABLE club_settings ADD COLUMN ai_notes TEXT NOT NULL DEFAULT ''")

        # Explicit migration: aliases used to be unique across the server; now per club.
        alias_columns = {r[1] for r in db.execute("PRAGMA table_info(club_member_aliases)")}
        if "club_id" not in alias_columns:
            db.execute("""CREATE TABLE club_member_aliases_multi (
                member_id INTEGER NOT NULL REFERENCES club_members(id) ON DELETE CASCADE,
                club_id INTEGER NOT NULL,
                alias TEXT NOT NULL COLLATE NOCASE,
                PRIMARY KEY(member_id, alias),
                UNIQUE(club_id, alias)
            )""")
            db.execute("""INSERT INTO club_member_aliases_multi(member_id,club_id,alias)
                SELECT a.member_id,m.club_id,a.alias FROM club_member_aliases a
                JOIN club_members m ON m.id=a.member_id""")
            db.execute("DROP TABLE club_member_aliases")
            db.execute("ALTER TABLE club_member_aliases_multi RENAME TO club_member_aliases")

        db.execute("""CREATE TABLE IF NOT EXISTS club_member_settlements (
            session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            member_id INTEGER NOT NULL REFERENCES club_members(id),
            place INTEGER NOT NULL,
            amount_numerator INTEGER NOT NULL,
            amount_denominator INTEGER NOT NULL,
            PRIMARY KEY(session_id,member_id)
        )""")

        scols = {r[1] for r in db.execute("PRAGMA table_info(sessions)")}
        if "club_id" not in scols:
            db.execute("ALTER TABLE sessions ADD COLUMN club_id INTEGER NOT NULL DEFAULT 1")
        # Snapshot of the club's money mode and bid values when a night starts; NULL means
        # the original placement payment and house rules, so older nights are unchanged.
        for column in ("money_mode", "point_rate", "bid_settings"):
            if column not in scols:
                db.execute(f"ALTER TABLE sessions ADD COLUMN {column} TEXT")
        db.execute("CREATE INDEX IF NOT EXISTS idx_sessions_club ON sessions(club_id,id)")
        if "date_precision" not in scols:
            db.execute(
                "ALTER TABLE sessions ADD COLUMN date_precision TEXT NOT NULL DEFAULT 'minute'"
            )

        # Server build that created the night; NULL for nights created before this was
        # recorded (fill those explicitly with `whistapp stamp-night`).
        for column in ("server_version", "server_commit"):
            if column not in scols:
                db.execute(f"ALTER TABLE sessions ADD COLUMN {column} TEXT")

        if "collect_club_money" not in scols:
            db.execute("ALTER TABLE sessions ADD COLUMN collect_club_money INTEGER")
            db.execute(
                "UPDATE sessions SET collect_club_money=1 WHERE is_club=1 AND ended_at IS NOT NULL"
            )

        pcols = {r[1] for r in db.execute("PRAGMA table_info(players)")}
        if "member_id" not in pcols:
            db.execute(
                "ALTER TABLE players ADD COLUMN member_id INTEGER REFERENCES club_members(id)"
            )
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_session_member ON players(session_id,member_id) WHERE member_id IS NOT NULL"
        )

        # One-time import of the original stable club.ini identifiers.
        for legacy_id, name in initial_roster():
            db.execute(
                "INSERT OR IGNORE INTO club_members(legacy_id,name,status) VALUES (?,?,?)",
                (legacy_id, name, "active"),
            )

        rates, penalty = initial_finance()
        db.execute(
            "INSERT OR IGNORE INTO club_settings(id,placements,fejl40) VALUES (1,?,?)",
            (rates, penalty),
        )

        db.execute("""UPDATE players SET member_id=(SELECT id FROM club_members
            WHERE club_members.legacy_id=players.club_id)
            WHERE club_id IS NOT NULL AND member_id IS NULL""")

        # Audit is independent of sessions: deleting a night must not erase its history.
        db.execute("""CREATE TABLE IF NOT EXISTS audit_events (
            id INTEGER PRIMARY KEY,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            actor_username TEXT NOT NULL,
            actor_account_id INTEGER,
            session_id INTEGER,
            hand_id INTEGER,
            entity TEXT NOT NULL,
            action TEXT NOT NULL,
            before_json TEXT,
            after_json TEXT
        )""")
        if not any(row[1] == "operation_id" for row in db.execute("PRAGMA table_info(audit_events)")):
            db.execute("ALTER TABLE audit_events ADD COLUMN operation_id TEXT")
        # Recreate audit triggers to include the request-scoped operation identifier.
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'audit_%'").fetchall():
            db.execute('DROP TRIGGER "' + name + '"')
        db.execute("CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_events(session_id,id)")
        db.execute("""CREATE TRIGGER IF NOT EXISTS audit_hands_insert
            AFTER INSERT ON hands BEGIN
            INSERT INTO audit_events(actor_username,actor_account_id,session_id,hand_id,entity,action,before_json,after_json,operation_id)
            VALUES(audit_username(),audit_account_id(),NEW.session_id,NEW.id,'hands','insert',NULL,json_object('id', NEW.id, 'session_id', NEW.session_id, 'bid_key', NEW.bid_key, 'bid_label', NEW.bid_label, 'target', NEW.target, 'actual', NEW.actual, 'won', NEW.won, 'contract_points', NEW.contract_points, 'partner_id', NEW.partner_id, 'origin_client_id', NEW.origin_client_id),audit_operation_id());
            END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS audit_hands_update
            AFTER UPDATE ON hands BEGIN
            INSERT INTO audit_events(actor_username,actor_account_id,session_id,hand_id,entity,action,before_json,after_json,operation_id)
            VALUES(audit_username(),audit_account_id(),NEW.session_id,NEW.id,'hands','update',json_object('id', OLD.id, 'session_id', OLD.session_id, 'bid_key', OLD.bid_key, 'bid_label', OLD.bid_label, 'target', OLD.target, 'actual', OLD.actual, 'won', OLD.won, 'contract_points', OLD.contract_points, 'partner_id', OLD.partner_id, 'origin_client_id', OLD.origin_client_id),json_object('id', NEW.id, 'session_id', NEW.session_id, 'bid_key', NEW.bid_key, 'bid_label', NEW.bid_label, 'target', NEW.target, 'actual', NEW.actual, 'won', NEW.won, 'contract_points', NEW.contract_points, 'partner_id', NEW.partner_id, 'origin_client_id', NEW.origin_client_id),audit_operation_id());
            END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS audit_hands_delete
            AFTER DELETE ON hands BEGIN
            INSERT INTO audit_events(actor_username,actor_account_id,session_id,hand_id,entity,action,before_json,after_json,operation_id)
            VALUES(audit_username(),audit_account_id(),OLD.session_id,OLD.id,'hands','delete',json_object('id', OLD.id, 'session_id', OLD.session_id, 'bid_key', OLD.bid_key, 'bid_label', OLD.bid_label, 'target', OLD.target, 'actual', OLD.actual, 'won', OLD.won, 'contract_points', OLD.contract_points, 'partner_id', OLD.partner_id, 'origin_client_id', OLD.origin_client_id),NULL,audit_operation_id());
            END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS audit_hand_scores_insert
            AFTER INSERT ON hand_scores BEGIN
            INSERT INTO audit_events(actor_username,actor_account_id,session_id,hand_id,entity,action,before_json,after_json,operation_id)
            VALUES(audit_username(),audit_account_id(),(SELECT session_id FROM hands WHERE id=NEW.hand_id),NEW.hand_id,'hand_scores','insert',NULL,json_object('hand_id', NEW.hand_id, 'player_id', NEW.player_id, 'numerator', NEW.numerator, 'denominator', NEW.denominator),audit_operation_id());
            END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS audit_hand_scores_update
            AFTER UPDATE ON hand_scores BEGIN
            INSERT INTO audit_events(actor_username,actor_account_id,session_id,hand_id,entity,action,before_json,after_json,operation_id)
            VALUES(audit_username(),audit_account_id(),(SELECT session_id FROM hands WHERE id=NEW.hand_id),NEW.hand_id,'hand_scores','update',json_object('hand_id', OLD.hand_id, 'player_id', OLD.player_id, 'numerator', OLD.numerator, 'denominator', OLD.denominator),json_object('hand_id', NEW.hand_id, 'player_id', NEW.player_id, 'numerator', NEW.numerator, 'denominator', NEW.denominator),audit_operation_id());
            END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS audit_hand_scores_delete
            AFTER DELETE ON hand_scores BEGIN
            INSERT INTO audit_events(actor_username,actor_account_id,session_id,hand_id,entity,action,before_json,after_json,operation_id)
            VALUES(audit_username(),audit_account_id(),(SELECT session_id FROM hands WHERE id=OLD.hand_id),OLD.hand_id,'hand_scores','delete',json_object('hand_id', OLD.hand_id, 'player_id', OLD.player_id, 'numerator', OLD.numerator, 'denominator', OLD.denominator),NULL,audit_operation_id());
            END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS audit_hand_declarers_insert
            AFTER INSERT ON hand_declarers BEGIN
            INSERT INTO audit_events(actor_username,actor_account_id,session_id,hand_id,entity,action,before_json,after_json,operation_id)
            VALUES(audit_username(),audit_account_id(),(SELECT session_id FROM hands WHERE id=NEW.hand_id),NEW.hand_id,'hand_declarers','insert',NULL,json_object('hand_id', NEW.hand_id, 'player_id', NEW.player_id),audit_operation_id());
            END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS audit_hand_declarers_update
            AFTER UPDATE ON hand_declarers BEGIN
            INSERT INTO audit_events(actor_username,actor_account_id,session_id,hand_id,entity,action,before_json,after_json,operation_id)
            VALUES(audit_username(),audit_account_id(),(SELECT session_id FROM hands WHERE id=NEW.hand_id),NEW.hand_id,'hand_declarers','update',json_object('hand_id', OLD.hand_id, 'player_id', OLD.player_id),json_object('hand_id', NEW.hand_id, 'player_id', NEW.player_id),audit_operation_id());
            END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS audit_hand_declarers_delete
            AFTER DELETE ON hand_declarers BEGIN
            INSERT INTO audit_events(actor_username,actor_account_id,session_id,hand_id,entity,action,before_json,after_json,operation_id)
            VALUES(audit_username(),audit_account_id(),(SELECT session_id FROM hands WHERE id=OLD.hand_id),OLD.hand_id,'hand_declarers','delete',json_object('hand_id', OLD.hand_id, 'player_id', OLD.player_id),NULL,audit_operation_id());
            END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS audit_hand_participants_insert
            AFTER INSERT ON hand_participants BEGIN
            INSERT INTO audit_events(actor_username,actor_account_id,session_id,hand_id,entity,action,before_json,after_json,operation_id)
            VALUES(audit_username(),audit_account_id(),(SELECT session_id FROM hands WHERE id=NEW.hand_id),NEW.hand_id,'hand_participants','insert',NULL,json_object('hand_id', NEW.hand_id, 'player_id', NEW.player_id),audit_operation_id());
            END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS audit_hand_participants_update
            AFTER UPDATE ON hand_participants BEGIN
            INSERT INTO audit_events(actor_username,actor_account_id,session_id,hand_id,entity,action,before_json,after_json,operation_id)
            VALUES(audit_username(),audit_account_id(),(SELECT session_id FROM hands WHERE id=NEW.hand_id),NEW.hand_id,'hand_participants','update',json_object('hand_id', OLD.hand_id, 'player_id', OLD.player_id),json_object('hand_id', NEW.hand_id, 'player_id', NEW.player_id),audit_operation_id());
            END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS audit_hand_participants_delete
            AFTER DELETE ON hand_participants BEGIN
            INSERT INTO audit_events(actor_username,actor_account_id,session_id,hand_id,entity,action,before_json,after_json,operation_id)
            VALUES(audit_username(),audit_account_id(),(SELECT session_id FROM hands WHERE id=OLD.hand_id),OLD.hand_id,'hand_participants','delete',json_object('hand_id', OLD.hand_id, 'player_id', OLD.player_id),NULL,audit_operation_id());
            END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS audit_special_bids_insert
            AFTER INSERT ON special_bids BEGIN
            INSERT INTO audit_events(actor_username,actor_account_id,session_id,hand_id,entity,action,before_json,after_json,operation_id)
            VALUES(audit_username(),audit_account_id(),(SELECT session_id FROM hands WHERE id=NEW.hand_id),NEW.hand_id,'special_bids','insert',NULL,json_object('hand_id', NEW.hand_id, 'player_id', NEW.player_id, 'actual', NEW.actual, 'won', NEW.won),audit_operation_id());
            END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS audit_special_bids_update
            AFTER UPDATE ON special_bids BEGIN
            INSERT INTO audit_events(actor_username,actor_account_id,session_id,hand_id,entity,action,before_json,after_json,operation_id)
            VALUES(audit_username(),audit_account_id(),(SELECT session_id FROM hands WHERE id=NEW.hand_id),NEW.hand_id,'special_bids','update',json_object('hand_id', OLD.hand_id, 'player_id', OLD.player_id, 'actual', OLD.actual, 'won', OLD.won),json_object('hand_id', NEW.hand_id, 'player_id', NEW.player_id, 'actual', NEW.actual, 'won', NEW.won),audit_operation_id());
            END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS audit_special_bids_delete
            AFTER DELETE ON special_bids BEGIN
            INSERT INTO audit_events(actor_username,actor_account_id,session_id,hand_id,entity,action,before_json,after_json,operation_id)
            VALUES(audit_username(),audit_account_id(),(SELECT session_id FROM hands WHERE id=OLD.hand_id),OLD.hand_id,'special_bids','delete',json_object('hand_id', OLD.hand_id, 'player_id', OLD.player_id, 'actual', OLD.actual, 'won', OLD.won),NULL,audit_operation_id());
            END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS audit_sessions_insert
            AFTER INSERT ON sessions BEGIN
            INSERT INTO audit_events(actor_username,actor_account_id,session_id,hand_id,entity,action,before_json,after_json,operation_id)
            VALUES(audit_username(),audit_account_id(),NEW.id,NULL,'sessions','insert',NULL,json_object('id', NEW.id, 'title', NEW.title, 'ended_at', NEW.ended_at, 'origin', NEW.origin),audit_operation_id());
            END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS audit_sessions_update
            AFTER UPDATE ON sessions BEGIN
            INSERT INTO audit_events(actor_username,actor_account_id,session_id,hand_id,entity,action,before_json,after_json,operation_id)
            VALUES(audit_username(),audit_account_id(),NEW.id,NULL,'sessions','update',json_object('id', OLD.id, 'title', OLD.title, 'ended_at', OLD.ended_at, 'origin', OLD.origin),json_object('id', NEW.id, 'title', NEW.title, 'ended_at', NEW.ended_at, 'origin', NEW.origin),audit_operation_id());
            END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS audit_sessions_delete
            AFTER DELETE ON sessions BEGIN
            INSERT INTO audit_events(actor_username,actor_account_id,session_id,hand_id,entity,action,before_json,after_json,operation_id)
            VALUES(audit_username(),audit_account_id(),OLD.id,NULL,'sessions','delete',json_object('id', OLD.id, 'title', OLD.title, 'ended_at', OLD.ended_at, 'origin', OLD.origin),NULL,audit_operation_id());
            END""")

        # What a night charges each member is money: every change to it is audited.
        for action, row in (("insert", "NEW"), ("delete", "OLD")):
            data = (f"json_object('member_id', {row}.member_id, 'place', {row}.place, "
                    f"'amount_numerator', {row}.amount_numerator, 'amount_denominator', {row}.amount_denominator)")
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS audit_club_member_settlements_{action}
                AFTER {action.upper()} ON club_member_settlements BEGIN
                INSERT INTO audit_events(actor_username,actor_account_id,session_id,hand_id,entity,action,before_json,after_json,operation_id)
                VALUES(audit_username(),audit_account_id(),{row}.session_id,NULL,'club_member_settlements','{action}',
                {"NULL" if action == "insert" else data},{data if action == "insert" else "NULL"},audit_operation_id());
                END""")

        # Versioned club rules: each night points to the rules and medlemsliste it was
        # played under (see rule_versions.py). The older per-night snapshot columns stay.
        db.execute("""CREATE TABLE IF NOT EXISTS club_rule_versions (
            id INTEGER PRIMARY KEY,
            club_id INTEGER NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
            number INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_by TEXT NOT NULL,
            placements TEXT NOT NULL,
            fejl40 INTEGER NOT NULL,
            money_mode TEXT NOT NULL,
            point_rate TEXT NOT NULL,
            bid_settings TEXT NOT NULL,
            member_ids TEXT NOT NULL,
            UNIQUE(club_id, number)
        )""")
        if "rule_version_id" not in {r[1] for r in db.execute("PRAGMA table_info(sessions)")}:
            db.execute("ALTER TABLE sessions ADD COLUMN rule_version_id INTEGER REFERENCES club_rule_versions(id)")
        migrate_rule_versions(db)
