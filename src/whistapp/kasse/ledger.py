"""The kasse's books: entries, buckets, balances, the kasserer's log and alerts.

Nothing here knows what an entry kind does; each kind's file in this folder says that,
and balances() only adds the effects up. Entries are never edited or deleted: a mistake is
undone by a reversing entry, which is the same entry with the amount negated.

A member's saldo has two parts. Bogført: the entries plus what posted nights charged
(kasse_night_charges, written once by finalize_due and never changed). Afventer: what nights
that are not posted yet charge right now (aftener.pending_nights). I alt is their sum, so posting
a night moves its amount from Afventer to Bogført and leaves I alt as it was. Nights are money
members owe, not cash: posting one never changes the club's cash.
Every function takes the club id; the routes pass active_club_id().
"""

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from fractions import Fraction
from zoneinfo import ZoneInfo

from ..database import audit_actor
from . import aftener
from .base import UnknownEntryType
from .registry import get_type

logger = logging.getLogger(__name__)


def today():
    return datetime.now(ZoneInfo("Europe/Copenhagen")).date().isoformat()


def club(db, club_id):
    return db.execute("""SELECT id,name,kasserer_member_id,kasse_start,kasse_start_cash_numerator,
        kasse_start_cash_denominator,kasse_started_at FROM clubs WHERE id=?""", (club_id,)).fetchone()


def start_cash(row):
    """The club's cash on the kasse's start date (Startbeholdning)."""
    return Fraction(row["kasse_start_cash_numerator"] or 0, row["kasse_start_cash_denominator"] or 1)


def kasserer(db, club_id):
    """The club's kasserer, if one is assigned and still an active member of the club."""
    return db.execute("""SELECT cm.* FROM clubs c JOIN club_members cm ON cm.id=c.kasserer_member_id
        WHERE c.id=? AND cm.club_id=c.id AND cm.status='active'""", (club_id,)).fetchone()


def log(db, club_id, action, entity, entity_id=None, before=None, after=None, system=False):
    """One line in the kasserer's log. system: the kasse did it by itself (posting a night), not the viewer."""
    username, account_id = ("system", None) if system else audit_actor()
    db.execute("""INSERT INTO kasse_log(club_id,actor_username,actor_account_id,action,entity,entity_id,
        before_json,after_json) VALUES (?,?,?,?,?,?,?,?)""",
               (club_id, username, account_id, action, entity, entity_id,
                None if before is None else json.dumps(before), None if after is None else json.dumps(after)))


def log_rows(db, club_id, limit=200):
    return [dict(r) for r in db.execute(
        "SELECT * FROM kasse_log WHERE club_id=? ORDER BY id DESC LIMIT ?", (club_id, limit))]


def _kr(value):
    from ..web import fmt
    return f"{fmt(value)} kr"


