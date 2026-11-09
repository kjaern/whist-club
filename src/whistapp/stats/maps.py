"""Kort: the members as points on a map.

Spillestil (PCA): a handful of numbers per member about how they play, squeezed onto the
axes they differ most along. Bordkort (MDS): members placed so that those who often sat
side by side end up close together. The page draws them in plain SVG (static/stat-maps.js).
"""

import math
from collections import Counter, defaultdict
from fractions import Fraction

from .base import PARTNERLESS, SPECIAL, pct_text, points_text
from .projection import classical_mds, pca, standardize
from .seating import table_order

MIN_HANDS = 30  # hands a member must have played to get a point on a map
MIN_HANDS_NIGHT = 5

# (key, label, format): the numbers behind Spillestil, all per hand the member played.
FEATURES = [
    ("bids", "Meldelyst", "pct"),
    ("specials", "Sol-lyst", "pct"),
    ("height", "Meldehøjde", "points"),
    ("won_bid", "Sejr som melder", "pct"),
    ("partner", "Makkerrate", "pct"),
    ("won_partner", "Sejr som makker", "pct"),
    ("self", "Selvmakker", "pct"),
    ("points", "Point pr. hånd", "points"),
    ("swing", "Udsving", "points"),
]
ABOUT = {
    "bids": "andel af hænderne, hvor medlemmet meldte",
    "specials": "andel af hænderne med Sol, Ren sol eller Bordlægger",
    "height": "gennemsnitligt antal stik i medlemmets meldinger med makker",
    "won_bid": "vundne af medlemmets afgjorte meldinger",
    "partner": "andel af hænderne som makker for en anden",
    "won_partner": "vundne af de afgjorte hænder som makker",
    "self": "andel af meldingerne med makker, hvor medlemmet var sin egen makker",
    "points": "gennemsnitlig pointændring pr. hånd",
    "swing": "hvor meget pointændringen svinger fra hånd til hånd (standardafvigelse)",
}


def _minimum(data):
    return MIN_HANDS_NIGHT if data.scope.kind == "night" else MIN_HANDS


def _text(value, fmt):
    if value is None:
        return "—"
    return pct_text(value) if fmt == "pct" else points_text(value)


def _scale(coords):
    """Coordinates scaled so the farthest point is 1 from the centre."""
    big = max((abs(x) for c in coords for x in c), default=0) or 1
    return [[round(x / big, 4) for x in c] for c in coords]


def profiles(data):
    """{member id: {feature key: float or None}} for members with enough hands."""
    hands = Counter()
    scores = defaultdict(list)
    count = defaultdict(Counter)
    targets = defaultdict(list)
    for h in data.hands:
        members = {pid: data.member(pid) for pid in h.participants if data.member(pid) is not None}
        for pid, mid in members.items():
            hands[mid] += 1
            scores[mid].append(h.scores.get(pid, Fraction()))
        if h.bid_key in SPECIAL and h.specials:
            for pid, _actual, won in h.specials:
                if pid in members:
                    c = count[members[pid]]
                    c["bids"] += 1; c["specials"] += 1
                    if won is not None:
                        c["decided"] += 1; c["won"] += won
            continue
        if h.bid_key == "paskrig" or h.declarer not in members:
            continue
        mid = members[h.declarer]
        c = count[mid]
        c["bids"] += 1
        if h.won is not None:
            c["decided"] += 1; c["won"] += h.won
        if h.bid_key in PARTNERLESS:
            continue
        c["with_partner"] += 1
        if h.target is not None:
            targets[mid].append(h.target)
        if h.partner == h.declarer:
            c["self"] += 1
        elif h.partner in members:
            p = count[members[h.partner]]
            p["partner"] += 1
            if h.won is not None:
                p["partner_decided"] += 1; p["partner_won"] += h.won

    def share(a, b):
        return a / b if b else None

    result = {}
    for mid, n in hands.items():
        if n < _minimum(data):
            continue
        c, pts = count[mid], [float(s) for s in scores[mid]]
        mean = sum(pts) / n
        result[mid] = dict(
            bids=c["bids"] / n, specials=c["specials"] / n,
            height=share(sum(targets[mid]), len(targets[mid])),
            won_bid=share(c["won"], c["decided"]), partner=c["partner"] / n,
            won_partner=share(c["partner_won"], c["partner_decided"]),
            self=share(c["self"], c["with_partner"]), points=mean,
            swing=math.sqrt(sum((x - mean) ** 2 for x in pts) / n),
        )
    return result, hands


