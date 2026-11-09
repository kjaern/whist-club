"""Hall of Fame: the club's titles, records and rivalries, computed from real recorded nights.

Shown as the Hall of Fame tab of the statistics pages, for the club's lifetime or a year.
Its awards are not Statistic tables, but like the statistics it never queries the database:
it gets the page's StatsData (stats/data.py) and reuses the existing night results and
placement rules (stats/night_results.py). A year is the statistics pages' year.

Only finished club nights count: is_club=1 and closed (ended_at set). A reopened night is
open again and does not count until it is closed; a deleted night is gone. Imported nights
count where their data allows: final-only imports give placements but no hands, so they are
left out of awards built on hands, bids or running scores.

Everything here is read-only. Scores stay Fractions; floats appear only in display text.
"""

from collections import defaultdict
from dataclasses import dataclass, field, replace
from fractions import Fraction
from itertools import combinations
from math import ceil

from .bids.registry import registered_bids
from .stats.base import PARTNERLESS, pct_text as pct, points_text as points
from .stats.bids_played import attempts
from .stats.member_hands import member_hands
from .stats.night_results import night_totals, place_score, places

# Thresholds; each is shown in its award's explanation.
MIN_KING_NIGHTS = 3  # and a third of the period's nights
MIN_SHARED_NIGHTS = 5  # Nemesis
MIN_BIDS = 10  # Vovehalsen, Den forsigtige
MIN_SECONDS = 3  # Evighedstoeren
MIN_SOL_RATE_ATTEMPTS = 5  # Solkongen's success-rate line
FORM_NIGHTS = 5  # Formbarometer
YOYO_NIGHTS = 5  # Jojo-spilleren
MIN_STREAK = 2  # Længste sejrsrække og taberrække

# An ambitious bid: a trick contract for at least this many tricks, or a solo misère contract.
AMBITIOUS_TRICKS = 10
SOLO_SPECIALS = ("sol", "ren_sol", "bordlaegger")
SOL_BIDS = ("sol", "ren_sol")


# ---------------------------------------------------------------- data

def finished(data):
    """Only the scope's closed nights: a night still open (or reopened) does not count yet."""
    nights = {k: n for k, n in data.nights.items() if n.closed}
    return replace(data, nights=nights, hands=[h for h in data.hands if h.session_id in nights],
                   players={k: p for k, p in data.players.items() if p.session_id in nights})


@dataclass
class NightResult:
    """One finished night as the Hall of Fame sees it."""
    night: object  # NightRecord
    totals: dict  # member id -> Fraction, members who played
    places: dict  # member id -> (label, midpoint place)
    scores: dict  # member id -> place score 0..1 (Placeringsscore), only with two or more members
    running: dict | None  # member id -> [0, total after each of their hands]; None without hand-level data


def night_results(data):
    """Every night with a result for club members, oldest first."""
    totals = night_totals(data)
    # Running scores need the night's hands; a night with imported final scores has none.
    by_night = defaultdict(lambda: defaultdict(list))
    for mid, hands in member_hands(data).items():
        for h, change in hands:
            by_night[h.session_id][mid].append(change)
    results = []
    for sid, night_totals_ in totals.items():
        night = data.nights[sid]
        ranked = places(night_totals_)
        running = None
        if not night.final_scores:
            running = {}
            for mid, changes in by_night[sid].items():
                path, total = [Fraction()], Fraction()
                for change in changes:
                    total += change
                    path.append(total)
                running[mid] = path
        results.append(NightResult(
            night=night, totals=night_totals_, places=ranked,
            scores={mid: s for mid, (_label, place) in ranked.items()
                    if (s := place_score(place, len(night_totals_))) is not None},
            running=running,
        ))
    return results


# ---------------------------------------------------------------- awards

@dataclass
class Holder:
    member_ids: tuple
    name: str
    value: object  # the raw ranking value (Fraction/int/tuple), for tests and comparison
    value_text: str
    detail: str = ""
    link: str | None = None
    link_text: str = ""


