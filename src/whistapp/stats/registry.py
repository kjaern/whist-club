"""The statistics shown, grouped into topic tabs in this order.

To add a statistic: write a Statistic subclass in its own file and list it here.
"""

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
]


def topics_for(kind):
    """Topics with at least one statistic that makes sense for the scope kind."""
    return [t for t in TOPICS if any(kind in s.scopes for s in t.statistics)]


def topic_tables(topic, data):
    return [(s, s.compute(data)) for s in topic.statistics if data.scope.kind in s.scopes]
