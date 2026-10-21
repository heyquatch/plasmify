"""All-pairs test design: every value of every option meets every value of every other
option at least once, with far fewer cases than the full product.
"""

from __future__ import annotations

import itertools
import random
from typing import Any


def all_pairs(options: dict[str, list[Any]], seed: int = 1, candidates: int = 300) -> list[dict[str, Any]]:
    names = list(options)
    uncovered = {
        ((a, va), (b, vb)) for a, b in itertools.combinations(names, 2) for va in options[a] for vb in options[b]
    }
    rng = random.Random(seed)  # noqa: S311 (test design, not security)
    cases: list[dict[str, Any]] = []
    while uncovered:
        best, best_score = None, -1
        # Seed each candidate with one uncovered pair so progress is guaranteed.
        (a, va), (b, vb) = next(iter(uncovered))
        for _ in range(candidates):
            case = {name: rng.choice(values) for name, values in options.items()}
            case[a], case[b] = va, vb
            score = sum(((x, case[x]), (y, case[y])) in uncovered for x, y in itertools.combinations(names, 2))
            if score > best_score:
                best, best_score = case, score
        cases.append(best)
        uncovered -= {((x, best[x]), (y, best[y])) for x, y in itertools.combinations(names, 2)}
    return cases


def covers_all_pairs(options: dict[str, list[Any]], cases: list[dict[str, Any]]) -> bool:
    for a, b in itertools.combinations(options, 2):
        seen = {(c[a], c[b]) for c in cases}
        if len(seen) != len(options[a]) * len(options[b]):
            return False
    return True