def log_lines(db, club_id, limit=200):
    """The log as Danish sentences, newest first; every member reads it. Names are read live."""
    names = {r[0]: r[1] for r in db.execute("SELECT id,name FROM club_members WHERE club_id=?", (club_id,))}
    bucket_names = {r[0]: r[1] for r in db.execute("SELECT id,name FROM kasse_buckets WHERE club_id=?", (club_id,))}

    def bucket(bid):
        return bucket_names.get(bid, f"pulje #{bid}")

    lines = []
    for row in log_rows(db, club_id, limit):
        before = json.loads(row["before_json"]) if row["before_json"] else {}
        after = json.loads(row["after_json"]) if row["after_json"] else {}
        entity, action = row["entity"], row["action"]
        if entity == "entry":
            try:
                label = get_type(after.get("kind")).label
            except KeyError:
                label = after.get("kind", "postering")
            who = names.get(after.get("member_id")) if after.get("member_id") else (
                bucket(after["bucket_id"]) if after.get("bucket_id") else "Klubkassen")
            amount = Fraction(after.get("amount_numerator", 0), after.get("amount_denominator", 1))
            note = f" ({after['note']})" if after.get("note") else ""
            if action == "reverse":
                text = f"tilbageførte postering #{after.get('reverses_id')} med #{row['entity_id']}: {label}, {who}, {_kr(amount)}"
            else:
                text = f"bogførte {label} #{row['entity_id']}: {who}, {_kr(amount)}{note}"
        elif entity == "bucket":
            if action == "create":
                text = f"oprettede puljen {after.get('name')}"
            else:
                changes = [f"{label} {before.get(key) or '–'} → {after.get(key) or '–'}"
                           for key, label in (("name", "navn"), ("note", "note")) if before.get(key) != after.get(key)]
                text = f"ændrede puljen {after.get('name')}: " + ", ".join(changes)
        elif entity == "kasse_start":
            start = after.get("kasse_start") or "ingen (alle aftener tæller)"
            text = f"satte kassens start til {start}"
            if "start_cash" in after:
                text += f" med startbeholdning {_kr(Fraction(*after['start_cash']))}"
        elif entity == "night":
            total = sum((Fraction(n, d) for _, _, n, d in after.get("charges", [])), Fraction())
            when = ("ved overgangen til bogførte aftener" if after.get("source") == "historisk"
                    else f"{aftener.REOPEN_HOURS} timer efter, den sidst blev afsluttet")
            text = f"bogførte spilaftenen {after.get('title')} ({after.get('date')}): {_kr(total)} i alt, {when}"
        elif entity == "kasserer":
            text = f"valgte {names.get(after.get('member_id'), 'ingen')} som kasserer"
        elif entity == "alert":
            title = db.execute("SELECT session_title FROM kasse_alerts WHERE id=?", (row["entity_id"],)).fetchone()
            text = f"markerede ændringen af {title[0] if title else 'en spilaften'} som set"
        else:
            text = f"{action} {entity}"
        lines.append(dict(created_at=row["created_at"], actor=row["actor_username"], text=text))
    return lines


# Buckets -----------------------------------------------------------------

def buckets(db, club_id):
    """The club's buckets with what was spent from each. A trip's bucket gets a plain name
    (fx "Udlandstur 2027") and is renamed once the destination is known."""
    spent = {}
    for e in _entry_rows(db, club_id):
        for bid, amount in _effect(e).buckets.items():
            spent[bid] = spent.get(bid, Fraction()) + amount
    return [dict(b, spent=spent.get(b["id"], Fraction()))
            for b in db.execute("SELECT * FROM kasse_buckets WHERE club_id=? ORDER BY id", (club_id,))]


def _bucket_values(name, note):
    name, note = str(name or "").strip()[:80], str(note or "").strip()[:500]
    if not name:
        raise ValueError("Puljen skal have et navn")
    return dict(name=name, note=note)


def add_bucket(db, club_id, name, note=""):
    values = _bucket_values(name, note)
    bid = db.execute("INSERT INTO kasse_buckets(club_id,name,note) VALUES (?,?,?)",
                     (club_id, values["name"], values["note"])).lastrowid
    log(db, club_id, "create", "bucket", bid, None, values)
    return bid


def update_bucket(db, club_id, bucket_id, name, note=""):
    old = db.execute("SELECT name,note FROM kasse_buckets WHERE id=? AND club_id=?", (bucket_id, club_id)).fetchone()
    if not old:
        raise LookupError("Ukendt pulje")
    values = _bucket_values(name, note)
    if values != dict(old):
        db.execute("UPDATE kasse_buckets SET name=?,note=? WHERE id=?", (values["name"], values["note"], bucket_id))
        log(db, club_id, "update", "bucket", bucket_id, dict(old), values)


# Entries -----------------------------------------------------------------

def _entry_rows(db, club_id):
    return db.execute("SELECT * FROM kasse_entries WHERE club_id=? ORDER BY id", (club_id,)).fetchall()


def _amount(row):
    return Fraction(row["amount_numerator"], row["amount_denominator"])


def _effect(row):
    """What one entry does to the books. An entry whose kind no file defines raises UnknownEntryType:
    counting it as nothing would quietly change balances and the cash."""
    try:
        kind = get_type(row["kind"])
    except KeyError:
        raise UnknownEntryType(row["id"], row["kind"]) from None
    return kind.effect(_amount(row), row["member_id"], row["bucket_id"])