@dataclass
class Award:
    key: str
    icon: str
    title: str
    tagline: str
    explanation: str
    empty: str
    holders: list = field(default_factory=list)
    extra: list = field(default_factory=list)  # further lines, fx a runner-up or a best rate

    @property
    def vacant(self):
        return not self.holders

    @property
    def shared(self):
        return len(self.holders) > 1


def signed(value):
    return ("+" if value > 0 else "") + points(value)


def pp(value):
    """A difference of two percentages, in procentpoint."""
    return pct(value).removesuffix(" %") + " procentpoint"


def night_label(night):
    return night.date if night.title in ("", night.date) else f"{night.date} · {night.title}"


def night_link(night):
    return f"/sessions/{night.id}"


def leaders(candidates, key):
    """Every candidate sharing the best key (highest first). Never breaks a tie by itself."""
    if not candidates:
        return []
    best = max(key(c) for c in candidates)
    return [c for c in candidates if key(c) == best]


def _name_sorted(data, mids):
    return sorted(mids, key=lambda m: data.name_of(m).casefold())


def appearances(data, results):
    """member id -> [(NightResult, place score)] for the nights they played with two or more members.

    Only nights the member played: an absence is neither a loss nor a zero.
    """
    seen = defaultdict(list)
    for r in results:
        for mid, score in r.scores.items():
            seen[mid].append((r, score))
    return seen


def is_win(label):
    return label == "1"


def has_first(label):
    return label.split("/")[0] == "1"


def is_loss(label, members):
    """Alone on last place of a night with this many members; a shared last place is not a loss."""
    return label == str(members)


def king(data, results, year, period=""):
    title = "Periodens konge" if period else "Tidernes konge" if year is None else "Årets konge"
    award = Award(
        "konge", "👑", title, "Kronen går til den, der oftest ender foran de andre.",
        "Gennemsnitlig placeringsscore over aftenerne, som på Placeringer: hver aften er en lille kamp mod "
        "hver anden spiller (foran = 1, lige = ½, bagved = 0), så 100 % er alene først og 0 % alene sidst, "
        "uanset hvor mange der spillede. Kun aftener man spillede tæller. Kræver mindst "
        f"{MIN_KING_NIGHTS} aftener og mindst en tredjedel af periodens aftener. Lige score deler titlen.",
        "Ingen har endnu spillet nok aftener til at bære kronen.")
    nights = sum(1 for r in results if r.scores)
    need = max(MIN_KING_NIGHTS, ceil(Fraction(nights, 3)))
    rows = [(mid, sum((s for _r, s in seen), Fraction()) / len(seen), len(seen))
            for mid, seen in appearances(data, results).items() if len(seen) >= need]
    top = leaders(rows, lambda r: r[1])
    others = [r for r in rows if r not in top]
    margin = top[0][1] - max(r[1] for r in others) if top and others else None
    for mid, score, count in sorted(top, key=lambda r: data.name_of(r[0]).casefold()):
        detail = f"{count} aftener"
        if margin is not None:
            detail += f" · {pp(margin)} foran nr. 2"
        award.holders.append(Holder((mid,), data.name_of(mid), score, pct(score), detail))
    award.extra.append(f"Kræver mindst {need} aftener i perioden.")
    return award


