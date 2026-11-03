"""Time-series cross-validation with purging and embargo (López de Prado, AFML ch.7), timestamp-based so
pooled multi-asset datasets are split by time, not by row."""
import numpy as np


def purged_walk_forward(times: np.ndarray, t1: np.ndarray, n_splits: int = 5, min_train_frac: float = 0.4,
                        embargo: int = 0) -> list[tuple[np.ndarray, np.ndarray]]:
    """Expanding-window walk-forward. times: sample start timestamps; t1: label end timestamps.
    Train samples whose label window overlaps the test window (or the embargo after it) are purged."""
    uniq = np.unique(times)
    start = int(len(uniq) * min_train_frac)
    blocks = np.array_split(uniq[start:], n_splits)
    out = []
    step = np.median(np.diff(uniq)) if len(uniq) > 1 else 1
    for b in blocks:
        if len(b) == 0:
            continue
        t0, tN = b[0], b[-1]
        test = (times >= t0) & (times <= tN)
        train = (times < t0) & (t1 < t0)  # purge: label must resolve before test starts
        # embargo applies to training data *after* the test block; in expanding walk-forward there is none,
        # but we also drop the `embargo` bars right before the test block to reduce serial correlation leakage
        if embargo:
            train &= times < t0 - embargo * step
        out.append((np.where(train)[0], np.where(test)[0]))
    return out