def entries(db, club_id):
    """The ledger, newest first, with current member and bucket names."""
    names = {r[0]: r[1] for r in db.execute("SELECT id,name FROM club_members WHERE club_id=?", (club_id,))}
    bucket_names = {r[0]: r[1] for r in db.execute("SELECT id,name FROM kasse_buckets WHERE club_id=?", (club_id,))}
    rows = _entry_rows(db, club_id)
    reversed_ids = {r["reverses_id"] for r in rows if r["reverses_id"]}
    result = []
    for r in reversed(rows):
        try:
            label = get_type(r["kind"]).label
        except KeyError:
            label = r["kind"]
        result.append(dict(r, amount=_amount(r), kind_label=label, member_name=names.get(r["member_id"]),
                           bucket_name=bucket_names.get(r["bucket_id"]), reversed=r["id"] in reversed_ids))
    return result


def _member_in_club(db, club_id, member_id):
    return db.execute("SELECT 1 FROM club_members WHERE id=? AND club_id=?", (member_id, club_id)).fetchone()


def add_entry(db, club_id, kind_key, amount, member_id=None, bucket_id=None, entry_date="", note=""):
    """Validate and write one entry; returns its id. Raises ValueError with a Danish message."""
    try:
        kind = get_type(kind_key)
    except KeyError:
        raise ValueError("Ukendt posteringstype")
    note = str(note or "").strip()[:500]
    if member_id is not None and not _member_in_club(db, club_id, member_id):
        raise ValueError("Ukendt medlem")
    if bucket_id is not None and not db.execute(
            "SELECT 1 FROM kasse_buckets WHERE id=? AND club_id=?", (bucket_id, club_id)).fetchone():
        raise ValueError("Ukendt pulje")
    entry_date = str(entry_date or "").strip() or today()
    try:
        datetime.strptime(entry_date, "%Y-%m-%d")
    except ValueError:
        raise ValueError("Ugyldig dato")
    kind.validate(amount=amount, member_id=member_id, bucket_id=bucket_id, note=note)
    _, account_id = audit_actor()
    eid = db.execute("""INSERT INTO kasse_entries(club_id,kind,member_id,bucket_id,amount_numerator,
        amount_denominator,entry_date,note,actor_account_id) VALUES (?,?,?,?,?,?,?,?,?)""",
                     (club_id, kind.key, member_id, bucket_id, amount.numerator, amount.denominator,
                      entry_date, note, account_id)).lastrowid
    log(db, club_id, "create", "entry", eid, None, _entry_log(db, eid))
    return eid


