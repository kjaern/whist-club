"""Almindelige: complete scoring rules."""

from fractions import Fraction

from .base import Bid, BidResult


class Almindelige(Bid):
    key = "almindelige"
    label = "Almindelige"
    target = "variable"
    min_target = 8
    max_target = 13
    default_target = 8
    failure_multiplier = 2
    options = {"base_price": ("Grundpris", 1)}

    def configuration(self, options=None):
        base_price = self.option_values(options)["base_price"]
        return dict(
            label=self.label,
            target=self.target,
            min_target=self.min_target,
            max_target=self.max_target,
            default_target=self.default_target,
            failure_multiplier=self.failure_multiplier,
            base_price=base_price,
            values=[Fraction(bid - 6, 2) * base_price for bid in range(8, 14)],
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
    ):
        if len(active) != 4 or len(set(active)) != 4 or declarer not in active:
            raise ValueError("Exactly four active players are required")
        if partner not in active:
            raise ValueError("An active partner is required")
        if not 8 <= target <= 13 or actual is None or not 0 <= actual <= 13:
            raise ValueError("Invalid bid or number of tricks")

        base_price = self.option_values(config)["base_price"]
        price = Fraction(target - 6, 2) * base_price

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
