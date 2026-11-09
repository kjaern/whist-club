"""Squeeze many numbers per member into a few map coordinates, in plain Python.

PCA starts from a row of numbers per member and finds the directions they differ most in.
Classical MDS starts from a distance between every pair and finds points that keep those
distances. Both end in the eigenvectors of a small symmetric matrix, found by Jacobi
rotations. Floats are fine here: the maps are only drawn, never scored.
"""

import math


def eigh(matrix, sweeps=100):
    """Eigenvalues and eigenvectors of a symmetric matrix, largest eigenvalue first.

    Each eigenvector is a list, its largest component made positive so maps don't flip
    between page loads."""
    n = len(matrix)
    a = [list(map(float, row)) for row in matrix]
    v = [[float(i == j) for j in range(n)] for i in range(n)]
    for _ in range(sweeps):
        if sum(a[i][j] ** 2 for i in range(n) for j in range(i + 1, n)) < 1e-22:
            break
        for p in range(n):
            for q in range(p + 1, n):
                if abs(a[p][q]) < 1e-30:
                    continue
                theta = (a[q][q] - a[p][p]) / (2 * a[p][q])
                t = math.copysign(1, theta) / (abs(theta) + math.sqrt(theta * theta + 1))
                c = 1 / math.sqrt(t * t + 1)
                s = t * c
                for k in range(n):  # columns p and q
                    akp, akq = a[k][p], a[k][q]
                    a[k][p], a[k][q] = c * akp - s * akq, s * akp + c * akq
                for k in range(n):  # rows p and q
                    apk, aqk = a[p][k], a[q][k]
                    a[p][k], a[q][k] = c * apk - s * aqk, s * apk + c * aqk
                for k in range(n):
                    vkp, vkq = v[k][p], v[k][q]
                    v[k][p], v[k][q] = c * vkp - s * vkq, s * vkp + c * vkq
    pairs = []
    for i in range(n):
        vec = [v[k][i] for k in range(n)]
        if max(vec, key=abs) < 0:
            vec = [-x for x in vec]
        pairs.append((a[i][i], vec))
    pairs.sort(key=lambda pair: -pair[0])
    return [p[0] for p in pairs], [p[1] for p in pairs]


def standardize(rows):
    """Each column to mean 0 and spread 1; a column where everyone is equal is dropped.
    Returns the new rows and the indexes of the columns kept."""
    m = len(rows)
    kept, columns = [], []
    for j in range(len(rows[0]) if rows else 0):
        col = [r[j] for r in rows]
        mean = sum(col) / m
        sd = math.sqrt(sum((x - mean) ** 2 for x in col) / m)
        if sd > 1e-12:
            kept.append(j)
            columns.append([(x - mean) / sd for x in col])
    return [list(r) for r in zip(*columns)] if columns else [[] for _ in rows], kept


def pca(rows, dims=3):
    """(coordinates per row, share of the spread per axis, loadings per axis) of standardized rows."""
    m, d = len(rows), len(rows[0]) if rows else 0
    if m < 2 or d == 0:
        return [[0.0] * dims for _ in rows], [0.0] * dims, [[0.0] * d for _ in range(dims)]
    cov = [[sum(r[i] * r[j] for r in rows) / m for j in range(d)] for i in range(d)]
    values, vectors = eigh(cov)
    total = sum(max(x, 0) for x in values) or 1
    coords = [[sum(r[i] * vectors[k][i] for i in range(d)) if k < d else 0.0 for k in range(dims)] for r in rows]
    shares = [max(values[k], 0) / total if k < d else 0.0 for k in range(dims)]
    loadings = [vectors[k] if k < d else [0.0] * d for k in range(dims)]
    return coords, shares, loadings


def classical_mds(distances, dims=3):
    """(coordinates per point, share of the spread per axis) from a symmetric distance matrix."""
    m = len(distances)
    if m < 2:
        return [[0.0] * dims for _ in range(m)], [0.0] * dims
    sq = [[d * d for d in row] for row in distances]
    row_mean = [sum(r) / m for r in sq]
    all_mean = sum(row_mean) / m
    b = [[-0.5 * (sq[i][j] - row_mean[i] - row_mean[j] + all_mean) for j in range(m)] for i in range(m)]
    values, vectors = eigh(b)
    total = sum(x for x in values if x > 0) or 1
    coords = [[0.0] * dims for _ in range(m)]
    shares = [0.0] * dims
    for k in range(min(dims, m)):
        if values[k] <= 1e-12:
            continue
        scale = math.sqrt(values[k])
        shares[k] = values[k] / total
        for i in range(m):
            coords[i][k] = vectors[k][i] * scale
    return coords, shares
