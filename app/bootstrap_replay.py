"""Shared block-bootstrap replay statistics used by both EGX and US research
evaluation pipelines. Pure math only; no market-specific assumptions."""
from decimal import Decimal

D = Decimal


def sample_blocks(values, block_size, rng):
    """Nonwrapping uniformly chosen valid starts, final block truncated to n."""
    sampled = []
    while len(sampled) < len(values):
        start = rng.randrange(len(values) - block_size + 1)
        sampled.extend(values[start:start + block_size])
    return tuple(sampled[:len(values)])


def bootstrap_statistics(values, starting_equity):
    # Primitive equivalent of M7 realized drawdown; repeated IDs are resamples,
    # not admissible canonical M7 observations. Never replay M6 here.
    equity = peak = starting_equity
    drawdown = D(0)
    for net in values:
        equity += net
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    total = sum(values, D(0))
    return total / D(len(values)), total, drawdown


def quantile(values, probability):
    """Linear interpolation at (B-1)*p in sorted empirical replicates."""
    ordered = sorted(values)
    index = D(len(ordered) - 1) * probability
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)
