"""Meta-label filter on engine setups (research + live scoring). Results: docs/ML_RESEARCH.md section 11.

RECONSTRUCTED on 2026-11-26 after the original file (written 2026-11-11) was accidentally overwritten with no backup.
It was rebuilt from its callers (app/agents/readiness.py, app/agents/daily.py, app/signal/evidence.py,
app/research/simulate.py, context.py, hypotheses.py, selector.py and their tests), the setup cache format in
data/meta_setups/, the surviving run reports data/models/meta_label_{tf}_latest.json and docs/ML_RESEARCH.md section 11.
`code_fingerprint` reproduces the fingerprint stored in the existing cache exactly. Feature details that no caller,
test or document pins down (the exact direction-aligned copies, the regime one-hots, the cap on room to the nearest
level, the threshold rule's objective) are best reconstructions and may differ from the lost original.

    uv run python -m app.ml.meta_label --tf 4h --days 1460 --symbols BTCUSDT ETHUSDT ...

What it is (Lopez de Prado, AFML 3.6). The primary model is the rule engine: every confirmed, validated setup of
`app.backtest.engine.generate_setups` (the live code path), cached per symbol in data/meta_setups/{tf}_{days}d_{SYM}.json
together with the code fingerprint it was generated with. The live exit profile `edge_tp2r` is applied (single limit
at the zone edge, the strategy's stop, one take-profit at +2R) and each setup is replayed with TradeSim (0.05 % taker
fee, 2 bps slippage). Label = 1 if +2R is hit before the stop, 0 on a stop or time exit; unfilled setups are dropped.

Features (`setup_row`): the f1 set without taker imbalance (the long CSV history lacks it), direction-aligned copies
of the directional f1 features, and setup-level features: strategy (one-hot), direction, regime (one-hot and aligned
with the trade), validation quality, confluence bias, supporting/opposing evidence families, stop distance in ATR,
distance from price to the limit, R to the nearest opposing S/R level, higher-timeframe trend agreement, last and
3-day mean perp funding (signed per side), hour/day. Optional market-context features (`context=`, see
app/research/context.py) are added by name in CONTEXT_FEATURES; they are never model inputs.

Model: LightGBM with isotonic calibration on the last 20 % of each (time-ordered) training window, against the
training base rate and an L2 logistic regression; purged walk-forward CV (5 folds, 24-bar embargo). The take/skip
threshold for test fold k is chosen on the out-of-sample predictions of folds < k only, so fold 0 is validation only.

Acceptance policy (train.py criteria adapted to a filter, plus the section 7.2 ECE gate), all must pass:
  - calibrated log loss beats the base rate on >= 70 % of folds;
  - mean AUC >= 0.52 and above the logistic baseline;
  - the filtered trades (folds 1..4) number >= 30, have a bootstrap 95 % CI lower bound of expectancy > 0 and beat
    taking every setup;
  - pooled out-of-sample ECE <= 0.03.
The run report is written to {model_dir}/meta_label_{tf}_latest.json. A model file is saved ONLY when every
criterion passes; `score()` then serves it, otherwise it returns p None, accepted_model False and the failed criteria.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import pickle
import time
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from app.analysis.regime import REGIMES
from app.data.timeframes import tf_ms
from app.ml.features import FEATURE_VERSION, build_features
from app.strategies.exits import apply_exit_profile
from app.strategies.library import STRATEGIES

HISTORY = Path("data/history")             # research CSVs: data/history/{tf}/{SYMBOL}.csv (app.maintenance.history)
FUNDING_DIR = HISTORY / "funding"          # settled perp funding: data/history/funding/{SYMBOL}.csv (time, rate)
CACHE = Path("data/meta_setups")           # engine setups: {tf}_{days}d_{SYMBOL}.json
PROFILE = "edge_tp2r"                      # live exit profile used for the labels
WINDOW = 300                               # analysis window, as BacktestConfig (cache: n_bars = days * bars/day + 300)
HTF: dict[str, list[str]] = {"15m": ["1h", "4h"], "1h": ["4h", "1d"], "4h": ["1d"], "1d": []}
N_SPLITS = 5
EMBARGO_BARS = 24
CAL_FRAC = 0.2                             # last 20 % of each training window calibrates the isotonic map
MIN_TRAIN, MIN_TEST = 1000, 100            # folds smaller than this are skipped
MIN_KEPT = 30
MAX_ECE = 0.03
ECE_BINS = 10
OPEN_ROOM_R = 10.0                         # room (R) when no S/R level opposes the trade; also the cap
FUNDING_STALE_MS = 2 * 86_400_000          # a funding print older than this at setup time is unknown (NaN)
FINGERPRINT_FILES = ("app/analysis/**/*.py", "app/strategies/**/*.py", "app/backtest/engine.py", "app/risk/sizing.py")

STRATEGY_IDS = sorted(STRATEGIES)
REGIME_SIGN = {"strong_bull": 1.0, "weak_bull": 1.0, "strong_bear": -1.0, "weak_bear": -1.0}
# directional f1 features -> neutral centre; the "_dir" copy is (value - centre) * dir (positive = in trade direction)
DIRECTIONAL: dict[str, float] = {
    "ret_1": 0.0, "ret_3": 0.0, "ret_6": 0.0, "ret_12": 0.0, "ret_24": 0.0, "ret_72": 0.0, "rsi14": 50.0,
    "stochrsi_k": 50.0, "macd_hist_atr": 0.0, "di_diff": 0.0, "bb_pctb": 0.5, "cmf": 0.0, "dist_ema20_atr": 0.0,
    "dist_ema50_atr": 0.0, "dist_ema200_atr": 0.0, "donchian_pos": 0.5, "supertrend": 0.0, "structure": 0.0,
    "btc_ret_6": 0.0, "btc_ret_24": 0.0, "rel_strength_24": 0.0,
}
F1_COLUMNS = ["ret_1", "ret_3", "ret_6", "ret_12", "ret_24", "ret_72", "vol_24", "vol_72", "vol_ratio", "atr_pct",
              "parkinson", "rsi14", "stochrsi_k", "macd_hist_atr", "adx", "di_diff", "bb_pctb", "bb_width", "chop",
              "cmf", "rvol", "taker_imb", "dist_ema20_atr", "dist_ema50_atr", "dist_ema200_atr", "donchian_pos",
              "supertrend", "squeeze", "structure", "hour_sin", "hour_cos", "dow"]
BTC_COLUMNS = ["btc_ret_6", "btc_ret_24", "rel_strength_24"]
SETUP_FEATURES = ["dir", "quality", "bias_dir", "n_support", "n_oppose", "stop_atr", "entry_gap_atr", "opp_level_r",
                  "htf_agree", "regime_aligned", "funding_dir", "funding_3d_dir"]
CONTEXT_FEATURES = ("btc_trend_dir", "xs_mom_dir_pct", "funding_pct_dir", "pending_same_side", "pending_opp_side")
NAN = math.nan


# ---------------------------------------------------------------- cache helpers

@lru_cache(maxsize=1)
def code_fingerprint() -> str:
    """16-hex fingerprint of the code that decides which setups exist (analysis, strategies, the setup generator and
    sizing). Stored in every cache file; a different value means the cached setups came from other code."""
    root = Path(__file__).resolve().parents[2]
    files: set[Path] = set()
    for pat in FINGERPRINT_FILES:
        files |= set(root.glob(pat))
    h = hashlib.sha256()
    for p in sorted(files):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def _jsonable(o):
    """json.dumps default= for numpy scalars/arrays in setups and reports."""
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, Path):
        return str(o)
    raise TypeError(f"not JSON serialisable: {type(o).__name__}")


def snapshot_extras(snap: Mapping, cand: Mapping) -> dict:
    """Fields kept with each setup from the same as-of snapshot (generate_setups extras=): HTF trend votes, S/R level
    prices and the number of supporting/opposing evidence families of the validation."""
    v = cand.get("validation") or {}
    sup, opp = v.get("supporting_families"), v.get("opposing_families")
    levels = ((snap.get("price_action") or {}).get("levels")) or []
    return {"mtf": {k: int(x) for k, x in (snap.get("mtf") or {}).items()},
            "levels": [float(lv["price"]) for lv in levels if lv.get("price") is not None],
            "n_support": len(sup) if sup is not None else None,
            "n_oppose": len(opp) if opp is not None else None}


def cache_path(tf: str, days: int, symbol: str, root: Path | None = None) -> Path:
    return (root or CACHE) / f"{tf}_{days}d_{symbol}.json"


def history_source(symbol: str, tf: str) -> str:
    return f"data/history/{tf} CSV" if (HISTORY / tf / f"{symbol}.csv").exists() else "candles table"


def load_history(symbol: str, tf: str, days: int | None = None) -> pd.DataFrame:
    """Closed candles from data/history/{tf}/{SYMBOL}.csv, else the candles table (spot). days: the last days of bars
    plus the WINDOW warm-up bars; None = everything."""
    p = HISTORY / tf / f"{symbol}.csv"
    if p.exists():
        df = pd.read_csv(p)
    else:
        df = _load_db(symbol, tf)
    if df.empty:
        return df
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    df["open_time"] = df["open_time"].astype("int64")
    if days is not None:
        n = int(days * 86_400_000 // tf_ms(tf)) + WINDOW
        df = df.tail(n).reset_index(drop=True)
    return df


def _load_db(symbol: str, tf: str) -> pd.DataFrame:
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import get_settings
    from app.data.store import load_candles

    async def go() -> pd.DataFrame:
        eng = create_async_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(eng)() as s:
                return await load_candles(s, symbol, tf, limit=10_000_000)
        finally:
            await eng.dispose()

    return asyncio.run(go())


def load_funding(symbol: str) -> pd.DataFrame | None:
    """Settled perp funding prints (time ms, rate per 8 h) from data/history/funding/{SYMBOL}.csv, or None."""
    p = FUNDING_DIR / f"{symbol}.csv"
    if not p.exists():
        return None
    f = pd.read_csv(p, usecols=["time", "rate"]).dropna()
    return f.drop_duplicates("time").sort_values("time").reset_index(drop=True)


def load_cached_setups(symbol: str, tf: str, days: int, allow_stale: bool = False,
                       root: Path | None = None) -> tuple[pd.DataFrame, list[dict], dict] | None:
    """(candles pinned to the cache's bars, setups, header) or None if there is no usable cache. A cache generated by
    other code (fingerprint) is ignored unless allow_stale."""
    path = cache_path(tf, days, symbol, root)
    if not path.exists():
        return None
    c = json.loads(path.read_text())
    if c.get("fingerprint") != code_fingerprint() and not allow_stale:
        return None
    full = load_history(symbol, tf, None)
    if full.empty:
        return None
    df = full[(full.open_time >= c["first_open_time"]) & (full.open_time <= c["last_open_time"])].reset_index(drop=True)
    if len(df) != c["n_bars"]:
        return None
    setups = c.pop("setups")
    return df, setups, c


def generate_cache(symbol: str, tf: str, days: int, root: Path | None = None) -> tuple[str, int, float]:
    """Regenerate one symbol's engine setups (slow: ~11 min per 4h symbol over 4 years) and write the cache file."""
    from app.backtest.engine import BacktestConfig, generate_setups

    t0 = time.time()
    df = load_history(symbol, tf, days)
    cfg = BacktestConfig(symbols=[symbol], timeframe=tf, htf=HTF[tf], window=WINDOW)
    setups = generate_setups(df, symbol, cfg, extras=snapshot_extras) if len(df) > WINDOW + 50 else []
    path = cache_path(tf, days, symbol, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"fingerprint": code_fingerprint(), "symbol": symbol, "tf": tf, "days": days, "n_bars": len(df),
         "first_open_time": int(df.open_time.iloc[0]) if len(df) else 0,
         "last_open_time": int(df.open_time.iloc[-1]) if len(df) else 0,
         "generated_at": int(time.time() * 1000), "setups": setups}, default=_jsonable))
    return symbol, len(setups), time.time() - t0


