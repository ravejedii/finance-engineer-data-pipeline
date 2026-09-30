"""Deterministic randomness: one independent stream per named component.

Deriving each stream from (seed, name) means adding a random draw in one
component never shifts the output of another.
"""

from __future__ import annotations

import hashlib
import math
import random


def stream(seed: int, name: str) -> random.Random:
    digest = hashlib.sha256(f"{seed}:{name}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def poisson(rng: random.Random, lam: float) -> int:
    """Knuth's method; fine for the small per-day rates used here."""
    if lam <= 0:
        return 0
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def hex_id(rng: random.Random, prefix: str, length: int = 24) -> str:
    return f"{prefix}{rng.getrandbits(length * 4):0{length}x}"


def weighted_choice(rng: random.Random, weights: dict) -> object:
    keys = list(weights)
    return rng.choices(keys, weights=[weights[k] for k in keys], k=1)[0]
