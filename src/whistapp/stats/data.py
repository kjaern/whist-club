"""Load one statistics scope (the club's lifetime, a year, a period between two dates or a night) into plain records.

Statistics never query the database themselves: they receive a StatsData and compute
from its hands and nights. Scores stay Fractions; formatting happens at display time.
"""

import json
from collections import defaultdict
from dataclasses import dataclass, field, replace
from fractions import Fraction

from ..club import active_club_id, club_players, club_settings
from ..rule_versions import night_rules

KINDS = ("all", "year", "period", "night")
ORIGINS = ("all", "live", "imported")


@dataclass(frozen=True)
class Scope:
    kind: str = "all"
    year: int | None = None
    session_id: int | None = None
    origin: str = "all"
    date_from: str | None = None  # period: ISO dates, both included; None leaves that end open
    date_to: str | None = None

    def __post_init__(self):
        if self.kind not in KINDS or self.origin not in ORIGINS:
            raise ValueError("Invalid statistics scope")

    def period_text(self):
        """The period in Danish dates, fx "1/3/2026 – 30/6/2026"; an open end is left blank."""
        def day(value):
            if not value:
                return ""
            year, month, dom = value.split("-")
            return f"{int(dom)}/{int(month)}/{year}"
        return f"{day(self.date_from)} – {day(self.date_to)}".strip()


@dataclass
class PlayerRecord:
    """A club member's seat on one night; guests are not loaded."""
    id: int
    session_id: int
    member_id: int
    name: str


@dataclass
class HandRecord:
    id: int
    session_id: int
    year: str
    bid_key: str
    bid_label: str
    target: int | None
    actual: int | None
    won: int | None
    declarer: int | None  # player id (member or guest)
    partner: int | None
    participants: frozenset
    scores: dict  # player id -> Fraction, guests included
    specials: list  # (player id, actual, won) per special bidder
    dealer: int | None = None  # player id; recorded on live hands since 7 Nov 2026


@dataclass
class NightRecord:
    id: int
    title: str
    created_at: str
    origin: str
    year: str
    date: str  # Danish display date
    collect_club_money: bool
    closed: bool = False  # ended_at is set; a reopened night is open again
    final_scores: dict = field(default_factory=dict)  # player id -> Fraction (final-only imports)
    penalties: list = field(default_factory=list)  # (player id, amount), only when money is collected
    settlements: dict = field(default_factory=dict)  # member id -> Fraction
    member_names: dict = field(default_factory=dict)  # member id -> club_members.name (settlements)


@dataclass
class StatsData:
    scope: Scope
    hands: list
    nights: dict  # id -> NightRecord, oldest first
    players: dict  # player id -> PlayerRecord (club members only)
    names: dict  # str(member id) -> current display name
    bids: list = field(default_factory=list)  # (key, label) of every bid the club plays, in its order
    bid_order: list = field(default_factory=list)  # every bid key in the scope's order
    seats: dict = field(default_factory=dict)  # night id -> player ids in seat order, guests included

    def bid_rank(self, key):
        """A bid's place in the club's order (the night's own for a night); unknown ones last."""
        return self.bid_order.index(key) if key in self.bid_order else len(self.bid_order)

    def member(self, pid):
        player = self.players.get(pid)
        return player.member_id if player else None

    def member_name(self, mid, fallback=""):
        return self.names.get(str(mid), fallback)

    def name_of(self, mid):
        """The member's current name, else the name on one of their seats."""
        fallback = next((p.name for p in self.players.values() if p.member_id == mid), "")
        return self.member_name(mid, fallback)

    def for_year(self, year):
        year = str(year)
        return replace(self, hands=[h for h in self.hands if h.year == year],
                       nights={k: n for k, n in self.nights.items() if n.year == year})

    def for_night(self, sid):
        """One night of the loaded scope, as if it had been loaded on its own."""
        return replace(self, scope=replace(self.scope, kind="night", session_id=sid),
                       hands=[h for h in self.hands if h.session_id == sid],
                       nights={sid: self.nights[sid]})

    def years(self):
        return sorted({n.year for n in self.nights.values()}, reverse=True)

    def nights_with_members(self):
        """Nights where a club member played a hand or has an imported final score."""
        played = {h.session_id for h in self.hands
                  if any(self.players.get(p) and self.players[p].session_id == h.session_id
                         for p in h.participants)}
        played |= {n.id for n in self.nights.values() if any(p in self.players for p in n.final_scores)}
        return [n for n in self.nights.values() if n.id in played]