def nemesis(data, results):
    award = Award(
        "nemesis", "⚔️", "Nemesis", "Nogle modstandere er bare sværere end andre.",
        "Beregnes ud fra aftener, hvor begge spillere deltog: hvem endte med det højeste resultat. "
        f"Mindst {MIN_SHARED_NIGHTS} fælles aftener kræves. Titlen går til det mest ensidige opgør: "
        "den højeste andel af de fælles aftener, hvor den ene endte foran den anden; ved lige andel det "
        "opgør med flest sejre. Lige resultat på en aften tæller for ingen af dem.",
        f"Der skal mindst {MIN_SHARED_NIGHTS} fælles spilleaftener til for at finde en Nemesis.")
    pairs = defaultdict(lambda: [0, 0, 0])  # (a, b) with a < b -> [a ahead, b ahead, ties]
    for r in results:
        for a, b in combinations(sorted(r.totals), 2):
            ta, tb = r.totals[a], r.totals[b]
            pairs[(a, b)][0 if ta > tb else 1 if tb > ta else 2] += 1
    rivalries = []
    for (a, b), (a_ahead, b_ahead, ties) in pairs.items():
        shared = a_ahead + b_ahead + ties
        if shared < MIN_SHARED_NIGHTS or a_ahead == b_ahead:
            continue
        winner, loser, ahead, behind = (a, b, a_ahead, b_ahead) if a_ahead > b_ahead else (b, a, b_ahead, a_ahead)
        rivalries.append((winner, loser, ahead, behind, ties, shared))
    top = leaders(rivalries, lambda r: (Fraction(r[2], r[5]), r[2]))
    for winner, loser, ahead, behind, ties, shared in sorted(
            top, key=lambda r: (data.name_of(r[1]).casefold(), data.name_of(r[0]).casefold())):
        award.holders.append(Holder(
            (winner, loser), f"{data.name_of(winner)} mod {data.name_of(loser)}", (Fraction(ahead, shared), ahead),
            f"{ahead} af {shared}",
            f"{data.name_of(winner)} har slået {data.name_of(loser)} {ahead} gange på {shared} fælles aftener "
            f"({behind} gange omvendt, {ties} uafgjort)."))
    return award


def _ambitious(h):
    bid = registered_bids()[h.bid_key]
    return h.bid_key in SOLO_SPECIALS or (bid.target == "variable" and (h.target or 0) >= AMBITIOUS_TRICKS)


def bid_counts(data):
    """member id -> (ambitious bids, all bids). Each Sol/Ren sol/Bordlægger bidder makes their own bid."""
    counts = defaultdict(lambda: [0, 0])
    bids = registered_bids()
    for h in data.hands:
        if h.bid_key not in bids:
            continue
        for pid, _won in attempts(h):  # Påskrig has no bidder
            if (mid := data.member(pid)) is not None:
                counts[mid][1] += 1
                counts[mid][0] += _ambitious(h)
    return counts


AMBITIOUS_TEXT = (f"En vovet melding er en stikmelding på mindst {AMBITIOUS_TRICKS} stik (Almindelige, Halve, VIP, "
                  "Sans eller Gode) eller en Sol, Ren sol eller Bordlægger, hvor man spiller alene mod tre. "
                  "Sol m.fl. tæller hver melders eget bud; Påskrig er ingens melding. "
                  f"Kræver mindst {MIN_BIDS} egne meldinger.")


def daredevil(data, counts, careful=False):
    if careful:
        award = Award("forsigtige", "🎯", "Den forsigtige", "Hvorfor satse stort, når man kan spille sikkert?",
                      "Laveste andel vovede meldinger blandt egne meldinger. Siger intet om, hvem der spiller "
                      "bedst. " + AMBITIOUS_TEXT,
                      f"Ingen har endnu meldt de {MIN_BIDS} gange, der skal til.")
    else:
        award = Award("vovehals", "🎲", "Vovehalsen", "Lever efter mottoet: ti stik eller intet.",
                      "Højeste andel vovede meldinger blandt egne meldinger. " + AMBITIOUS_TEXT,
                      "Ingen har endnu vovet nok til at kalde sig Vovehals.")
    rows = [(mid, Fraction(a, n), a, n) for mid, (a, n) in counts.items() if n >= MIN_BIDS]
    if not careful:
        rows = [r for r in rows if r[2]]
    top = leaders(rows, lambda r: -r[1] if careful else r[1])
    for mid, share, a, n in sorted(top, key=lambda r: data.name_of(r[0]).casefold()):
        award.holders.append(Holder((mid,), data.name_of(mid), share, pct(share), f"{a} vovede af {n} meldinger"))
    return award


def swings(results, comeback=True):
    """(NightResult, member id, low or high, final, change) for every member's run on every night."""
    rows = []
    for r in results:
        for mid, path in (r.running or {}).items():
            final = path[-1]
            if comeback:
                low = min(path)
                rows.append((r, mid, low, final, final - low))
            else:
                high = max(path)
                rows.append((r, mid, high, final, high - final))
    return [row for row in rows if row[4] > 0]


