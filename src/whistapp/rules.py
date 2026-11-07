"""Shared settlement utilities; bid definitions live in whistapp/bids/*.py."""

import json
from fractions import Fraction

from .bids import registered_bids


def load_rules(bid_settings=None):
    """Bid metadata with a club's configured values; no YAML is read at runtime.

    bid_settings maps a bid class key to {"enabled": bool, "options": {name: value}};
    missing entries mean the original house rules.
    """
    settings = bid_settings or {}

    def options(bid):
        return settings.get(bid.key, {}).get("options")

    return {
        "tricks_per_hand": 13,
        "active_per_hand": 4,
        "bids": {
            key: bid.configuration(level=int(key[-1]), options=options(bid))
            if key.startswith("vip")
            else bid.configuration(options=options(bid))
            for key, bid in registered_bids().items()
        },
    }


def bid_classes():
    """One entry per bid implementation, in registration order (VIP once)."""
    unique = {}
    for bid in registered_bids().values():
        unique.setdefault(bid.key, bid)
    return list(unique.values())


# The club's bid types from lowest to highest when bidding, until a club sets its own
# order on Klubadministration. This is also the old score book's order.
DEFAULT_BID_ORDER = ["almindelige", "halve", "vip", "sans", "gode", "sol", "ren_sol",
                     "bordlaegger", "paskrig"]


def bid_type_order(bid_settings=None):
    """Bid types (VIP once) in the club's order.

    Types with a saved "order" come first, sorted by it. A type without one goes last:
    one the club has never ordered, or a bid added to the app after the club saved its
    order. That placement is provisional; it belongs with versioned club rules, which
    should decide where a new bid enters a club's ranking.
    """
    settings = bid_settings or {}
    types = [bid.key for bid in bid_classes()]
    default = {key: i for i, key in enumerate(DEFAULT_BID_ORDER)}
    saved = sorted((t for t in types if isinstance(settings.get(t, {}).get("order"), int)),
                   key=lambda t: settings[t]["order"])
    rest = sorted((t for t in types if t not in saved), key=lambda t: (default.get(t, len(default)), types.index(t)))
    return saved + rest


def ordered_bid_keys(bid_settings=None):
    """Every bid key in the club's order; VIP expands to its levels vip1-vip3."""
    by_type = {}
    for key, bid in registered_bids().items():
        by_type.setdefault(bid.key, []).append(key)
    return [key for t in bid_type_order(bid_settings) for key in by_type[t]]


def enabled_bid_keys(bid_settings=None):
    """The bids a club plays, in the club's order."""
    settings = bid_settings or {}
    bids = registered_bids()
    return [key for key in ordered_bid_keys(settings)
            if settings.get(bids[key].key, {}).get("enabled", True)]


def bid_short_names(bid_settings=None):
    """Each bid key's short name for the compact hand table: the club's own, per bid type
    (VIP followed by its level), else the bid's normal name. Display only, so a change
    shows on every night at once."""
    settings = bid_settings or {}
    names = {}
    for key, bid in registered_bids().items():
        short = str(settings.get(bid.key, {}).get("short") or "").strip()
        if key.startswith("vip"):
            names[key] = (short or "VIP") + key[-1]
        else:
            names[key] = short or bid.label
    return names


def rules_snapshot(bid):
    """JSON for hands.rules_snapshot; exact Fractions are stored as text like '3/2'."""
    return json.dumps(bid, ensure_ascii=False, default=str)


def target_for(bid, target):
    if bid["target"] == "none":
        return 0
    if bid["target"] == "fixed":
        return int(bid["required_tricks"])

    value = int(target)
    if not bid["min_target"] <= value <= bid["max_target"]:
        raise ValueError("Invalid target")

    return value


def calculate(bid, target, actual):
    """Return (won, per-opponent amount) for ordinary contracts.

    Successful: price at BID target times (actual - 6).
    Failed: configurable penalty factor times missing tricks times price at BID target.
    """
    if not 0 <= actual <= 13:
        raise ValueError("Invalid tricks")
    if bid["target"] != "variable":
        raise ValueError("Use special settlement for this bid")

    if actual >= target:
        return True, Fraction(str(bid["values"][target - 8])) * (actual - 6)
    return False, -Fraction(str(bid.get("failure_multiplier", 2))) * (
        target - actual
    ) * Fraction(str(bid["values"][target - 8]))


def distribute(per_opponent, declarer, active, partner=None):
    if len(active) != 4 or len(set(active)) != 4 or declarer not in active:
        raise ValueError("Four active players required")
    if partner is None:
        partner = declarer
    if partner not in active:
        raise ValueError("Partner must be playing")

    team = {declarer, partner}
    if len(team) == 2:
        return {pid: (per_opponent if pid in team else -per_opponent) for pid in active}
    return {
        pid: (3 * per_opponent if pid == declarer else -per_opponent) for pid in active
    }


def special_outcome(bid, actual):
    """Misere contracts: sol allows one trick; the others allow none."""
    if not 0 <= actual <= 13:
        raise ValueError("Invalid tricks")

    maximum = int(bid["max_allowed_tricks"])
    return actual <= maximum, Fraction(str(bid["value"]))


def distribute_special(outcomes, active):
    """One fixed pot per special contract, paid by the losing side to winners.

    Every active non-bidder is a loser when a special bid succeeds, and
    a winner when the bid fails. Bidders are classified by their own result.
    The 9/18/36 pot is divided equally within each side, not per bidder.
    """
    if len(active) != 4 or len(set(active)) != 4:
        raise ValueError("Four active players required")
    if not outcomes or not set(outcomes).issubset(set(active)):
        raise ValueError("Invalid special bidders")

    stakes = {stake for _, stake in outcomes.values()}
    if len(stakes) != 1:
        raise ValueError("Mixed special stakes")

    pot = next(iter(stakes))
    winners = {p for p, (won, _) in outcomes.items() if won}
    losers = {p for p, (won, _) in outcomes.items() if not won}
    nonbidders = set(active) - set(outcomes)
    if winners and not losers:
        losers = nonbidders
    elif losers and not winners:
        winners = nonbidders

    # When there are winning and losing bidders, nonbidders stay neutral.
    if not winners or not losers:
        return {p: Fraction(0) for p in active}

    return {
        p: (
            pot / len(winners)
            if p in winners
            else -pot / len(losers)
            if p in losers
            else Fraction(0)
        )
        for p in active
    }


def distribute_paskrig(actuals, active, pot=9):
    """Highest trick-takers share the total loss; others share the gain.

    Tie handling is a configurable house-rule assumption: ties at maximum all
    lose equally. If all four tie, the round is zero-sum with no transfer.
    """
    if len(active) != 4 or len(set(active)) != 4 or set(actuals) != set(active):
        raise ValueError("Paskrig requires results from all four players")
    if any(not 0 <= v <= 13 for v in actuals.values()):
        raise ValueError("Invalid tricks")

    top = max(actuals.values())
    losers = {p for p, v in actuals.items() if v == top}
    winners = set(active) - losers
    if not winners:
        return {p: Fraction(0) for p in active}

    pot = Fraction(str(pot))
    return {
        p: (-pot / len(losers) if p in losers else pot / len(winners)) for p in active
    }
