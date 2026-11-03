"""Scoring and metadata for Paskrig."""

from fractions import Fraction

from .base import Bid, BidResult


class Paskrig(Bid):
    key = "paskrig"
    label = "Paskrig"
    target = "none"
    options = {"value": ("Værdi", 9)}

    def configuration(self, options=None):
        return dict(label=self.label, target=self.target,
                    value=self.option_values(options)["value"])

    def score(
        self,
        config,
        *,
        active,
        declarer,
        target=0,
        actual=None,
        partner=None,
        actuals=None,
    ):
        if (
            len(active) != 4
            or len(set(active)) != 4
            or not actuals
            or set(actuals) != set(active)
        ):
            raise ValueError("Paskrig requires four players")
        if any(not 0 <= n <= 13 for n in actuals.values()):
            raise ValueError("Invalid tricks")

        highest = max(actuals.values())
        losers = {pid for pid, n in actuals.items() if n == highest}
        winners = set(active) - losers

        value = self.option_values(config)["value"]
        scores = {pid: Fraction(0) for pid in active}
        if winners:
            for pid in winners:
                scores[pid] = value / len(winners)
            for pid in losers:
                scores[pid] = -value / len(losers)

        return BidResult(scores=scores)