def comeback(data, results, rise=True):
    if rise:
        award = Award("comeback", "🚀", "Det store comeback", "Det er ikke slut, før de sidste kort er lagt.",
                      "Største forbedring fra en spillers laveste stilling til slutresultatet på samme aften. "
                      "Stillingen starter i 0 og følger hånd for hånd. Kræver aftenens hænder; importerede "
                      "aftener med kun slutresultater tæller ikke.",
                      "Vi mangler endnu et legendarisk comeback.")
    else:
        award = Award("nedtur", "📉", "Den store nedtur", "Højt at flyve, dybt at falde. Det sker for de bedste.",
                      "Største fald fra en spillers højeste stilling til slutresultatet på samme aften. "
                      "Stillingen starter i 0 og følger hånd for hånd. Kræver aftenens hænder; importerede "
                      "aftener med kun slutresultater tæller ikke.",
                      "Ingen store nedture endnu. Nyd det, mens det varer.")
    top = leaders(swings(results, rise), lambda row: row[4])
    for r, mid, turn, final, change in sorted(top, key=lambda row: (row[0].night.date, data.name_of(row[1]).casefold())):
        word = "fra laveste" if rise else "fra højeste"
        award.holders.append(Holder(
            (mid,), data.name_of(mid), change, signed(change) if rise else signed(-change),
            f"{word} {signed(turn)} til {signed(final)} point", night_link(r.night), night_label(r.night)))
    return award


def eternal_second(data, results):
    award = Award("toer", "🥈", "Evighedstoeren", "Altid tæt på. Aldrig helt.",
                  "Flest aftener alene på 2.-pladsen. Spillere, der ikke har været nr. 1 (heller ikke delt) i "
                  f"perioden, går forud. Kræver mindst {MIN_SECONDS} andenpladser; en delt 2.-plads tæller ikke.",
                  "Titlen er ledig. Ingen har endnu været toer nok gange.")
    seconds, firsts = defaultdict(int), defaultdict(int)
    for mid, seen in appearances(data, results).items():
        for r, _s in seen:
            label = r.places[mid][0]
            seconds[mid] += label == "2"
            firsts[mid] += has_first(label)
    rows = [(mid, n, firsts[mid]) for mid, n in seconds.items() if n >= MIN_SECONDS]
    top = leaders(rows, lambda r: (r[2] == 0, r[1]))
    for mid, n, wins in sorted(top, key=lambda r: data.name_of(r[0]).casefold()):
        detail = "aldrig nr. 1 i perioden" if not wins else f"har dog også været nr. 1 {wins} gange"
        award.holders.append(Holder((mid,), data.name_of(mid), n, f"{n} × nr. 2", detail))
    return award


def kingmaker(data):
    award = Award("kongemager", "🤝", "Kongemageren", "Bag enhver stor melder står en makker, der leverer.",
                  "Flest vundne meldinger som makker (ikke melder). Kun meldinger med makker: Sol, Ren sol, "
                  "Bordlægger og Påskrig tæller ikke, og heller ikke når melderen selv var makker. Hænder "
                  "uden registreret makker eller udfald tæller ikke.",
                  "Ingen makker har endnu hjulpet en melder hjem.")
    counts = defaultdict(lambda: [0, 0])
    for h in data.hands:
        if h.bid_key in PARTNERLESS or h.partner is None or h.partner == h.declarer or h.won is None:
            continue
        if (mid := data.member(h.partner)) is not None:
            counts[mid][1] += 1
            counts[mid][0] += bool(h.won)
    rows = [(mid, won, total) for mid, (won, total) in counts.items() if won]
    top = leaders(rows, lambda r: r[1])
    for mid, won, total in sorted(top, key=lambda r: data.name_of(r[0]).casefold()):
        award.holders.append(Holder((mid,), data.name_of(mid), won, f"{won} vundne",
                                    f"{won} af {total} meldinger som makker"))
    return award