def style_map(data, profiled=None):
    """Spillestil: PCA over the members' numbers (`profiled`: profiles(data), if already made)."""
    base = dict(key="spillestil", title="Spillestil",
                description=("Hvert klubmedlem er et punkt, regnet ud fra ni tal om, hvordan de spiller (se Vis tal). "
                             "Kortet viser de retninger, medlemmerne adskiller sig mest i (PCA): tæt på hinanden "
                             "betyder ens spillestil. Ved hver akse står, hvor stor en del af forskellene den viser, "
                             "og hvilke tal der trækker mest i den. Kun medlemmer med mindst "
                             f"{_minimum(data)} hænder er med."))
    found, hands = profiled or profiles(data)
    mids = sorted(found, key=lambda m: data.name_of(m).casefold())
    if len(mids) < 3:
        return dict(base, points=[], empty="For få medlemmer med nok hænder til et kort (mindst tre).")
    keys = [k for k, _, _ in FEATURES]
    # A member with no value (fx never decided a bid) gets the others' average: no pull either way.
    means = {}
    for k in keys:
        known = [found[m][k] for m in mids if found[m][k] is not None]
        means[k] = sum(known) / len(known) if known else 0.0
    raw = [[found[m][k] if found[m][k] is not None else means[k] for k in keys] for m in mids]
    rows, kept = standardize(raw)
    coords, shares, loadings = pca(rows)
    labels = {k: label for k, label, _ in FEATURES}
    axes = []
    for share, loading in zip(shares, loadings):
        top = sorted(range(len(kept)), key=lambda i: -abs(loading[i]))[:3]
        drivers = [("+" if loading[i] > 0 else "−") + labels[keys[kept[i]]] for i in top if abs(loading[i]) > 0.25]
        axes.append(dict(share=pct_text(share), value=round(share, 4), drivers=", ".join(drivers)))
    points = []
    for mid, xyz in zip(mids, _scale(coords)):
        name = data.name_of(mid)
        detail = " · ".join(f"{label} {_text(found[mid][k], fmt)}" for k, label, fmt in FEATURES)
        points.append(dict(name=name, x=xyz[0], y=xyz[1], z=xyz[2], detail=f"{hands[mid]} hænder · {detail}"))
    table = dict(columns=["Spiller", "Hænder"] + [label for _, label, _ in FEATURES],
                 about=[ABOUT[k] for k in keys],
                 rows=[[data.name_of(m), str(hands[m])] + [_text(found[m][k], fmt) for k, _, fmt in FEATURES]
                       for m in mids])
    return dict(base, points=points, axes=axes, edges=[], table=table)