def reverse_entry(db, club_id, entry_id, note=""):
    """Undo an entry with its exact opposite. An entry is reversed at most once; a reversal is never reversed."""
    original = db.execute("SELECT * FROM kasse_entries WHERE id=? AND club_id=?", (entry_id, club_id)).fetchone()
    if not original:
        raise LookupError("Ukendt postering")
    if original["reverses_id"] is not None:
        raise ValueError("En tilbageførsel kan ikke tilbageføres")
    if db.execute("SELECT 1 FROM kasse_entries WHERE reverses_id=?", (entry_id,)).fetchone():
        raise ValueError("Posteringen er allerede tilbageført")
    amount = -_amount(original)
    _, account_id = audit_actor()
    note = str(note or "").strip()[:500] or f"Tilbageførsel af postering #{entry_id}"
    eid = db.execute("""INSERT INTO kasse_entries(club_id,kind,member_id,bucket_id,amount_numerator,
        amount_denominator,entry_date,note,reverses_id,actor_account_id) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                     (club_id, original["kind"], original["member_id"], original["bucket_id"],
                      amount.numerator, amount.denominator, today(), note, entry_id, account_id)).lastrowid
    log(db, club_id, "reverse", "entry", eid, None, _entry_log(db, eid))
    return eid


def _entry_log(db, eid):
    r = db.execute("SELECT * FROM kasse_entries WHERE id=?", (eid,)).fetchone()
    return {k: r[k] for k in ("kind", "member_id", "bucket_id", "amount_numerator", "amount_denominator",
                              "entry_date", "note", "reverses_id")}


def set_start(db, club_id, start, cash=Fraction(0)):
    """Where the books begin: nights before the date don't count, and the club had `cash` on that date.

    It is saved once and then locked, because changing the date or the amount later would rewrite
    every saldo and the cash after the fact. Mistakes are set right with korrektioner.
    Members' starting saldi are booked as korrektioner too.
    """
    start = str(start or "").strip() or None
    if start:
        try:
            datetime.strptime(start, "%Y-%m-%d")
        except ValueError:
            raise ValueError("Ugyldig startdato")
    current = club(db, club_id)
    if current["kasse_started_at"]:
        raise ValueError("Kassens start er låst. Ret med en korrektion i stedet.")
    db.execute("""UPDATE clubs SET kasse_start=?,kasse_start_cash_numerator=?,kasse_start_cash_denominator=?,
        kasse_started_at=CURRENT_TIMESTAMP WHERE id=?""", (start, cash.numerator, cash.denominator, club_id))
    log(db, club_id, "create", "kasse_start", None, None,
        {"kasse_start": start, "start_cash": [cash.numerator, cash.denominator]})


def set_kasserer(db, club_id, member_id):
    if member_id is not None and not db.execute(
            "SELECT 1 FROM club_members WHERE id=? AND club_id=? AND status='active'", (member_id, club_id)).fetchone():
        raise ValueError("Kassereren skal være et aktivt medlem af klubben")
    old = club(db, club_id)["kasserer_member_id"]
    if old != member_id:
        db.execute("UPDATE clubs SET kasserer_member_id=? WHERE id=?", (member_id, club_id))
        log(db, club_id, "update", "kasserer", None, {"member_id": old}, {"member_id": member_id})


def drop_inactive_kasserer(db, club_id):
    """A kasserer who is set inactive loses the role (logged), so reactivating them later
    does not quietly give them back the right to book entries."""
    if club(db, club_id)["kasserer_member_id"] is not None and not kasserer(db, club_id):
        set_kasserer(db, club_id, None)


# Balances ----------------------------------------------------------------

def balances(db, club_id):
    """Per member: Bogført, Afventer and I alt, plus the club's cash.

    A saldo is negative when the member owes the club and positive when the club owes the member,
    so a night's charge counts negative. Per member:
      nights, fejl40   what posted nights charged (settlement, Fejl40)
      entries          the entries' effect
      posted           Bogført = nights + fejl40 + entries
      pending          Afventer: what nights not posted yet charge now
      pending_nights   [(night id, title, date, amount)] behind pending, oldest first
      balance          I alt = posted + pending
    The club's cash comes from the start cash and the entries only; nights never change it.
    """
    current = club(db, club_id)
    started = bool(current["kasse_started_at"])
    # Only a saved (and so locked) start counts.
    kasse_start, opening = (current["kasse_start"], start_cash(current)) if started else (None, Fraction(0))
    members = {r["id"]: dict(r, nights=Fraction(), fejl40=Fraction(), entries=Fraction(), pending=Fraction(),
                             pending_nights=[])
               for r in db.execute("SELECT id,name,status FROM club_members WHERE club_id=? ORDER BY id", (club_id,))}
    for r in db.execute("""SELECT member_id,part,amount_numerator,amount_denominator FROM kasse_night_charges
            WHERE club_id=?""", (club_id,)):
        if r["member_id"] in members:
            members[r["member_id"]]["nights" if r["part"] == "aften" else "fejl40"] -= Fraction(r[2], r[3])
    pending = aftener.pending_nights(db, club_id, kasse_start)
    for night in pending:
        charged = {}
        for part in (night["settlement"], night["fejl40"]):
            for mid, amount in part.items():
                charged[mid] = charged.get(mid, Fraction()) + amount
        for mid, amount in charged.items():
            if mid in members and amount:
                members[mid]["pending"] -= amount
                members[mid]["pending_nights"].append(dict(id=night["id"], title=night["title"], date=night["date"],
                                                           open=night["open"], stale=night["stale"], amount=-amount))
    cash = opening
    for row in _entry_rows(db, club_id):
        effect = _effect(row)
        cash += effect.cash
        for mid, amount in effect.members.items():
            if mid in members:
                members[mid]["entries"] += amount
    rows = []
    for m in members.values():
        m["posted"] = m["nights"] + m["fejl40"] + m["entries"]
        m["balance"] = m["posted"] + m["pending"]
        if m["status"] == "active" or m["nights"] or m["fejl40"] or m["entries"] or m["pending"]:
            rows.append(m)
    return dict(
        members=rows, cash=cash, kasse_start=kasse_start, start_cash=opening, started=started,
        pending_nights=pending,
        owed_to_club=-sum((m["balance"] for m in rows if m["balance"] < 0), Fraction()),
        owed_by_club=sum((m["balance"] for m in rows if m["balance"] > 0), Fraction()),
    )


def member_balance(books, member_id):
    """One member's saldo (I alt) from a balances() result."""
    return next((m["balance"] for m in books["members"] if m["id"] == member_id), Fraction())


def member_row(books, member_id):
    """One member's row (posted, pending, balance, pending_nights) from a balances() result, or None."""
    return next((m for m in books["members"] if m["id"] == member_id), None)


# Posting nights ------------------------------------------------------------
#
# No timers: whether a night is due is worked out from what the database holds (when it was last
# closed) and the time now, so a restart or a long downtime changes nothing. Posting runs inside
# the caller's BEGIN IMMEDIATE, and kasse_night_postings.session_id is UNIQUE, so two requests
# can never post the same night twice.

def due_nights(db, club_id, now=None):
    """The club's closed nights with Klubkasse whose window has passed and that are not posted yet.

    Nothing is due before Kassens start is saved: until then a night from before the start date may
    still drop out, so posting it would be wrong.
    """
    current = club(db, club_id)
    if not current or not current["kasse_started_at"]:
        return []
    now = now or datetime.now(timezone.utc)
    due = []
    for night in aftener.unposted_nights(db, club_id, closed_only=True):
        final = aftener.final_at(night)
        if final is not None and final <= now and not aftener.before_start(night, current["kasse_start"]):
            due.append(night)
    return due


def post_night(db, club_id, night, source="spilaften"):
    """Post what one closed night charges, once. Returns False if it was posted already.

    The caller holds BEGIN IMMEDIATE. The posting row comes first: if it exists, nothing else is
    written. Only members of the club are charged, as balances() only counts them.
    """
    closed = aftener.closed_at(night)
    posting = db.execute("""INSERT OR IGNORE INTO kasse_night_postings(club_id,session_id,session_title,night_date,
        source,closed_at) VALUES (?,?,?,?,?,?)""",
                         (club_id, night["id"], night["title"], aftener.night_date(night), source,
                          closed.strftime(aftener.STAMP) if closed else None))
    if posting.rowcount != 1:
        return False
    posting_id = posting.lastrowid
    members = {r[0] for r in db.execute("SELECT id FROM club_members WHERE club_id=?", (club_id,))}
    charges = []
    for part, amounts in zip(("aften", "fejl40"), aftener.night_parts(db, night["id"])):
        for mid, amount in sorted(amounts.items()):
            if amount and mid in members:
                charges.append((posting_id, club_id, mid, part, amount.numerator, amount.denominator))
    db.executemany("""INSERT INTO kasse_night_charges(posting_id,club_id,member_id,part,amount_numerator,
        amount_denominator) VALUES (?,?,?,?,?,?)""", charges)
    log(db, club_id, "post", "night", night["id"], None,
        dict(title=night["title"], date=aftener.night_date(night), source=source,
             closed_at=closed.strftime(aftener.STAMP) if closed else None,
             charges=[[c[2], c[3], c[4], c[5]] for c in charges]), system=True)
    return True


def try_post_night(db, club_id, night, source="spilaften"):
    """post_night, but a night whose charges cannot be worked out (aftener.NIGHT_ERRORS) is skipped
    and logged instead of stopping the others. A savepoint undoes whatever it wrote, so a
    half-written posting is never left behind; the night stays in Afventer, flagged as an error.

    The caller must hold a transaction (BEGIN IMMEDIATE): outside one, SQLite's SAVEPOINT would open
    its own and RELEASE would commit it early, without the write lock that makes posting exactly-once."""
    if not db.in_transaction:
        raise RuntimeError("try_post_night needs the caller's transaction (BEGIN IMMEDIATE)")
    db.execute("SAVEPOINT kasse_post_night")
    try:
        posted = post_night(db, club_id, night, source)
    except (*aftener.NIGHT_ERRORS, sqlite3.IntegrityError):
        db.execute("ROLLBACK TO kasse_post_night"); db.execute("RELEASE kasse_post_night")
        logger.warning("Kasse cannot post night %s of club %s; it stays in Afventer", night["id"], club_id, exc_info=True)
        return False
    db.execute("RELEASE kasse_post_night")
    return posted


def finalize_due(db, club_id, now=None):
    """Post every due night of the club; the caller holds BEGIN IMMEDIATE. Returns the posted night ids.
    Safe to call again and again: a posted night is never due. A broken night does not stop the others."""
    return [night["id"] for night in due_nights(db, club_id, now) if try_post_night(db, club_id, night)]


def posted_nights(db, club_id, limit=100):
    """Posted nights, newest first, with what each charged in all."""
    fields = ("id", "session_id", "deleted_session_id", "session_title", "night_date", "source", "closed_at", "posted_at")
    nights = {}
    # One query: a row per charge (or one with no charge), summed per posting as Fractions.
    for r in db.execute(f"""SELECT {",".join("p." + f for f in fields)},c.amount_numerator,c.amount_denominator
            FROM (SELECT * FROM kasse_night_postings WHERE club_id=? ORDER BY id DESC LIMIT ?) p
            LEFT JOIN kasse_night_charges c ON c.posting_id=p.id ORDER BY p.id DESC""", (club_id, limit)):
        night = nights.setdefault(r["id"], dict({f: r[f] for f in fields}, total=Fraction()))
        if r["amount_numerator"] is not None:
            night["total"] += Fraction(r["amount_numerator"], r["amount_denominator"])
    return list(nights.values())


# Alerts: a night that already counted in the kasse changed --------------------

ALERT_KINDS = {
    "reopen": "Afsluttet spilaften genåbnet",
    "delete": "Spilaften slettet",
    "import": "Spilaften importeret",
    "money": "Klubkasse slået til/fra for importeret aften",
}


def snapshot(db, sid):
    """What a night charges, as stored in an alert: [[member_id, numerator, denominator]], never names."""
    return [[mid, a.numerator, a.denominator] for mid, a in sorted(aftener.night_amounts(db, sid).items())]


def _night(db, sid):
    return db.execute("SELECT id,title,club_id,created_at,origin FROM sessions WHERE id=?", (sid,)).fetchone()


def _counted_start(db, club_id):
    """The kasse's start date, once it is saved; before that every night counts."""
    current = club(db, club_id)
    return current["kasse_start"] if current["kasse_started_at"] else None


def _posted(db, sid):
    """Is the night posted right now? Asked before a change, so a deleted night is still found by its id."""
    return db.execute("SELECT 1 FROM kasse_night_postings WHERE session_id=?", (sid,)).fetchone() is not None


def _insert_alert(db, night, kind, before, after, posted):
    # Nothing changed, or the night is from before the kasse started and never counts.
    if after is not None and before == after:
        return
    if aftener.before_start(night, _counted_start(db, night["club_id"])):
        return
    db.execute("""INSERT INTO kasse_alerts(club_id,session_id,session_title,kind,actor_username,before_json,after_json,
        night_posted) VALUES (?,?,?,?,?,?,?,?)""",
               (night["club_id"], night["id"], night["title"], kind, audit_actor()[0],
                json.dumps(before), None if after is None else json.dumps(after), int(posted)))


def add_alert(db, sid, kind, before, after):
    """Tell the kasserer a night's charges changed. after=None: not known yet (a reopened night)."""
    night = _night(db, sid)
    if night:
        _insert_alert(db, night, kind, before, after, _posted(db, sid))


def _pending_reopen(db, sid):
    return db.execute("""SELECT id,before_json FROM kasse_alerts WHERE session_id=? AND kind='reopen'
        AND after_json IS NULL ORDER BY id DESC LIMIT 1""", (sid,)).fetchone()


@contextmanager
def watch(db, sid, kind):
    """Alert the kasserer if the block changes what the night charges, including deleting it.

    A reopened night is open and charges nothing right now, so it counts as it was before it was
    reopened: its pending reopen alert is replaced by this one, which then has the real 'after'.
    """
    night = _night(db, sid)
    pending = _pending_reopen(db, sid)
    before = json.loads(pending["before_json"]) if pending else snapshot(db, sid)
    posted = _posted(db, sid)  # before the block: deleting the night moves its posting off session_id
    yield
    if pending:
        db.execute("DELETE FROM kasse_alerts WHERE id=?", (pending["id"],))
    if night:
        _insert_alert(db, night, kind, before, snapshot(db, sid), posted)


def complete_reopen_alert(db, sid):
    """A reopened night was closed again: record what it charges now, or drop the alert if nothing changed."""
    alert = _pending_reopen(db, sid)
    if not alert:
        return
    after = snapshot(db, sid)
    if json.loads(alert["before_json"]) == after:
        db.execute("DELETE FROM kasse_alerts WHERE id=?", (alert["id"],))
    else:
        db.execute("UPDATE kasse_alerts SET after_json=? WHERE id=?", (json.dumps(after), alert["id"]))


def unseen_alerts(db, club_id):
    return db.execute("SELECT COUNT(*) FROM kasse_alerts WHERE club_id=? AND seen_at IS NULL", (club_id,)).fetchone()[0]


def mark_seen(db, club_id, alert_id):
    username, _ = audit_actor()
    found = db.execute("UPDATE kasse_alerts SET seen_at=CURRENT_TIMESTAMP,seen_by=? WHERE id=? AND club_id=? AND seen_at IS NULL",
                       (username, alert_id, club_id)).rowcount
    if found:
        log(db, club_id, "seen", "alert", alert_id)


def alerts(db, club_id, limit=50):
    """Alerts, unseen first, with each member's change. Names are read live from the member ids."""
    names = {r[0]: r[1] for r in db.execute("SELECT id,name FROM club_members WHERE club_id=?", (club_id,))}
    rows = []
    for r in db.execute("SELECT * FROM kasse_alerts WHERE club_id=? ORDER BY seen_at IS NOT NULL, id DESC LIMIT ?",
                        (club_id, limit)):
        before = {m: Fraction(n, d) for m, n, d in json.loads(r["before_json"])}
        after = None if r["after_json"] is None else {m: Fraction(n, d) for m, n, d in json.loads(r["after_json"])}
        changes = []
        for mid in sorted(set(before) | set(after or {})):
            old, new = before.get(mid, Fraction()), None if after is None else after.get(mid, Fraction())
            changes.append(dict(member_id=mid, name=names.get(mid, f"#{mid}"), before=old, after=new,
                                diff=None if new is None else new - old))
        # A posted night's charges stay as posted; the kasserer decides on a korrektion. The alert
        # records whether its night was posted when it was made, so a reused night id never misleads.
        # Alerts from before night_posted was recorded fall back to matching the posting by id.
        if r["night_posted"] is not None:
            posted = bool(r["night_posted"])
        else:
            column = "deleted_session_id" if r["kind"] == "delete" else "session_id"
            posted = db.execute(f"""SELECT 1 FROM kasse_night_postings WHERE club_id=? AND {column}=? AND posted_at<=?""",
                                (club_id, r["session_id"], r["created_at"])).fetchone() is not None
        rows.append(dict(r, kind_label=ALERT_KINDS.get(r["kind"], r["kind"]), changes=changes, pending=after is None,
                         posted=posted))
    return rows