def _generate_one(args: tuple) -> tuple[str, int, float]:
    return generate_cache(*args)


# ---------------------------------------------------------------- labels

def label_setups(df: pd.DataFrame, setups: Sequence[Mapping], fee: float, slip_bps: float) -> list[dict]:
    """Each setup with the edge_tp2r profile applied, replayed with TradeSim on the bars after its setup bar.
    Adds outcome, filled, r (R after fees and slippage; None unless filled and finished), label (1 = +2R before the
    stop, 0 = stop or time exit; None when unfilled or still open), fill_time, exit_time. Unfilled setups are kept
    here (the simulator needs them); the training set drops them."""
    from app.signals.simulator import TradeSim

    o, h, lo, c, tm = (df[k].to_numpy() for k in ("open", "high", "low", "close", "open_time"))
    out = []
    for s0 in setups:
        s = apply_exit_profile(dict(s0), PROFILE)
        sim = TradeSim(s["direction"], s["entry_low"], s["entry_high"], s["stop"], list(s["targets"]),
                       expiry_bars=s["expiry_bars"], max_hold_bars=max(3 * s["hold_bars"], 24), fee=fee,
                       slip_bps=slip_bps)
        j = s["bar"] + 1
        while j < len(df) and not sim.terminal:
            sim.on_bar(int(tm[j]), o[j], h[j], lo[j], c[j])
            j += 1
        filled = sim.fill is not None
        done = filled and sim.terminal
        out.append({**s, "outcome": sim.outcome or "OPEN_AT_END", "filled": filled,
                    "r": float(sim.realized_r) if done else None,
                    "label": (1 if sim.outcome == "TARGET_HIT" else 0) if done else None,
                    "fill_time": sim.fill_time, "exit_time": sim.exit_time})
    return out


