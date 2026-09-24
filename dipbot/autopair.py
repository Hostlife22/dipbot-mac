"""Selection reconstructed from native choose_candidate at 0x140858fd0."""
from dataclasses import dataclass
from .chain import Pool


@dataclass(frozen=True)
class Candidate:
    pool: Pool
    pair_name: str
    ready: bool
    liquidity_score: int


def choose(candidates):
    candidates = list(candidates)
    if not candidates:
        return 'NOT_FOUND', None
    active = [c for c in candidates if c.ready] or candidates
    best = {}
    for candidate in active:
        key = (candidate.pool.router, candidate.pair_name)
        if key not in best or candidate.liquidity_score > best[key].liquidity_score:
            best[key] = candidate
    if len(best) != 1:
        return 'AMBIGUOUS', None
    selected = next(iter(best.values()))
    return ('RESOLVED' if selected.ready else 'PENDING'), selected


def ordered(candidates):
    # Native _discover_token sort lambda at 0x140868980.
    return sorted(candidates, key=lambda c: (c.pool.router, c.pair_name.casefold(),
                  -int(c.liquidity_score), c.pool.fee or 0, c.pool.address.lower()))