def sol_counts(data):
    """member id -> {sol: won, ren_sol: won, attempts}; only attempts with a recorded outcome."""
    counts = defaultdict(lambda: dict(sol=0, ren_sol=0, attempts=0))
    for h in data.hands:
        if h.bid_key not in SOL_BIDS:
            continue
        for pid, won in attempts(h):
            if won is None or (mid := data.member(pid)) is None:
                continue
            counts[mid]["attempts"] += 1
            counts[mid][h.bid_key] += bool(won)
    return counts


def sun_king(data):
    award = Award("solkonge", "☀️", "Solkongen", "Når man har styr på de små kort.",
                  "Flest vundne Sol- og Ren sol-meldinger i perioden; hver melder tæller sit eget forsøg. "
                  "Ved lige antal vinder den med færrest forsøg (højeste succesrate); er det også lige, "
                  f"deles titlen. Succesraten sammenlignes kun for spillere med mindst {MIN_SOL_RATE_ATTEMPTS} "
                  "forsøg. Forsøg uden registreret udfald tæller ikke.",
                  "Solen er endnu ikke stået op over nogen.")
    counts = sol_counts(data)
    rows = [(mid, c["sol"] + c["ren_sol"], c) for mid, c in counts.items() if c["sol"] + c["ren_sol"]]
    top = leaders(rows, lambda r: (r[1], -r[2]["attempts"]))
    for mid, won, c in sorted(top, key=lambda r: data.name_of(r[0]).casefold()):
        award.holders.append(Holder(
            (mid,), data.name_of(mid), won, f"{won} vundne",
            f"{c['sol']} Sol og {c['ren_sol']} Ren sol af {c['attempts']} forsøg "
            f"({pct(Fraction(won, c['attempts']))})"))
    rated = [(mid, Fraction(c["sol"] + c["ren_sol"], c["attempts"]), c) for mid, c in counts.items()
             if c["attempts"] >= MIN_SOL_RATE_ATTEMPTS]
    best = leaders(rated, lambda r: r[1])
    if best:
        rate = {mid: c for mid, _r, c in best}
        names = " og ".join(f"{data.name_of(m)} ({rate[m]['sol'] + rate[m]['ren_sol']} af {rate[m]['attempts']})"
                            for m in _name_sorted(data, rate))
        award.extra.append(f"Bedste succesrate (mindst {MIN_SOL_RATE_ATTEMPTS} forsøg): {pct(best[0][1])}, {names}.")
    return award


def yoyo(data, results):
    award = Award("jojo", "🎢", "Jojo-spilleren", "Oppe at ringe den ene uge, nede med nakken den næste.",
                  "Største gennemsnitlige spring i placeringsscore mellem to aftener i træk, som spilleren "
                  f"deltog i. Kræver mindst {YOYO_NIGHTS} aftener. Spilleren vises med sit største enkeltspring "
                  "(ved lige spring det seneste). Placeringsscoren er sammenlignelig uanset antal spillere og "
                  "hænder; fravær er ikke et nul.",
                  f"Ingen har endnu spillet de {YOYO_NIGHTS} aftener, der skal til for at svinge.")
    rows = []
    for mid, seen in appearances(data, results).items():
        if len(seen) < YOYO_NIGHTS:
            continue
        steps = [(abs(s2 - s1), r1, s1, r2, s2) for (r1, s1), (r2, s2) in zip(seen, seen[1:])]
        average = sum((step[0] for step in steps), Fraction()) / len(steps)
        biggest = max(steps, key=lambda step: step[0])
        biggest = [step for step in steps if step[0] == biggest[0]][-1]
        if average:
            rows.append((mid, average, biggest))
    for mid, average, (swing, r1, s1, r2, s2) in sorted(leaders(rows, lambda row: row[1]),
                                                        key=lambda row: data.name_of(row[0]).casefold()):
        direction = "op" if s2 > s1 else "ned"
        award.holders.append(Holder(
            (mid,), data.name_of(mid), average, pp(average) + " i snit",
            f"Største spring: {direction} {pct(s1)} ({r1.night.date}) → {pct(s2)} ({r2.night.date})",
            night_link(r2.night), night_label(r2.night)))
    return award