# ---------------------------------------------------------------- features

def base_feature_columns(btc: bool = True) -> list[str]:
    """The f1 columns used (taker imbalance excluded: the long CSV history has no taker volume); btc adds the BTC
    columns (NaN for BTC itself)."""
    return [c for c in F1_COLUMNS if c != "taker_imb"] + (BTC_COLUMNS if btc else [])


def feature_columns() -> list[str]:
    """Model inputs, in order (context features are never inputs)."""
    base = base_feature_columns(True)
    return (base + [f"{k}_dir" for k in DIRECTIONAL if k in base] + SETUP_FEATURES
            + [f"strat_{s}" for s in STRATEGY_IDS] + [f"regime_{r}" for r in REGIMES])


def _num(v) -> float:
    if v is None or isinstance(v, str):
        return NAN
    try:
        x = float(v)
    except (TypeError, ValueError):
        return NAN
    return x if math.isfinite(x) else NAN


def _funding_feats(funding: pd.DataFrame | None, t_ms: int) -> tuple[float, float]:
    """(last settled print at t, mean of the prints of the last 3 days) or NaN."""
    if funding is None or not len(funding):
        return NAN, NAN
    tm = funding["time"].to_numpy()
    i = int(np.searchsorted(tm, t_ms, side="right"))
    if i == 0 or t_ms - int(tm[i - 1]) > FUNDING_STALE_MS:
        return NAN, NAN
    j = int(np.searchsorted(tm, t_ms - 3 * 86_400_000, side="right"))
    rates = funding["rate"].to_numpy(dtype=float)
    return float(rates[i - 1]), float(rates[j:i].mean())


