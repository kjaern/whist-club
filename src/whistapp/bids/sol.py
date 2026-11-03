"""Scoring and metadata for Sol."""

from fractions import Fraction

from .base import Bid, BidResult


class Sol(Bid):
    key = "sol"
    label = "Sol"
    target = "fixed"
    required_tricks = 9
    max_allowed_tricks = 1
    options = {"value": ("Værdi", 9)}

    def configuration(self, options=None):
        return dict(
            label=self.label,
            target=self.target,
            required_tricks=self.required_tricks,
            max_allowed_tricks=self.max_allowed_tricks,
            value=self.option_values(options)["value"],
        )

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
        if len(active) != 4 or len(set(active)) != 4:
            raise ValueError("Exactly four active players are required")
        if not actuals or not set(actuals) <= set(active):
            raise ValueError("Invalid bidders")
        if any(not 0 <= tricks <= 13 for tricks in actuals.values()):
            raise ValueError("Invalid number of tricks")

        winners = {
            pid for pid, tricks in actuals.items() if tricks <= self.max_allowed_tricks
        }
        losers = set(actuals) - winners
        other_players = set(active) - set(actuals)
        if winners and not losers:
            losers = other_players
        elif losers and not winners:
            winners = other_players

        value = self.option_values(config)["value"]
        scores = {pid: Fraction(0) for pid in active}
        if winners and losers:
            for pid in winners:
                scores[pid] = value / len(winners)
            for pid in losers:
                scores[pid] = -value / len(losers)

        return BidResult(scores=scores)
