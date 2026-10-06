"""Deterministic NSGA-II: rank/crowding tournaments, SBX, polynomial mutation.

All objectives are minimized. Returns an approximation, never a proof of optimum.
"""

import numpy as np


def fronts(values):
    values = np.asarray(values, dtype=float)
    dominates = (values[:, None, :] <= values[None, :, :]).all(axis=2) & (values[:, None, :] < values[None, :, :]).any(
        axis=2
    )
    counts = dominates.sum(axis=0)
    remaining = np.ones(len(values), dtype=bool)
    result = []
    while remaining.any():
        front = np.flatnonzero((counts == 0) & remaining)
        if not len(front):
            raise ValueError("Non-finite objectives or invalid dominance graph")
        result.append(front)
        remaining[front] = False
        counts -= dominates[front].sum(axis=0)
    return result


def crowding(values):
    values = np.asarray(values, dtype=float)
    distance = np.zeros(len(values))
    if len(values) <= 2:
        return np.full(len(values), np.inf)
    for column in range(values.shape[1]):
        order = np.argsort(values[:, column], kind="stable")
        spread = values[order[-1], column] - values[order[0], column]
        if spread <= 0:
            continue
        distance[order[0]] = distance[order[-1]] = np.inf
        distance[order[1:-1]] += (values[order[2:], column] - values[order[:-2], column]) / spread
    return distance


def ranks_and_distances(values):
    ranks = np.empty(len(values), dtype=int)
    distances = np.zeros(len(values))
    for rank, front in enumerate(fronts(values)):
        ranks[front] = rank
        distances[front] = crowding(values[front])
    return ranks, distances


def sbx(a, b, rng, eta=15):
    """Bounded simulated binary crossover on normalized [0, 1] variables."""
    child1, child2 = a.copy(), b.copy()
    if rng.random() > 0.9:
        return child1, child2
    for j in range(len(a)):
        if rng.random() > 0.5 or abs(a[j] - b[j]) < 1e-14:
            continue
        y1, y2 = sorted((a[j], b[j]))
        random = rng.random()
        beta = 1 + 2 * y1 / (y2 - y1)
        alpha = 2 - beta ** (-(eta + 1))
        betaq = (
            (random * alpha) ** (1 / (eta + 1))
            if random <= 1 / alpha
            else (1 / (2 - random * alpha)) ** (1 / (eta + 1))
        )
        c1 = 0.5 * ((y1 + y2) - betaq * (y2 - y1))
        beta = 1 + 2 * (1 - y2) / (y2 - y1)
        alpha = 2 - beta ** (-(eta + 1))
        betaq = (
            (random * alpha) ** (1 / (eta + 1))
            if random <= 1 / alpha
            else (1 / (2 - random * alpha)) ** (1 / (eta + 1))
        )
        c2 = 0.5 * ((y1 + y2) + betaq * (y2 - y1))
        if rng.random() < 0.5:
            c1, c2 = c2, c1
        child1[j], child2[j] = np.clip(c1, 0, 1), np.clip(c2, 0, 1)
    return child1, child2


def mutate(child, rng, eta=20):
    for j, y in enumerate(child):
        if rng.random() >= 1 / len(child):
            continue
        r = rng.random()
        if r <= 0.5:
            delta = (2 * r + (1 - 2 * r) * (1 - y) ** (eta + 1)) ** (1 / (eta + 1)) - 1
        else:
            delta = 1 - (2 * (1 - r) + 2 * (r - 0.5) * y ** (eta + 1)) ** (1 / (eta + 1))
        child[j] = np.clip(y + delta, 0, 1)
    return child


def search(evaluate, bounds, population_size=32, generations=16, seed=42):
    bounds = np.asarray(bounds, dtype=float)
    if population_size < 4 or generations < 1 or not np.all(bounds[:, 1] > bounds[:, 0]):
        raise ValueError("Invalid NSGA-II settings")
    rng = np.random.default_rng(seed)
    population = rng.random((population_size, len(bounds)))
    population[:2] = [np.zeros(len(bounds)), np.ones(len(bounds))]
    cache = {}

    def assess(normalized):
        key = tuple(normalized)
        if key not in cache:
            real = bounds[:, 0] + normalized * (bounds[:, 1] - bounds[:, 0])
            result = np.asarray(evaluate(real), dtype=float)
            if not np.isfinite(result).all():
                raise ValueError("Non-finite objective")
            cache[key] = result
        return cache[key]

    values = np.array([assess(x) for x in population])
    for _ in range(generations):
        ranks, distances = ranks_and_distances(values)

        def tournament():
            a, b = rng.integers(0, len(population), size=2)
            winner = min((a, b), key=lambda i: (ranks[i], -distances[i]))
            return population[winner]

        children = []
        while len(children) < population_size:
            a, b = sbx(tournament(), tournament(), rng)
            children.extend([mutate(a, rng), mutate(b, rng)])
        combined = np.vstack([population, children[:population_size]])
        combined_values = np.vstack([values, [assess(x) for x in children[:population_size]]])
        chosen = []
        for front in fronts(combined_values):
            free = population_size - len(chosen)
            if len(front) <= free:
                chosen.extend(front)
            else:
                order = np.argsort(-crowding(combined_values[front]), kind="stable")
                chosen.extend(front[order[:free]])
                break
        population, values = combined[chosen], combined_values[chosen]
    # Final front over every evaluated design, not just the final population.
    all_x = np.array(list(cache))
    all_values = np.array(list(cache.values()))
    front = fronts(all_values)[0]
    return (bounds[:, 0] + all_x[front] * (bounds[:, 1] - bounds[:, 0]), all_values[front], len(cache))