def context_features(context: Mapping | None, d: float) -> dict[str, float]:
    """Direction-aware versions of the side-agnostic context dict (app/research/context.py). Missing -> NaN."""
    c = context or {}
    long = d > 0
    bt, xs, fp = _num(c.get("btc_trend")), _num(c.get("xs_mom_pct")), _num(c.get("funding_pct_90d"))
    pl, ps = _num(c.get("pending_long")), _num(c.get("pending_short"))
    return {"btc_trend_dir": bt * d,
            "xs_mom_dir_pct": xs if long else 1.0 - xs,
            "funding_pct_dir": fp if long else 1.0 - fp,
            "pending_same_side": pl if long else ps,
            "pending_opp_side": ps if long else pl}


def setup_row(base: Mapping, price: float, setup: Mapping, funding: pd.DataFrame | None = None,
              context: Mapping | None = None) -> dict:
    """Feature dict of one setup. base: the f1 row of the setup bar (base_feature_columns(True)); price: that bar's
    close; setup: a generate_setups record with the exit profile applied (edge entry). funding: the symbol's settled
    funding prints (load_funding) or None; context: app.research.context market_context dict or None (the context
    features are added only when it is given, so the model rows are unchanged)."""
    d = 1.0 if setup["direction"] == "LONG" else -1.0
    cols = base_feature_columns(True)
    row: dict = {k: _num(base.get(k)) for k in cols}
    for k, centre in DIRECTIONAL.items():
        if k in row:
            row[f"{k}_dir"] = (row[k] - centre) * d
    edge = float(setup["entry_high"] if d > 0 else setup["entry_low"])
    stop = float(setup["stop"])
    risk = abs(edge - stop)
    atr = row.get("atr_pct", NAN) * float(price)
    ok_atr = math.isfinite(atr) and atr > 0
    levels = setup.get("levels")
    if levels is None or risk <= 0:
        room = NAN
    else:
        opp = [d * (float(x) - edge) for x in levels if d * (float(x) - edge) > 0]
        room = min(min(opp) / risk, OPEN_ROOM_R) if opp else OPEN_ROOM_R
    mtf = [v for v in (setup.get("mtf") or {}).values() if v is not None]
    fund, fund3 = _funding_feats(funding, int(setup["time"])) if setup.get("time") is not None else (NAN, NAN)
    if not math.isfinite(fund) and setup.get("funding_rate") is not None:  # live fallback: the snapshot's rate
        fund = _num(setup.get("funding_rate"))
    regime = setup.get("regime")
    row.update({
        "dir": d,
        "quality": _num(setup.get("quality")),
        "bias_dir": _num(setup.get("bias")) * d,
        "n_support": _num(setup.get("n_support")),
        "n_oppose": _num(setup.get("n_oppose")),
        "stop_atr": risk / atr if ok_atr else NAN,
        "entry_gap_atr": d * (float(price) - edge) / atr if ok_atr else NAN,
        "opp_level_r": room,
        "htf_agree": float(np.mean(mtf)) * d if mtf else NAN,
        "regime_aligned": REGIME_SIGN.get(str(regime), 0.0) * d if regime is not None else NAN,
        "funding_dir": fund * d,
        "funding_3d_dir": fund3 * d,
    })
    row.update({f"strat_{s}": float(setup.get("strategy") == s) for s in STRATEGY_IDS})
    row.update({f"regime_{r}": float(regime == r) for r in REGIMES})
    if context is not None:
        row.update(context_features(context, d))
    return row


def live_setup_features(df: pd.DataFrame, cand: Mapping, snap: Mapping, tf: str, btc: pd.DataFrame | None = None,
                        funding: pd.DataFrame | None = None, context: Mapping | None = None) -> dict:
    """setup_row for a live engine candidate on the last closed bar of df (closed candles of tf; btc: BTCUSDT's,
    None for BTC itself). The setup is built exactly as generate_setups records it (time = last open_time + tf,
    quality, regime, confluence bias, snapshot_extras) and gets the same exit profile. Without a funding history the
    snapshot's current funding rate (if any) stands in for the last print."""
    base = build_features(df, btc).reindex(columns=base_feature_columns(True))
    setup = {"bar": len(df) - 1, "time": int(df["open_time"].iloc[-1]) + tf_ms(tf),
             "strategy": cand["strategy"], "direction": cand["direction"],
             "entry_low": float(cand["entry_low"]), "entry_high": float(cand["entry_high"]),
             "stop": float(cand["stop"]), "targets": list(cand.get("targets") or []),
             "expiry_bars": cand.get("expiry_bars"),
             "hold_bars": cand.get("expected_hold_bars", cand.get("hold_bars")),
             "regime": (snap.get("regime") or {}).get("regime"),
             "quality": (cand.get("validation") or {}).get("quality_score"),
             "bias": (snap.get("confluence") or {}).get("bias"),
             **snapshot_extras(snap, cand)}
    if funding is None:
        setup["funding_rate"] = (snap.get("derivatives") or {}).get("funding_rate")
    setup = apply_exit_profile(setup, PROFILE)
    return setup_row(base.iloc[-1].to_dict(), float(df["close"].iloc[-1]), setup, funding, context)


