"""A test club with made-up members and nights, for trying out statistics and graphs.

Every hand is scored by the registered bids, exactly as a hand entered at the table,
and every night is closed with the club's settlement; nothing touches other clubs.
"""

import json
import random
from datetime import date, datetime, timedelta
from fractions import Fraction

from .bids import get_bid
from .buildinfo import build_stamp
from .finance import settlement_entries
from .rule_versions import stamp_night
from .rules import enabled_bid_keys, load_rules, rules_snapshot, special_outcome, target_for
from .web import hand_round

NAMES = ["Anna", "Bent", "Carla", "Dennis", "Eva", "Frede", "Gitte"]
YEARS = 5
NIGHTS_PER_YEAR = (10, 15)
HANDS_PER_NIGHT = (18, 32)
# How often each bid is played; bids not listed are rare.
WEIGHTS = {"almindelige": 40, "halve": 14, "vip1": 6, "vip2": 3, "vip3": 1, "sans": 8, "gode": 8,
           "sol": 5, "ren_sol": 2, "bordlaegger": 1, "paskrig": 3}


def _ts(moment):
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _night_dates(rng, year, today):
    """10-15 distinct evenings spread over the year, none after today."""
    last = date(year, 12, 31) if year < today.year else today
    days = (last - date(year, 1, 1)).days + 1
    count = min(rng.randint(*NIGHTS_PER_YEAR), days)
    return sorted(date(year, 1, 1) + timedelta(days=d) for d in rng.sample(range(days), count))


def _hand(rng, bids, keys, active):
    """(bid key, target, actual, won, contract points, partner, declarer, specials, scores)."""
    key = rng.choices(keys, [WEIGHTS.get(k, 1) for k in keys])[0]
    bid, declarer = bids[key], rng.choice(active)
    if bid["target"] == "variable":
        span = list(range(bid["min_target"], bid["max_target"] + 1))
        target = target_for(bid, rng.choices(span, [2 ** -i for i in range(len(span))])[0])
        actual = min(13, target + rng.choice([0, 0, 1, 1, 2])) if rng.random() < 0.62 else max(0, target - rng.choice([1, 1, 2, 3]))
        partner = declarer if rng.random() < 0.1 else rng.choice([p for p in active if p != declarer])
        result = get_bid(key).score(bid, active=active, declarer=declarer, target=target, actual=actual,
                                    partner=partner, **({"level": int(key[-1])} if key.startswith("vip") else {}))
        return key, target, actual, int(result.won), result.contract_points, partner, declarer, [], result.scores
    target = target_for(bid, None)
    actuals, specials = {}, []
    if bid["target"] == "fixed":
        bidders = [declarer] + ([rng.choice([p for p in active if p != declarer])] if rng.random() < 0.06 else [])
        allowed = int(bid["max_allowed_tricks"])
        for pid in bidders:
            n = rng.randint(0, allowed) if rng.random() < 0.6 else allowed + rng.randint(1, 3)
            actuals[pid] = n
            specials.append((pid, n, int(special_outcome(bid, n)[0])))
    else:  # Paskrig: everyone's tricks, 13 in all
        cuts = sorted(rng.randint(0, 13) for _ in range(3))
        for pid, n in zip(active, [cuts[0], cuts[1] - cuts[0], cuts[2] - cuts[1], 13 - cuts[2]]):
            actuals[pid] = n
            specials.append((pid, n, None))
    scores = get_bid(key).score(bid, active=active, declarer=declarer, actuals=actuals).scores
    return key, target, None, None, None, None, declarer, specials, scores


