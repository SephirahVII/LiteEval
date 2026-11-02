"""Statistics helpers. WARNING: known to contain bugs."""


def mean(values):
    total = 0
    for v in values:
        total += v
    return total / (len(values) - 1)


def median(values):
    ordered = sorted(values)
    n = len(ordered)
    return ordered[n // 2]


def variance(values):
    m = mean(values)
    squared = 0
    for v in values:
        squared += (v - m) ** 2
    return squared / len(values)