def dataset_rows(symbol: str, df: pd.DataFrame, labelled: Sequence[Mapping], base: pd.DataFrame,
                 funding: pd.DataFrame | None) -> list[dict]:
    """Training rows of one symbol: filled and finished setups only (unfilled and still-open ones are dropped)."""
    close = df["close"].to_numpy()
    out = []
    for t in labelled:
        if not t["filled"] or t["label"] is None or t["r"] is None:
            continue
        row = setup_row(base.iloc[t["bar"]].to_dict(), float(close[t["bar"]]), t, funding)
        row.update({"label": int(t["label"]), "r": float(t["r"]), "time": int(t["time"]),
                    "t1": int(t["exit_time"]) if t.get("exit_time") is not None else int(t["time"]),
                    "symbol": symbol, "strategy": t["strategy"], "direction": t["direction"]})
        out.append(row)
    return out


# ---------------------------------------------------------------- evaluation helpers

def choose_threshold(p: np.ndarray, r: np.ndarray, min_keep: int = MIN_KEPT) -> float:
    """The take threshold (take when p >= threshold) that maximises the total R of the kept trades, keeping at least
    min_keep; candidates are the distinct predicted values, and the lowest one is "take every setup". Ties go to the
    lower threshold (more trades)."""
    p, r = np.asarray(p, float), np.asarray(r, float)
    if len(p) == 0:
        return 0.0
    order = np.argsort(-p, kind="stable")
    csum = np.cumsum(r[order])
    uniq = np.unique(p)
    counts = len(p) - np.searchsorted(np.sort(p), uniq, side="left")
    totals = csum[counts - 1]
    valid = counts >= min(min_keep, len(p))
    best, best_tot = 0, -np.inf
    for i in np.where(valid)[0]:
        if totals[i] > best_tot + 1e-12:
            best, best_tot = i, totals[i]
    return float(uniq[best])


def walk_forward_thresholds(preds: Sequence[tuple[np.ndarray, np.ndarray]], min_keep: int = MIN_KEPT
                            ) -> list[float | None]:
    """Threshold for each test fold k from the out-of-sample (p, r) of folds < k only; fold 0 has none
    (validation only)."""
    out: list[float | None] = [None]
    for k in range(1, len(preds)):
        p = np.concatenate([preds[j][0] for j in range(k)])
        r = np.concatenate([preds[j][1] for j in range(k)])
        out.append(choose_threshold(p, r, min_keep))
    return out


def ece(p: np.ndarray, y: np.ndarray, bins: int = ECE_BINS) -> float:
    """Expected calibration error over equal-count (quantile) bins."""
    p, y = np.asarray(p, float), np.asarray(y, float)
    if len(p) == 0:
        return NAN
    order = np.argsort(p, kind="stable")
    e = 0.0
    for idx in np.array_split(order, min(bins, len(p))):
        if len(idx):
            e += len(idx) / len(p) * abs(p[idx].mean() - y[idx].mean())
    return float(e)


def _stats(r: np.ndarray, y: np.ndarray) -> dict:
    from app.backtest.metrics import trade_stats

    s = trade_stats([float(x) for x in r])
    s["target_hit_rate"] = float(np.mean(y)) if len(y) else None
    return s


def _cat(xs: list[np.ndarray]) -> np.ndarray:
    return np.concatenate(xs) if xs else np.array([])


def _mean_of(rows: list[dict], key: str) -> float:
    v = [x[key] for x in rows if x[key] is not None]
    return float(np.mean(v)) if v else NAN


def _models(seed: int = 0):
    import lightgbm as lgb
    from sklearn.impute import SimpleImputer
    from sklearn.isotonic import IsotonicRegression
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    from app.ml.train import LGB_PARAMS

    return (lambda: lgb.LGBMClassifier(**{**LGB_PARAMS, "random_state": seed}),
            lambda: IsotonicRegression(out_of_bounds="clip", y_min=0.01, y_max=0.99),
            lambda: make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                                  LogisticRegression(C=0.1, max_iter=1000)))


def _fit_calibrated(X: np.ndarray, y: np.ndarray, seed: int = 0):
    """LightGBM on the first 80 % (time order), isotonic on the last 20 %."""
    gbm_f, iso_f, _ = _models(seed)
    cut = int(len(X) * (1 - CAL_FRAC))
    gbm = gbm_f().fit(X[:cut], y[:cut])
    iso = iso_f().fit(gbm.predict_proba(X[cut:])[:, 1], y[cut:])
    return gbm, iso