def fill_test_club(db, club_id, seed=None, today=None):
    """Members and five years of closed club nights for a new, empty club."""
    rng = random.Random(club_id if seed is None else seed)
    today = today or date.today()
    members = [db.execute("INSERT INTO club_members(name,status,club_id) VALUES (?,'active',?)",
                          (name, club_id)).lastrowid for name in NAMES]
    # Placement payment needs one rate per active member, the founder included.
    active_count = db.execute("SELECT COUNT(*) FROM club_members WHERE club_id=? AND status='active'",
                              (club_id,)).fetchone()[0]
    rates = [25 * i for i in range(2, 2 + active_count)]
    db.execute("UPDATE club_settings SET placements=? WHERE id=?", (json.dumps(rates), club_id))
    settings = db.execute("SELECT * FROM club_settings WHERE id=?", (club_id,)).fetchone()
    bid_settings = json.loads(settings["bid_settings"] or "{}")
    bids, keys = load_rules(bid_settings)["bids"], enabled_bid_keys(bid_settings)
    names = dict(zip(members, NAMES))

    nights = 0
    for year in range(today.year - YEARS + 1, today.year + 1):
        for day in _night_dates(rng, year, today):
            start = datetime(day.year, day.month, day.day, 18, 30) + timedelta(minutes=rng.randint(0, 60))
            table = rng.sample(members, rng.choices([4, 5, 6], [2, 5, 2])[0])
            sid = db.execute(
                """INSERT INTO sessions(title,created_at,ended_at,rotation_mode,is_club,collect_club_money,
                    placement_rates,fejl40_rate,club_id,money_mode,point_rate,bid_settings,server_version,server_commit)
                    VALUES (?,?,?,'consecutive',1,1,?,?,?,?,?,?,?,?)""",
                (day.isoformat(), _ts(start), _ts(start + timedelta(hours=4)), settings["placements"],
                 settings["fejl40"], club_id, settings["money_mode"], settings["point_rate"],
                 settings["bid_settings"], *build_stamp())).lastrowid
            stamp_night(db, sid)
            players = [db.execute(
                "INSERT INTO players(session_id,name,member_id,seat_order) VALUES (?,?,?,?)",
                (sid, names[mid], mid, seat)).lastrowid for seat, mid in enumerate(table)]
            for n in range(rng.randint(*HANDS_PER_NIGHT)):
                # With more than four at the table, the rest sit out in turn.
                out = {(n + k) % len(players) for k in range(len(players) - 4)}
                active = [p for i, p in enumerate(players) if i not in out]
                key, target, actual, won, points, partner, declarer, specials, scores = _hand(rng, bids, keys, active)
                dealer, round_no, round_end = hand_round(db, sid, active)
                hid = db.execute(
                    """INSERT INTO hands(session_id,bid_key,bid_label,target,actual,won,contract_points,rules_snapshot,
                        partner_id,dealer_id,round_no,round_end,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (sid, key, bids[key]["label"], target, actual, won,
                     float(points) if points is not None else None, rules_snapshot(bids[key]), partner,
                     dealer, round_no, round_end, _ts(start + timedelta(minutes=7 * n)))).lastrowid
                db.execute("INSERT INTO hand_declarers(hand_id,player_id) VALUES (?,?)", (hid, declarer))
                db.executemany("INSERT INTO hand_participants(hand_id,player_id) VALUES (?,?)", [(hid, p) for p in active])
                db.executemany("INSERT INTO special_bids(hand_id,player_id,actual,won) VALUES (?,?,?,?)",
                               [(hid, *item) for item in specials])
                db.executemany("INSERT INTO hand_scores(hand_id,player_id,numerator,denominator) VALUES (?,?,?,?)",
                               [(hid, p, Fraction(str(v)).numerator, Fraction(str(v)).denominator)
                                for p, v in scores.items()])
            if rng.random() < 0.15:
                db.execute("INSERT INTO penalty_payments(session_id,player_id,amount) VALUES (?,?,?)",
                           (sid, rng.choice(players), settings["fejl40"]))
            entries = settlement_entries(db, sid, settings["money_mode"], rates, settings["point_rate"] or "1")
            db.executemany("""INSERT INTO club_member_settlements
                (session_id,member_id,place,amount_numerator,amount_denominator) VALUES (?,?,?,?,?)""",
                           [(sid, *entry) for entry in entries])
            nights += 1
    return nights
