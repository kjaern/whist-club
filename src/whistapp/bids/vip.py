"""VIP: one bid with the level supplied as an input."""

from fractions import Fraction

from .base import Bid, BidResult


class VIP(Bid):
    key = "vip"
    label = "VIP"
    target = "variable"
    min_target = 8
    max_target = 13
    default_target = 8
    options = {
        "modifier_1": ("Tillæg VIP1", "1/2"),
        "modifier_2": ("Tillæg VIP2", 1),
        "modifier_3": ("Tillæg VIP3", 2),
    }

    def configuration(self, level=1, options=None):
        values = self.option_values(options)
        modifier = values[f"modifier_{level}"]
        return dict(
            **values,
            label=f"VIP{level}",
            target=self.target,
            min_target=self.min_target,
            max_target=self.max_target,
            default_target=self.default_target,
            level=level,
            failure_multiplier=2,
            modifier=modifier,
            values=[Fraction(bid - 6, 2) + modifier for bid in range(8, 14)],
        )

    def score(
        self,
        config=None,
        *,
        active,
        declarer,
        target=0,
        actual=None,
        partner=None,
        actuals=None,
        level=1,
    ):
        if len(active) != 4 or len(set(active)) != 4 or declarer not in active:
            raise ValueError("Exactly four active players are required")
        if partner not in active:
            raise ValueError("An active partner is required")
        if level not in (1, 2, 3):
            raise ValueError("VIP level must be 1, 2 or 3")
        if not 8 <= target <= 13 or actual is None or not 0 <= actual <= 13:
            raise ValueError("Invalid bid or number of tricks")

        ordinary_price = Fraction(target - 6, 2)
        price = ordinary_price + self.option_values(config)[f"modifier_{level}"]

        if actual >= target:
            points = price * (actual - 6)
            won = True
        else:
            points = -2 * (target - actual) * price
            won = False

        team = {declarer, partner}

        scores = {
            player: (3 * points if player == declarer else -points)
            if partner == declarer
            else (points if player in team else -points)
            for player in active
        }

        return BidResult(scores=scores, won=won, contract_points=points)