def research(ds: pd.DataFrame, features: Sequence[str] | None = None, tf: str = "4h", n_splits: int = N_SPLITS,
             embargo_bars: int = EMBARGO_BARS, min_train: int = MIN_TRAIN, min_test: int = MIN_TEST,
             min_keep: int = MIN_KEPT, seed: int = 0) -> dict:
    """Purged walk-forward evaluation and the acceptance verdict. ds: dataset_rows rows (features + label, r, time,
    t1). Pure: writes nothing (see save_run)."""
    import warnings

    from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

    from app.ml.cv import purged_walk_forward

    feats = list(features or feature_columns())
    d = ds.sort_values("time", kind="stable").reset_index(drop=True)
    X = d[feats].to_numpy(dtype=float)
    y = d["label"].to_numpy().astype(int)
    r = d["r"].to_numpy(dtype=float)
    times, t1 = d["time"].to_numpy(dtype=np.int64), d["t1"].to_numpy(dtype=np.int64)
    gbm_f, iso_f, lr_f = _models(seed)
    emb = embargo_bars * tf_ms(tf)
    rows, preds_g, preds_l, ys = [], [], [], []
    for tr, te in purged_walk_forward(times, t1, n_splits, embargo=0):
        if not len(te):
            continue
        t0 = times[te[0]]
        tr = tr[times[tr] < t0 - emb]  # embargo: the last 24 bars before the test block are not trained on
        if len(tr) < min_train or len(te) < min_test or len(set(y[tr])) < 2:
            continue
        cut = int(len(tr) * (1 - CAL_FRAC))
        tr_fit, tr_cal = tr[:cut], tr[cut:]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            gbm = gbm_f().fit(X[tr_fit], y[tr_fit])
            iso = iso_f().fit(gbm.predict_proba(X[tr_cal])[:, 1], y[tr_cal])
            p_g = iso.predict(gbm.predict_proba(X[te])[:, 1])
            p_l = lr_f().fit(X[tr], y[tr]).predict_proba(X[te])[:, 1]
            perm = gbm_f().fit(X[tr_fit], np.random.default_rng(seed + len(rows)).permutation(y[tr_fit]))
            p_perm = perm.predict_proba(X[te])[:, 1]
        yt = y[te]
        prior = float(y[tr].mean())
        p_b = np.full(len(te), prior)
        two = len(set(yt)) > 1
        rows.append({"fold": len(rows), "n_train": int(len(tr)), "n_cal": int(len(tr_cal)), "n_test": int(len(te)),
                     "test_start": int(times[te[0]]), "test_end": int(times[te[-1]]),
                     "base_rate_train": prior, "base_rate": float(yt.mean()),
                     "logloss_prior": float(log_loss(yt, p_b, labels=[0, 1])),
                     "logloss_lr": float(log_loss(yt, p_l, labels=[0, 1])),
                     "logloss_gbm": float(log_loss(yt, p_g, labels=[0, 1])),
                     "auc_gbm": float(roc_auc_score(yt, p_g)) if two else None,
                     "auc_lr": float(roc_auc_score(yt, p_l)) if two else None,
                     "auc_permuted": float(roc_auc_score(yt, p_perm)) if two else None,
                     "brier_gbm": float(brier_score_loss(yt, p_g)), "brier_prior": float(brier_score_loss(yt, p_b)),
                     "ece_gbm": ece(p_g, yt)})
        preds_g.append((p_g, r[te]))
        preds_l.append((p_l, r[te]))
        ys.append(yt)
    if not rows:
        return {"status": "rejected", "reason": "insufficient data for walk-forward", "criteria": {}, "folds": [],
                "threshold": None}
    thr_g = walk_forward_thresholds(preds_g, min_keep)
    thr_l = walk_forward_thresholds(preds_l, min_keep)
    all_r, all_y, kept_r, kept_y, lr_r, lr_y = [], [], [], [], [], []
    for k, row in enumerate(rows):
        row["threshold"], row["threshold_lr"] = thr_g[k], thr_l[k]
        if k == 0:
            continue  # validation only: it sets fold 1's threshold
        (pg, rk), (pl, _), yk = preds_g[k], preds_l[k], ys[k]
        keep, keep_l = pg >= thr_g[k], pl >= thr_l[k]
        row.update({"n_kept": int(keep.sum()), "expectancy_all": float(rk.mean()),
                    "expectancy_kept": float(rk[keep].mean()) if keep.any() else None})
        all_r.append(rk)
        all_y.append(yk)
        kept_r.append(rk[keep])
        kept_y.append(yk[keep])
        lr_r.append(rk[keep_l])
        lr_y.append(yk[keep_l])
    oos_all = _stats(_cat(all_r), _cat(all_y))
    oos_kept = _stats(_cat(kept_r), _cat(kept_y))
    oos_lr = _stats(_cat(lr_r), _cat(lr_y))
    p_pool = np.concatenate([p for p, _ in preds_g])
    ece_oos = ece(p_pool, np.concatenate(ys))
    beats = float(np.mean([x["logloss_gbm"] < x["logloss_prior"] for x in rows]))
    auc_g, auc_l, auc_p = (_mean_of(rows, k) for k in ("auc_gbm", "auc_lr", "auc_permuted"))
    ci = oos_kept.get("expectancy_ci95") or [NAN, NAN]
    criteria = {
        "beats_base_rate_logloss_>=70pct_folds": bool(beats >= 0.7),
        "mean_auc_>=0.52": bool(auc_g >= 0.52),
        "beats_logistic_baseline_auc": bool(auc_g > auc_l),
        "filtered_n_>=30": bool(oos_kept["n"] >= MIN_KEPT),
        "filtered_expectancy_ci_low_>0": bool(oos_kept["n"] >= 5 and ci[0] > 0),
        "filtered_beats_all_setups": bool(oos_kept["n"] > 0 and oos_all["n"] > 0
                                          and oos_kept["expectancy_r"] > oos_all["expectancy_r"]),
        "ece_<=0.03": bool(ece_oos <= MAX_ECE),
    }
    accepted = all(criteria.values())
    return {"status": "accepted" if accepted else "rejected", "criteria": criteria,
            "summary": {"mean_auc_gbm": auc_g, "mean_auc_lr": auc_l, "beats_prior_frac": beats, "ece_oos": ece_oos,
                        "n_samples": int(len(d)), "label_balance": float(y.mean()), "mean_auc_permuted": auc_p,
                        "symbols": int(d["symbol"].nunique()) if "symbol" in d else None,
                        "start": int(times[0]), "end": int(times[-1])},
            # the threshold a final model would use: chosen on every out-of-sample fold
            "threshold": choose_threshold(p_pool, np.concatenate([x for _, x in preds_g]), min_keep),
            "oos_all": oos_all, "oos_kept": oos_kept, "oos_logistic": oos_lr, "folds": rows,
            "features": feats}


