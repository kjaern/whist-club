"""Each night's result per club member, and the places it gives; shared by Placeringer."""

from collections import defaultdict
from fractions import Fraction

from .member_hands import member_hands


def night_totals(data):
    """night id -> {member id: total} for the members who played that night, oldest first.

    Hand score changes plus imported final scores, as on the night's own result.
    """
    totals = defaultdict(dict)
    for mid, hands in member_hands(data).items():
        for h, change in hands:
            totals[h.session_id][mid] = totals[h.session_id].get(mid, Fraction()) + change
    for night in data.nights.values():
        for pid, score in night.final_scores.items():
            if (player := data.players.get(pid)) and player.session_id == night.id:
                totals[night.id][player.member_id] = totals[night.id].get(player.member_id, Fraction()) + score
    return {sid: totals[sid] for sid in data.nights if sid in totals}


def place_score(place, players):
    """A night's place as a share from 0 (alone last) to 1 (alone first), at any table size.

    One match per other player: ahead = 1, behind = 0, equal result = a draw worth ½.
    With `place` the midpoint place from places(), that share is (n - place) / (n - 1):
    two tied for first of five get (3 + ½) / 4 = 7/8, the average of 1st and 2nd.
    None when the member played alone.
    """
    return (players - place) / Fraction(players - 1) if players > 1 else None


def places(totals):
    """member id -> (label, place) ranked by total, highest first.

    Equal totals share their places, written like the old score book: two members
    tied for first are both "1/2", and their place counts as 1.5.
    """
    ranked = sorted(totals, key=lambda mid: -totals[mid])
    result, i = {}, 0
    while i < len(ranked):
        j = i + 1
        while j < len(ranked) and totals[ranked[j]] == totals[ranked[i]]:
            j += 1
        label = str(i + 1) if j == i + 1 else "/".join(str(n) for n in range(i + 1, j + 1))
        for mid in ranked[i:j]:
            result[mid] = (label, Fraction(i + 1 + j, 2))
        i = j
    return result
