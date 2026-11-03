"""Trusted server-side bid interface and scoring context."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True)
class BidResult:
    scores: dict[int, Fraction]
    won: bool | None = None
    contract_points: Fraction | None = None


class Bid(ABC):
    key: str
    # Club-configurable values: name -> (Danish label, default). Defaults are the original house rules.
    options: dict = {}

    def option_values(self, chosen=None):
        """Configured values as Fractions, falling back to the defaults."""
        chosen = chosen or {}
        return {
            name: Fraction(str(chosen.get(name, default)))
            for name, (_label, default) in self.options.items()
        }

    @abstractmethod
    def score(
        self,
        config: dict,
        *,
        active: list[int],
        declarer: int,
        target: int = 0,
        actual: int | None = None,
        partner: int | None = None,
        actuals: dict[int, int] | None = None,
    ) -> BidResult:
        """Calculate a complete zero-sum hand using the configured house rules."""