# ---------------------------------------------------------------- run, report, serving

def models_dir() -> Path:
    from app.config import get_settings

    return Path(get_settings().model_dir)


def report_path(tf: str, root: Path | None = None) -> Path:
    return (root or models_dir()) / f"meta_label_{tf}_latest.json"


def save_run(report: dict, ds: pd.DataFrame, tf: str, root: Path | None = None) -> dict:
    """Write the run report; fit and save the final model ONLY if every acceptance criterion passed."""
    root = root or models_dir()
    root.mkdir(parents=True, exist_ok=True)
    version = report.get("version") or time.strftime("%Y%m%d%H%M%S", time.gmtime())
    report.update(name=f"meta_label_{tf}", version=version, artifact=None)
    if report.get("status") == "accepted" and report.get("criteria") and all(report["criteria"].values()):
        feats = report["features"]
        d = ds.sort_values("time", kind="stable").reset_index(drop=True)
        gbm, iso = _fit_calibrated(d[feats].to_numpy(dtype=float), d["label"].to_numpy().astype(int))
        path = root / f"meta_label_{tf}_{version}.pkl"
        with open(path, "wb") as fh:
            pickle.dump({"model": gbm, "calibrator": iso, "features": feats, "threshold": report["threshold"],
                         "tf": tf, "version": version, "profile": PROFILE, "feature_version": FEATURE_VERSION,
                         "code_fingerprint": code_fingerprint()}, fh)
        report["artifact"] = str(path)
    report_path(tf, root).write_text(json.dumps(report, indent=1, default=_jsonable))
    return report


def load_report(tf: str, root: Path | None = None) -> dict | None:
    p = report_path(tf, root)
    try:
        return json.loads(p.read_text()) if p.exists() else None
    except (OSError, ValueError):
        return None


@lru_cache(maxsize=8)
def _load_pickle(path: str, mtime: float) -> dict:
    with open(path, "rb") as fh:
        return pickle.load(fh)


def accepted_artifact(tf: str, root: Path | None = None) -> dict | None:
    """The accepted model bundle for tf, or None: no run, a rejected run, a missing file, or a model built on other
    features/engine code (fail-closed)."""
    rep = load_report(tf, root)
    if not rep or rep.get("status") != "accepted" or not rep.get("artifact"):
        return None
    p = Path(rep["artifact"])
    if not p.exists():
        return None
    art = _load_pickle(str(p), p.stat().st_mtime)
    if art.get("feature_version") != FEATURE_VERSION or art.get("code_fingerprint") != code_fingerprint():
        return None
    return art


def score(row: Mapping, tf: str = "4h", root: Path | None = None) -> dict:
    """The meta-label view of one setup (a setup_row / live_setup_features dict). Only an accepted model gives a
    probability; otherwise p is None and failed_criteria says why (docs/ML_RESEARCH.md section 11)."""
    art = accepted_artifact(tf, root)
    if art is None:
        rep = load_report(tf, root) or {}
        failed = [k for k, v in (rep.get("criteria") or {}).items() if not v]
        if not rep:
            basis = f"no meta-label research run for {tf}"
        elif rep.get("status") == "accepted":
            basis = f"the accepted {tf} model is missing or was built on other features/engine code"
        else:
            basis = (f"no {tf} meta-label model passed the acceptance policy (run {rep.get('version')}: "
                     f"failed {', '.join(failed) or rep.get('reason', 'unknown')})")
        return {"p": None, "threshold": None, "take": None, "accepted_model": False, "basis": basis,
                "failed_criteria": failed, "status": rep.get("status"), "version": rep.get("version")}
    x = np.array([[_num(row.get(f)) for f in art["features"]]])
    raw = float(art["model"].predict_proba(x)[:, 1][0])
    p = float(art["calibrator"].predict([raw])[0])
    thr = float(art["threshold"])
    return {"p": p, "threshold": thr, "take": bool(p >= thr), "accepted_model": True,
            "basis": f"meta_label_{tf} v{art.get('version')}: calibrated P(+2R before stop), take at >= {thr:.2f}",
            "failed_criteria": [], "status": "accepted", "version": art.get("version"), "raw": raw}


