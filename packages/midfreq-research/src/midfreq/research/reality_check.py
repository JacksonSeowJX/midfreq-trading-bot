"""
White's Reality Check (White 2000) for the selection this project made.

The question is different from the two earlier ones, and neither of those
answers it.

  Walk-forward asks: does one configuration hold up on unseen data?
  PBO asks:          within one strategy-universe pair, is the config the
                     grid search picked better than the other 95, or just
                     the luckiest of them?
  Reality Check asks: across every strategy-universe pair we tried, is the
                     one that passed better than nothing, or is it what
                     trying that many produces?

PBO cannot answer the third, because it only ever looked inside S&P 100
reversal and has no knowledge that 23 other pairs were tried and
discarded. A search over pairs is a search like any other.

Method (White 2000, with the stationary bootstrap of Politis & Romano
1994 to preserve serial dependence between windows):

  1. Take each candidate's per-window out-of-sample returns.
  2. Impose the null by centring each candidate on its own mean, so no
     candidate has any edge by construction.
  3. Resample windows with the stationary bootstrap, and for each
     resample record the LARGEST mean across candidates.
  4. The p-value is the fraction of resamples whose largest mean is at
     least the largest mean actually observed.

A small p-value means the winner is better than the best of a set of
edgeless candidates would be, i.e. the selection found something. A large
p-value means the winner is indistinguishable from what searching that
many candidates produces on its own.

Centring on the mean is what makes this a test of the SELECTION rather
than of any one candidate: every candidate is equally edgeless under the
null, so the only thing generating a large maximum is the number of
candidates and the noise in their returns.
"""
from typing import Dict, List, Optional
import numpy as np


def stationary_bootstrap_indices(n: int, mean_block: float,
                                 rng: np.random.Generator) -> np.ndarray:
    """
    One stationary-bootstrap resample of 0..n-1.

    Geometric block lengths with mean `mean_block`, wrapping at the end.
    Blocks matter because walk-forward windows are adjacent in time and a
    plain i.i.d. resample would destroy whatever dependence exists between
    them, understating the variance of the maximum and so overstating
    significance.
    """
    if n <= 0:
        return np.array([], dtype=int)
    p = 1.0 / max(1.0, mean_block)
    idx = np.empty(n, dtype=int)
    idx[0] = rng.integers(0, n)
    for t in range(1, n):
        if rng.random() < p:
            idx[t] = rng.integers(0, n)          # start a new block
        else:
            idx[t] = (idx[t - 1] + 1) % n        # continue the current one
    return idx


def reality_check(candidates: Dict[str, List[float]],
                  n_boot: int = 5000,
                  mean_block: float = 2.0,
                  seed: int = 12345) -> dict:
    """
    Run White's Reality Check over per-window returns.

    candidates: {name: [return per window]}. All candidates must share the
    same number of windows, since a resample draws the same window indices
    for every candidate — that is what keeps the comparison across
    candidates paired, and dropping it would let the maximum be inflated by
    candidates being resampled independently.
    """
    names = list(candidates)
    if len(names) < 2:
        raise ValueError("need at least 2 candidates to test a selection")
    lens = {len(v) for v in candidates.values()}
    if len(lens) != 1:
        raise ValueError(f"candidates have differing window counts: {sorted(lens)}")
    n = lens.pop()
    if n < 4:
        raise ValueError(f"only {n} windows; too few to bootstrap")

    R = np.array([candidates[k] for k in names], dtype=float)   # (k, n)
    observed_means = R.mean(axis=1)
    best_i = int(np.argmax(observed_means))
    observed_max = float(observed_means[best_i])

    # Impose the null: every candidate edgeless.
    centred = R - observed_means[:, None]

    rng = np.random.default_rng(seed)
    boot_max = np.empty(n_boot)
    for b in range(n_boot):
        idx = stationary_bootstrap_indices(n, mean_block, rng)
        boot_max[b] = centred[:, idx].mean(axis=1).max()

    p = float((boot_max >= observed_max).mean())
    return {
        'p_value': p,
        'best_candidate': names[best_i],
        'observed_max_mean': observed_max,
        'n_candidates': len(names),
        'n_windows': n,
        'n_boot': n_boot,
        'mean_block': mean_block,
        'bootstrap_max_quantiles': {
            'p50': float(np.quantile(boot_max, 0.50)),
            'p90': float(np.quantile(boot_max, 0.90)),
            'p95': float(np.quantile(boot_max, 0.95)),
            'p99': float(np.quantile(boot_max, 0.99)),
        },
        'candidate_means': {k: float(m) for k, m in zip(names, observed_means)},
    }


def naive_single_test(returns: List[float], n_boot: int = 5000,
                      mean_block: float = 2.0, seed: int = 12345) -> float:
    """
    The same bootstrap applied to ONE candidate, ignoring that others were
    tried. Reported alongside the Reality Check to show what the multiple
    testing correction actually costs: this is the p-value a paper would
    quote if it only ever mentioned the winner.
    """
    r = np.asarray(returns, dtype=float)
    obs = float(r.mean())
    centred = r - obs
    rng = np.random.default_rng(seed)
    n = len(r)
    boot = np.empty(n_boot)
    for b in range(n_boot):
        boot[b] = centred[stationary_bootstrap_indices(n, mean_block, rng)].mean()
    return float((boot >= obs).mean())