def seating_map(data):
    """Bordkort: classical MDS where the distance is how many seats apart two members sat.

    Seats apart rather than 'not side by side': two members who always sat together then
    stay half a table apart instead of landing on the same spot."""
    base = dict(key="bordkort", title="Bordkort",
                description=("Medlemmerne placeret, så dem, der ofte sad tæt ved bordet, ligger tæt (MDS). Afstanden "
                             "mellem to er, hvor mange pladser der var imellem dem i snit over deres fælles hænder, "
                             "regnet den korte vej rundt, hvor 1 er lige overfor. Stregerne viser de par, der oftest "
                             "sad lige ved siden af hinanden. Kun aftenens sidste bordplan "
                             "kendes, og importerede aftener bruger rækkefølgen fra arket. Kun medlemmer med mindst "
                             f"{_minimum(data)} hænder er med."))
    hands = Counter()
    together = Counter()
    beside = Counter()
    apart = defaultdict(float)
    for h in data.hands:
        order = table_order(data, h)
        n = len(order)
        if n < 3:
            continue
        members = [data.member(pid) for pid in order]
        for mid in members:
            if mid is not None:
                hands[mid] += 1
        seat = {m: i for i, m in enumerate(members) if m is not None}
        known = sorted(seat)
        for i, a in enumerate(known):
            for b in known[i + 1:]:
                together[a, b] += 1
                steps = abs(seat[a] - seat[b])
                apart[a, b] += min(steps, n - steps) / (n // 2)  # 1 = straight across
        for i, mid in enumerate(members):
            nxt = members[(i + 1) % n]
            if mid is not None and nxt is not None and mid != nxt:
                beside[tuple(sorted((mid, nxt)))] += 1
    mids = sorted((m for m, n in hands.items() if n >= _minimum(data)), key=lambda m: data.name_of(m).casefold())
    if len(mids) < 3:
        return dict(base, points=[], empty="For få medlemmer med nok hænder til et kort (mindst tre).")

    def rate(a, b):
        key = tuple(sorted((a, b)))
        return beside[key] / together[key] if together[key] else 0.0

    def distance(a, b):
        key = tuple(sorted((a, b)))
        return apart[key] / together[key] if together[key] else 1.0  # never together: as far as it gets

    distances = [[0.0 if a == b else distance(a, b) for b in mids] for a in mids]
    coords, shares = classical_mds(distances)
    rates = {(i, j): rate(a, b) for i, a in enumerate(mids) for j, b in enumerate(mids) if i < j}
    top = max(rates.values(), default=0) or 1
    edges = [dict(a=i, b=j, w=round(r / top, 3)) for (i, j), r in rates.items() if r > 0]
    points = []
    for i, (mid, xyz) in enumerate(zip(mids, _scale(coords))):
        near = sorted((m for m in mids if m != mid), key=lambda m: -rate(mid, m))[:3]
        detail = ", ".join(f"{data.name_of(m)} {pct_text(rate(mid, m))}" for m in near)
        points.append(dict(name=data.name_of(mid), x=xyz[0], y=xyz[1], z=xyz[2],
                           detail=f"{hands[mid]} hænder · Oftest ved siden af: {detail}"))
    axes = [dict(share=pct_text(s), value=round(s, 4), drivers="") for s in shares]
    return dict(base, points=points, axes=axes, edges=edges, table=None)


def _field(key, label, fmt, about):
    return dict(key=key, label=label, fmt=fmt, about=about)


def pick_map(data, profiled=None):
    """Vælg selv: two or three of the members' own numbers as the axes, in real units.

    Default: how often a member bids against how often it goes well, so the four corners
    read as styles (bold and good, bold and unlucky, careful and safe, careful and unlucky),
    with points per hand as the third axis: style on two axes, what it pays on the third."""
    base = dict(key="vaelg-selv", title="Vælg selv",
                description=("Vælg to tal om klubmedlemmerne som akser, eller tre i 3D. Som standard: hvor ofte "
                             "man melder mod hvor ofte det lykkes, med point pr. hånd som tredje akse i 3D. "
                             "De stiplede linjer er gennemsnittet af dem på kortet, så hjørnerne kan læses som "
                             f"spillestile. Kun medlemmer med mindst {_minimum(data)} hænder er med."),
                fields=[_field("hands", "Hænder", "int", "hænder spillet")]
                + [_field(k, label, fmt, ABOUT[k]) for k, label, fmt in FEATURES],
                defaults=["bids", "won_bid", "points"])
    found, hands = profiled or profiles(data)
    mids = sorted(found, key=lambda m: data.name_of(m).casefold())
    if len(mids) < 2:
        return dict(base, points=[], empty="For få medlemmer med nok hænder til et kort (mindst to).")
    points = [dict(name=data.name_of(m), values=dict(found[m], hands=hands[m])) for m in mids]
    return dict(base, points=points)


# (key, label, format, explanation) of the numbers for one member with each partner.
PARTNER_FIELDS = [
    ("count", "Hænder sammen", "int", "hænder, hvor I var melder og makker, uanset hvem der meldte"),
    ("share", "Andel af dine makkerhænder", "pct", "andel af dine hænder som melder eller makker med en anden"),
    ("pct", "Sejr % sammen", "pct", "vundne af jeres afgjorte hænder sammen"),
    ("pct_bid", "Sejr % når du meldte", "pct", "vundne, når du meldte med vedkommende som makker"),
    ("pct_partner", "Sejr % når de meldte", "pct", "vundne, når vedkommende meldte med dig som makker"),
    ("points", "Dine point pr. hånd sammen", "points", "din gennemsnitlige pointændring i hænderne sammen"),
]


def partners(data):
    """{member id: {partner member id: {field key: value}}} over hands with a real partner."""
    tally = defaultdict(lambda: defaultdict(Counter))
    points = defaultdict(lambda: defaultdict(Fraction))
    for h in data.hands:
        if h.bid_key in PARTNERLESS or h.partner is None or h.partner == h.declarer or h.declarer is None:
            continue
        d, p = data.member(h.declarer), data.member(h.partner)
        if d is None or p is None or d == p:
            continue
        for me, other, pid, role in ((d, p, h.declarer, "bid"), (p, d, h.partner, "partner")):
            c = tally[me][other]
            c["count"] += 1
            points[me][other] += h.scores.get(pid, Fraction())
            if h.won is not None:
                c["decided"] += 1; c["won"] += h.won
                c[f"{role}_decided"] += 1; c[f"{role}_won"] += h.won

    def share(a, b):
        return a / b if b else None

    result = {}
    for me, others in tally.items():
        total = sum(c["count"] for c in others.values())
        result[me] = {other: dict(count=c["count"], share=c["count"] / total, pct=share(c["won"], c["decided"]),
                                  pct_bid=share(c["bid_won"], c["bid_decided"]),
                                  pct_partner=share(c["partner_won"], c["partner_decided"]),
                                  points=float(points[me][other] / c["count"]))
                      for other, c in others.items()}
    return result


def partner_map(data):
    """Makkere: pick a member; every member they were declarer and partner with is a point.

    Default: how often you played together against how often you won together, with your
    points per hand together as the third axis."""
    base = dict(key="makkere", title="Makkere",
                description=("Vælg en spiller: hvert andet klubmedlem, spilleren har været melder og makker med, er "
                             "et punkt. Som standard: hvor mange hænder I spillede sammen mod hvor ofte I vandt, med "
                             "spillerens point pr. hånd sammen som tredje akse i 3D. Få hænder sammen giver "
                             "tilfældige procenter, så se på antallet."),
                fields=[_field(k, label, fmt, about) for k, label, fmt, about in PARTNER_FIELDS],
                defaults=["count", "pct", "points"])
    found = partners(data)
    groups = [dict(id=me, name=data.name_of(me),
                   points=[dict(name=data.name_of(o), values=v)
                           for o, v in sorted(others.items(), key=lambda item: data.name_of(item[0]).casefold())])
              for me, others in found.items() if len(others) >= 2]
    groups.sort(key=lambda g: g["name"].casefold())
    if not groups:
        return dict(base, groups=[], points=[], empty="Ingen spillere med mindst to forskellige makkere i perioden.")
    return dict(base, groups=groups, points=groups[0]["points"])


def compute(data):
    """Every map for any scope (StatsData). No database access."""
    profiled = profiles(data)
    return [pick_map(data, profiled), partner_map(data), style_map(data, profiled), seating_map(data)]