def build_dataset(tf: str, days: int, symbols: Sequence[str], fee: float, slip_bps: float, workers: int = 4,
                  allow_stale: bool = False, generate: bool = True) -> tuple[pd.DataFrame, dict]:
    """Setups (cache, regenerated when missing or stale) -> labels -> features for every symbol."""
    from concurrent.futures import ProcessPoolExecutor

    todo = [s for s in symbols if load_cached_setups(s, tf, days, allow_stale) is None]
    if todo and generate:
        print(f"generating setups for {len(todo)} symbols with {workers} workers (slow)", flush=True)
        with ProcessPoolExecutor(max(1, workers)) as ex:
            for sym, n, secs in ex.map(_generate_one, [(s, tf, days) for s in todo]):
                print(f"  {sym}: {n} setups ({secs:.0f}s)", flush=True)
    btc = load_history("BTCUSDT", tf, None)
    cols = base_feature_columns(True)
    rows, n_setups, used, sources = [], 0, [], set()
    for sym in symbols:
        got = load_cached_setups(sym, tf, days, allow_stale)
        if got is None:
            print(f"  skip {sym}: no usable setup cache")
            continue
        df, setups, _ = got
        base = build_features(df, btc if sym != "BTCUSDT" else None).reindex(columns=cols)
        labelled = label_setups(df, setups, fee, slip_bps)
        rows += dataset_rows(sym, df, labelled, base, load_funding(sym))
        n_setups += len(setups)
        used.append(sym)
        sources.add(history_source(sym, tf))
    meta = {"tf": tf, "days": days, "symbols": used, "n_setups": n_setups, "n_trades": len(rows), "fee": fee,
            "slip_bps": slip_bps, "profile": PROFILE, "code_fingerprint": code_fingerprint(),
            "source": ", ".join(sorted(sources))}
    return pd.DataFrame(rows), meta


def run(tf: str, days: int, symbols: Sequence[str] | None = None, workers: int = 4, allow_stale: bool = False,
        generate: bool = True, min_train: int = MIN_TRAIN, min_test: int = MIN_TEST, out: Path | None = None) -> dict:
    from app.config import get_settings

    s = get_settings()
    if not symbols:
        symbols = sorted(p.name.split("_", 2)[2][:-5] for p in CACHE.glob(f"{tf}_{days}d_*.json"))
    ds, meta = build_dataset(tf, days, symbols, s.taker_fee, s.slippage_bps, workers, allow_stale, generate)
    print(f"{meta['n_setups']} setups, {meta['n_trades']} filled and resolved trades on {len(meta['symbols'])} symbols",
          flush=True)
    rep = research(ds, tf=tf, min_train=min_train, min_test=min_test) if len(ds) else {"status": "rejected", "reason": "no data",
                                                                    "criteria": {}, "threshold": None}
    rep["data"] = meta
    return save_run(rep, ds, tf, out)


def main() -> None:
    ap = argparse.ArgumentParser(description="Meta-label filter on engine setups (docs/ML_RESEARCH.md section 11)")
    ap.add_argument("--tf", default="4h")
    ap.add_argument("--days", type=int, default=1460)
    ap.add_argument("--symbols", nargs="*", help="default: the symbols of the existing cache for tf/days")
    ap.add_argument("--workers", type=int, default=4, help="processes for setup generation")
    ap.add_argument("--allow-stale-cache", action="store_true",
                    help="use cached setups even if generated by other engine code (fingerprint mismatch)")
    ap.add_argument("--no-generate", action="store_true", help="never regenerate setups; skip symbols without cache")
    ap.add_argument("--min-train", type=int, default=MIN_TRAIN, help="smallest training fold")
    ap.add_argument("--min-test", type=int, default=MIN_TEST, help="smallest test fold")
    ap.add_argument("--out", help="report/model directory (default: settings.model_dir)")
    a = ap.parse_args()
    rep = run(a.tf, a.days, a.symbols, a.workers, a.allow_stale_cache, not a.no_generate, a.min_train, a.min_test,
              Path(a.out) if a.out else None)
    print(json.dumps({k: rep.get(k) for k in ("status", "reason", "criteria", "summary", "threshold", "artifact",
                                             "oos_all", "oos_kept")}, indent=1, default=_jsonable))


if __name__ == "__main__":
    main()
