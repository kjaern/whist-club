"""The statistics shown, grouped into topic tabs in this order.

To add a statistic: write a Statistic subclass in its own file and list it here.
"""

from .. import hall_of_fame
from .base import Topic
from .bids_by_member import BidsByMember
from .bids_played import BidsPlayed
from .club_rhythm import ClubRhythm
from .extremes import Extremes
from .streaks import Streaks
from .member_specials import MemberSpecials
from .night_grid import NightGrid
from .overview import Overview
from .partner_pairs import PartnerPairs
from .placements import Placements
from .self_partner import SelfPartner
from .special_multi import SpecialMulti

TOPICS = [
    Topic("overblik", "Overblik", [Overview()]),
    Topic("meldinger", "Meldinger", [BidsPlayed(), BidsByMember(), SpecialMulti()]),
    Topic("makkerpar", "Makkerpar", [PartnerPairs(), SelfPartner(), MemberSpecials()]),
    Topic("rekorder", "Rekorder", [Streaks(), Extremes(), ClubRhythm()]),
    Topic("placeringer", "Placeringer", [Placements(), NightGrid()]),
    # Club titles and records, computed by hall_of_fame.py rather than as tables.
    Topic("hall-of-fame", "🏆 Hall of Fame", scopes=("all", "year", "period"),
          page="partials/hall_of_fame.html", build=hall_of_fame.compute),
]


def statistic(key):
    """The registered statistic with this key, or None."""
    return next((s for t in TOPICS for s in t.statistics if s.key == key), None)


def rule_sources(stat):
    """(module name, source) for the statistic's own file, then the statistics helpers it uses.

    The shared plumbing in base.py (tables, columns, formatting) is left out; it computes nothing.
    """
    import inspect
    import sys

    own = sys.modules[type(stat).__module__]
    used = []
    for value in vars(own).values():
        module = getattr(value, "__module__", "") or ""
        if (module.startswith("whistapp.stats.") and module not in (own.__name__, "whistapp.stats.base")
                and module not in used):
            used.append(module)
    return [(m.rsplit(".", 1)[-1] + ".py", inspect.getsource(sys.modules[m])) for m in [own.__name__, *used]]


def topics_for(kind):
    """Topics with a page or at least one statistic that makes sense for the scope kind."""
    return [t for t in TOPICS if kind in t.scopes or any(kind in s.scopes for s in t.statistics)]


def topic_tables(topic, data):
    return [(s, s.compute(data)) for s in topic.statistics if data.scope.kind in s.scopes]
