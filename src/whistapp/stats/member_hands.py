"""Each club member's own hands in play order, shared by the Rekorder statistics."""

from collections import defaultdict
from fractions import Fraction


def member_hands(data):
    """member id -> [(hand, score change)] for the hands they played, oldest night first.

    Nights are ordered by date, hands within a night by entry; a hand a member sat out
    is not in their list.
    """
    night_order = {sid: i for i, sid in enumerate(data.nights)}
    played = defaultdict(list)
    for h in sorted(data.hands, key=lambda h: (night_order.get(h.session_id, 0), h.id)):
        for pid in h.participants:
            player = data.players.get(pid)
            if player and player.session_id == h.session_id:
                played[player.member_id].append((h, h.scores.get(pid, Fraction())))
    return played