def form(data, results):
    award = Award("form", "🌡️", "Formbarometer", "Hvem er varm lige nu?",
                  f"Gennemsnitlig placeringsscore over spillerens seneste {FORM_NIGHTS} aftener i perioden. "
                  f"Kræver {FORM_NIGHTS} aftener. Placeringsscoren er sammenlignelig uanset antal spillere og hænder.",
                  f"Ingen har endnu spillet {FORM_NIGHTS} aftener i perioden.")
    rows = []
    for mid, seen in appearances(data, results).items():
        if len(seen) >= FORM_NIGHTS:
            last = seen[-FORM_NIGHTS:]
            rows.append((mid, sum((s for _r, s in last), Fraction()) / FORM_NIGHTS, last))
    top = leaders(rows, lambda r: r[1])
    for mid, score, last in sorted(top, key=lambda r: data.name_of(r[0]).casefold()):
        award.holders.append(Holder(
            (mid,), data.name_of(mid), score, pct(score),
            "Seneste placeringer: " + " · ".join(f"nr. {r.places[mid][0]}" for r, _s in last)))
    return award


def most_wins(data, results):
    award = Award("sejre", "🏅", "Flest førstepladser", "Samler på sejre som andre samler på frimærker.",
                  "Flest aftener alene på 1.-pladsen efter aftenens resultat. En delt førsteplads (fx 1/2) er "
                  "ikke en sejr, men vises for sig. Lige antal sejre deler titlen.",
                  "Ingen har endnu vundet en aften alene.")
    wins, shared, played = defaultdict(int), defaultdict(int), defaultdict(int)
    for mid, seen in appearances(data, results).items():
        for r, _s in seen:
            label = r.places[mid][0]
            played[mid] += 1
            wins[mid] += is_win(label)
            shared[mid] += has_first(label) and not is_win(label)
    rows = [(mid, n) for mid, n in wins.items() if n]
    for mid, n in sorted(leaders(rows, lambda r: r[1]), key=lambda r: data.name_of(r[0]).casefold()):
        detail = f"{n} af {played[mid]} aftener ({pct(Fraction(n, played[mid]))})"
        if shared[mid]:
            detail += f" · {shared[mid]} delte førstepladser"
        award.holders.append(Holder((mid,), data.name_of(mid), n, f"{n} sejre", detail))
    return award


def streaks(data, award, sequences, hit, night_of, unit, points_of=None):
    """Fill the award with the longest run of items where hit(item) holds, one per member.

    sequences: member id -> their items in play order (nights or hands they played), so an
    absence or a hand sat out does not break a run; an item without a hit does. A member's
    run is their first longest one. points_of(item), if given, adds the run's points.
    """
    rows = []
    for mid, items in sequences.items():
        best, run = [], []
        for item in items + [None]:
            if item is not None and hit(item):
                run.append(item)
                continue
            if len(run) > len(best):
                best = run
            run = []
        if len(best) >= MIN_STREAK:
            rows.append((mid, len(best), night_of(best[0]), night_of(best[-1]), best))
    for mid, n, first, last, run in sorted(leaders(rows, lambda r: r[1]),
                                           key=lambda r: (r[2].date, data.name_of(r[0]).casefold())):
        detail = f"fra {first.date} til {last.date}" if first.id != last.id else f"aftenen {first.date}"
        if points_of:
            detail += f" · {signed(sum((points_of(i) for i in run), Fraction()))} point"
        award.holders.append(Holder((mid,), data.name_of(mid), n, f"{n} {unit} i træk", detail,
                                    night_link(last), night_label(last)))
    return award


def night_streak(data, results, award, hit):
    seen = {mid: [(r, mid) for r, _s in nights] for mid, nights in appearances(data, results).items()}
    return streaks(data, award, seen, lambda item: hit(*item), lambda item: item[0].night, "aftener")


def hand_streak(data, award, sign):
    return streaks(data, award, member_hands(data), lambda item: item[1] * sign > 0,
                   lambda item: data.nights[item[0].session_id], "hænder", lambda item: item[1])


