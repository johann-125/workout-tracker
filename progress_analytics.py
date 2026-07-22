"""Pure-Python trend/plateau analytics over a session's numeric series.
No DB/Flask imports here -- app.py fetches the data, this module does the math."""


def compute_trend(values, min_points=3):
    """Classify a series (oldest->newest) as rising/flat/falling via a simple
    linear-regression slope, normalized to %-change-per-session so the
    threshold is scale-independent (a 1kg lift and a 100kg lift are comparable)."""
    n = len(values)
    if n < min_points:
        return {'direction': 'insufficient_data', 'pct_per_session': None}

    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(values) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, values))
    den = sum((x - mean_x) ** 2 for x in xs)
    slope = num / den if den else 0.0

    pct_per_session = (slope / mean_y * 100) if mean_y else 0.0
    if pct_per_session > 2:
        direction = 'rising'
    elif pct_per_session < -2:
        direction = 'falling'
    else:
        direction = 'flat'
    return {'direction': direction, 'pct_per_session': round(pct_per_session, 1)}


def detect_plateau(values, window=3):
    """True when the best value in the most recent `window` sessions doesn't
    beat the best value from before that window -- catches "no new PR in a
    while" even when the trend still reads as flat-to-rising."""
    if len(values) < window + 1:
        return False
    recent_best = max(values[-window:])
    prior_best = max(values[:-window])
    return recent_best <= prior_best


def analyze_series(e1rm_values, volume_values):
    """Combines both signals for one exercise's progress view."""
    return {
        'e1rm_trend': compute_trend(e1rm_values),
        'volume_trend': compute_trend(volume_values),
        'stalled': detect_plateau(e1rm_values),
    }