def load(db, scope, roster=None):
    from ..web import night_time

    roster = club_players(db) if roster is None else roster
    where = "s.is_club=1 AND s.club_id=? AND (?='all' OR s.origin=?)"
    args = [active_club_id(), scope.origin, scope.origin]
    if scope.kind == "year":
        where += " AND substr(s.created_at,1,4)=?"
        args.append(str(scope.year))
    elif scope.kind == "night":
        where += " AND s.id=?"
        args.append(scope.session_id)
    elif scope.kind == "period":
        # By the night's displayed (Danish) date, which can differ from the stored UTC one.
        ids = [r["id"] for r in db.execute(f"SELECT s.* FROM sessions s WHERE {where}", args)
               if (scope.date_from or "") <= night_time(r)[:10] <= (scope.date_to or "9999")]
        where += f" AND s.id IN ({','.join('?' * len(ids)) or 'NULL'})"
        args.extend(ids)

    nights = {}
    for r in db.execute(
        f"SELECT s.* FROM sessions s WHERE {where} ORDER BY s.created_at, s.id", args
    ):
        nights[r["id"]] = NightRecord(
            id=r["id"], title=r["title"], created_at=r["created_at"], origin=r["origin"],
            year=str(r["created_at"])[:4], date=night_time(r)[:10],
            collect_club_money=bool(r["collect_club_money"]), closed=r["ended_at"] is not None,
        )

    players = {
        r["id"]: PlayerRecord(r["id"], r["session_id"], r["member_id"], r["name"])
        for r in db.execute(
            f"""SELECT p.id,p.session_id,p.member_id,p.name FROM players p
            JOIN sessions s ON s.id=p.session_id WHERE {where} AND p.member_id IS NOT NULL""",
            args,
        )
    }

    def per_hand(sql):
        rows = defaultdict(list)
        for r in db.execute(
            f"""SELECT x.* FROM {sql} x JOIN hands h ON h.id=x.hand_id
            JOIN sessions s ON s.id=h.session_id WHERE {where}""",
            args,
        ):
            rows[r["hand_id"]].append(r)
        return rows

    # Only a night's last seating is stored; imported nights use the sheet's column order.
    seats = defaultdict(list)
    for r in db.execute(
        f"""SELECT p.id,p.session_id FROM players p JOIN sessions s ON s.id=p.session_id
        WHERE {where} ORDER BY p.session_id, COALESCE(p.seat_order,p.id), p.id""",
        args,
    ):
        seats[r["session_id"]].append(r["id"])

    participants = per_hand("hand_participants")
    scores = per_hand("hand_scores")
    specials = per_hand("special_bids")

    hands = []
    for r in db.execute(
        f"""SELECT h.*, d.player_id AS declarer_id FROM hands h
        JOIN sessions s ON s.id=h.session_id LEFT JOIN hand_declarers d ON d.hand_id=h.id
        WHERE {where} ORDER BY h.id""",
        args,
    ):
        hands.append(HandRecord(
            id=r["id"], session_id=r["session_id"], year=nights[r["session_id"]].year,
            bid_key=r["bid_key"], bid_label=r["bid_label"], target=r["target"],
            actual=r["actual"], won=r["won"], declarer=r["declarer_id"], partner=r["partner_id"],
            participants=frozenset(x["player_id"] for x in participants[r["id"]]),
            scores={x["player_id"]: Fraction(x["numerator"], x["denominator"]) for x in scores[r["id"]]},
            specials=[(x["player_id"], x["actual"], x["won"]) for x in specials[r["id"]]],
            dealer=r["dealer_id"],
        ))

    for r in db.execute(
        f"""SELECT f.* FROM historical_final_scores f JOIN sessions s ON s.id=f.session_id
        WHERE {where}""",
        args,
    ):
        nights[r["session_id"]].final_scores[r["player_id"]] = Fraction(r["numerator"], r["denominator"])

    for r in db.execute(
        f"""SELECT pp.session_id,pp.player_id,pp.amount FROM penalty_payments pp
        JOIN sessions s ON s.id=pp.session_id WHERE {where} AND s.collect_club_money=1""",
        args,
    ):
        nights[r["session_id"]].penalties.append((r["player_id"], r["amount"]))

    # What each member owes for the night: money-mode settlements plus placement payments.
    for r in db.execute(
        f"""SELECT cms.session_id,cms.member_id,cm.name,cms.amount_numerator,cms.amount_denominator
        FROM club_member_settlements cms JOIN sessions s ON s.id=cms.session_id
        JOIN club_members cm ON cm.id=cms.member_id WHERE {where}""",
        args,
    ):
        night = nights[r["session_id"]]
        night.settlements[r["member_id"]] = night.settlements.get(r["member_id"], Fraction()) + Fraction(
            r["amount_numerator"], r["amount_denominator"])
        night.member_names[r["member_id"]] = r["name"]
    for r in db.execute(
        f"""SELECT ss.session_id,p.member_id,cm.name,ss.placement_amount FROM session_settlements ss
        JOIN players p ON p.id=ss.player_id JOIN sessions s ON s.id=ss.session_id
        JOIN club_members cm ON cm.id=p.member_id WHERE {where}""",
        args,
    ):
        night = nights[r["session_id"]]
        night.settlements[r["member_id"]] = night.settlements.get(r["member_id"], Fraction()) + Fraction(
            r["placement_amount"])
        night.member_names[r["member_id"]] = r["name"]

    return StatsData(scope=scope, hands=hands, nights=nights, players=players,
                     names={str(k): v for k, v in roster}, seats=dict(seats), **_bid_catalogue(db, scope, hands))


def _bid_catalogue(db, scope, hands):
    """Every bid the club plays, so unplayed bids show with zero, in the club's order; a
    night uses its own snapshot. Bids since disabled stay listed, at their place in the
    order, when they were played."""
    from ..rules import enabled_bid_keys, load_rules, ordered_bid_keys

    if scope.kind == "night":
        settings = night_rules(db, scope.session_id)["bid_settings"]
    else:
        row = club_settings(db)
        settings = json.loads(row["bid_settings"]) if row and row["bid_settings"] else {}
    rules = load_rules(settings)["bids"]
    labels = {k: rules[k]["label"] for k in enabled_bid_keys(settings)}
    for h in hands:
        labels.setdefault(h.bid_key, h.bid_label)
    order = ordered_bid_keys(settings)
    order += [key for key in labels if key not in order]
    return dict(bids=sorted(labels.items(), key=lambda item: order.index(item[0])), bid_order=order)