HAND_STREAK_RULE = ("på tværs af aftener i datoorden. En hånd uden ændring bryder rækken; en hånd man sidder over "
                    "gør ikke. Samme regel som Vinder- og taberrækker under Rekorder.")


def win_streak(data, results):
    award = Award("sejrsraekke", "🔥", "Længste sejrsrække (aftener)", "Når først man er i gang …",
                  "Flest sejre i træk (alene på 1.-pladsen) over de aftener, spilleren deltog i. Aftener man ikke "
                  "spillede bryder ikke rækken; en aften man ikke vandt (også en delt førsteplads) gør. "
                  f"Kræver mindst {MIN_STREAK} sejre i træk.",
                  "Ingen har endnu vundet to aftener i træk.")
    return night_streak(data, results, award, lambda r, mid: is_win(r.places[mid][0]))


def lose_streak(data, results):
    award = Award("taberraekke", "🌧️", "Længste taberrække (aftener)", "Når først det går skævt …",
                  "Flest sidstepladser i træk (alene på sidstepladsen) over de aftener, spilleren deltog i. Aftener "
                  "man ikke spillede bryder ikke rækken; en aften man ikke blev sidst (også en delt sidsteplads) gør. "
                  f"Kræver mindst {MIN_STREAK} sidstepladser i træk.",
                  "Ingen har endnu været sidst to aftener i træk.")
    return night_streak(data, results, award, lambda r, mid: is_loss(r.places[mid][0], len(r.totals)))


def hand_win_streak(data):
    award = Award("sejrsraekke_haender", "⚡", "Længste sejrsrække (hænder)", "Kortene vil bare det hele.",
                  f"Flest hænder i træk med gevinst, {HAND_STREAK_RULE} Kræver mindst {MIN_STREAK} hænder i træk.",
                  "Ingen har endnu vundet to hænder i træk.")
    return hand_streak(data, award, 1)


def hand_lose_streak(data):
    award = Award("taberraekke_haender", "🧊", "Længste taberrække (hænder)", "Kan man blive ved med at tabe?",
                  f"Flest hænder i træk med tab, {HAND_STREAK_RULE} Kræver mindst {MIN_STREAK} hænder i træk.",
                  "Ingen har endnu tabt to hænder i træk.")
    return hand_streak(data, award, -1)


# Klubrekorder: (award key, what the record is), shown from the awards themselves.
RECORDS = [
    ("sejre", "Flest førstepladser"),
    ("sejrsraekke", "Længste sejrsrække (aftener)"),
    ("sejrsraekke_haender", "Længste sejrsrække (hænder)"),
    ("taberraekke", "Længste taberrække (aftener)"),
    ("taberraekke_haender", "Længste taberrække (hænder)"),
    ("comeback", "Største comeback"),
    ("nedtur", "Største nedtur"),
    ("solkonge", "Flest vundne Sol og Ren sol"),
    ("kongemager", "Flest vundne meldinger som makker"),
]


@dataclass
class HallOfFame:
    year: int | None  # None: the club's lifetime
    nights: int  # finished nights with results in the period
    awards: list
    records: list  # (label, Award)
    period: str = ""  # a period between two dates, as Danish text

    def award(self, key):
        return next(a for a in self.awards if a.key == key)


def compute(data):
    """Every award for a loaded lifetime, year or period scope (StatsData). No database access."""
    year = data.scope.year if data.scope.kind == "year" else None
    period = data.scope.period_text() if data.scope.kind == "period" else ""
    data = finished(data)
    results = night_results(data)
    counts = bid_counts(data)
    awards = [
        king(data, results, year, period),
        most_wins(data, results),
        win_streak(data, results),
        lose_streak(data, results),
        hand_win_streak(data),
        hand_lose_streak(data),
        form(data, results),
        nemesis(data, results),
        eternal_second(data, results),
        comeback(data, results),
        comeback(data, results, rise=False),
        sun_king(data),
        kingmaker(data),
        daredevil(data, counts),
        daredevil(data, counts, careful=True),
        yoyo(data, results),
    ]
    by_key = {a.key: a for a in awards}
    return HallOfFame(year, len(results), awards, [(label, by_key[key]) for key, label in RECORDS], period)

