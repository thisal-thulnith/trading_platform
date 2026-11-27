"""Live outputs of the quant lab for the API/UI: today's ranking and the latest research/validation summary.

Both functions only read local files under data/quant_lab/ (run ``python -m app.quant.lab.data --update`` to refresh
the panel); they never hit the network. Heavy imports are lazy so ``import app.quant.lab`` stays cheap.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3] / "data" / "quant_lab"
LOOKBACK_DAYS = 420  # enough for the 200-day regime, 90-day factors and the 90-day history rule
REPLAY_DAYS = 120    # a promoted book with a schedule / no-trade band is replayed over this window to know its holdings


def _read(name: str) -> dict | None:
    p = ROOT / name
    try:
        return json.loads(p.read_text()) if p.exists() else None
    except (OSError, json.JSONDecodeError):
        return None


def _score_row(model: dict, X, names: list[str]):
    import numpy as np

    from app.quant.lab import model as qm
    if model["model"] == "lgbm":
        import lightgbm as lgb
        booster = lgb.Booster(model_file=model["lgbm_model_file"])
        return booster.predict(X)
    st = {"model": model["model"], "signs": model.get("signs", {}), "coef": model.get("coef", {}),
          "intercept": model.get("intercept", 0.0)}
    out = qm.predict(st, X, names)
    return np.asarray(out, dtype=float)


def live_ranking(as_of: str | None = None, k: int | None = None) -> dict:
    """Rank today's tradeable universe.

    Returns ``{as_of, data_through, promoted, model, note, universe_size, factors_used, portfolio, rows}`` where each
    row is ``{symbol, rank, score, weight, side, adv30_usd, vol30, factors: {name: value}}``, sorted by rank.
    If no model is promoted, the score is the equal-weight composite of the factors that pass the IC screen on the
    full history (in-sample, unvalidated) and ``promoted`` is False.
    """
    import numpy as np
    import pandas as pd

    from app.quant.lab import data as qd
    from app.quant.lab import portfolio as qp
    from app.quant.lab.factors import FACTOR_DOC, FACTORS, build_wide, compute_factors, universe

    panel = qd.load_panel()
    end = pd.Timestamp(as_of) if as_of else pd.Timestamp(panel["date"].max())
    start = end - pd.Timedelta(days=LOOKBACK_DAYS)
    w = build_wide(panel, qd.load_funding_daily(), start=start, end=end)
    facs = compute_factors(w)
    univ = universe(w)
    t = len(w.dates) - 1
    u = univ.iloc[t].to_numpy()
    syms = w.symbols[u]

    model = _read("model.json")
    promoted = model is not None
    names = model["factor_list"] if promoted else FACTORS
    # centred cross-sectional ranks on the last date (same transform as model.build_dataset)
    X = np.zeros((len(syms), len(names)))
    for j, f in enumerate(names):
        v = facs[f].iloc[t][syms]
        X[:, j] = np.nan_to_num((v.rank(pct=True) - (v.notna().sum() + 1) / (2 * max(v.notna().sum(), 1))).to_numpy())
    if promoted:
        score = _score_row(model, X, names)
        used = model.get("signs") or model.get("coef") or {f: None for f in names}
        note = f"promoted {model['model']} model ({model['version']}, h={model['horizon_days']}d)"
        cfg = qp.PortfolioConfig(**model["portfolio"])
    else:
        rep = _read("factor_report.json") or {}
        signs = rep.get("screen_passed") or {"mom_28": 1}
        st = {"model": "composite", "signs": signs}
        from app.quant.lab import model as qm
        score = qm.predict(st, X, names)
        used = signs
        note = ("no model is promoted: this is the equal-weight z-score composite of factors that passed the IC "
                "screen on the full history (in-sample); it did not pass the out-of-sample promotion gate")
        cfg = qp.PortfolioConfig(k=10, side="long", rebalance=7)
    if k:
        cfg.k = k
    stateful = promoted and (cfg.execution != "close" or cfg.band > 0 or cfg.anchor is not None)
    mkt = qp.prepare_market(w, univ, fill=cfg.execution if promoted else "close")
    full = np.full(len(w.symbols), np.nan)
    full[u] = score
    book = None
    if stateful:
        # the promoted book trades on a calendar schedule with a no-trade band: replay it with the same engine as the
        # backtest so today's weights are the holdings the backtest would have, after today's (unfilled) orders
        from app.quant.lab import model as qm
        h = int(model["horizon_days"])
        ds = qm.build_dataset(w, {f: facs[f] for f in names}, univ, horizons=(h,), names=names)
        S = ds.to_matrix(_score_row(model, ds.X, names))
        S[t] = full
        book = qp.live_book(S, mkt, cfg, start=w.dates[max(0, t - REPLAY_DAYS)])
        wts = book["book"]
    else:
        wts = qp.target_weights(full, t, mkt, cfg)
    order = np.argsort(-np.nan_to_num(score, nan=-1e9), kind="stable")
    rows = []
    for rnk, i in enumerate(order, 1):
        s = syms[i]
        j = w.symbols.get_loc(s)
        rows.append({
            "symbol": s, "rank": rnk, "score": None if not np.isfinite(score[i]) else round(float(score[i]), 4),
            "weight": round(float(wts[j]), 4), "side": "long" if wts[j] > 0 else "short" if wts[j] < 0 else None,
            "adv30_usd": round(float(mkt.adv[t, j]), 0), "vol30": None if not np.isfinite(mkt.vol[t, j])
            else round(float(mkt.vol[t, j]), 3),
            "factors": {f: (None if not np.isfinite(x := float(facs[f].iloc[t][s])) else round(x, 5)) for f in FACTORS},
        })
    nz = np.flatnonzero(wts)
    return {
        "as_of": str(end.date()), "data_through": _through(panel),
        "generated": datetime.now(UTC).isoformat(), "promoted": promoted,
        "model": ({k_: model[k_] for k_ in ("model", "version", "horizon_days", "trained_through")} if promoted
                  else None),
        "note": note, "universe_size": int(u.sum()),
        "factors_used": [{"name": f, "sign_or_weight": v, "doc": FACTOR_DOC.get(f, "")} for f, v in used.items()],
        "portfolio": _portfolio_field(cfg, wts, nz, book, w, t),
        "rows": rows,
    }


def _portfolio_field(cfg, wts, nz, book, w, t) -> dict:
    import numpy as np
    import pandas as pd

    out = {"k": cfg.k, "side": cfg.side, "rebalance_days": cfg.rebalance, "vol_target": cfg.vol_target,
           "gross_long": round(float(wts[wts > 0].sum()), 4), "gross_short": round(float(-wts[wts < 0].sum()), 4),
           "positions": int(len(nz)), "execution": cfg.execution, "order_type": cfg.order_type,
           "no_trade_band": cfg.band, "rebalance_anchor": cfg.anchor, "turnover_limit": cfg.turnover_limit}
    if cfg.order_type == "maker":
        out["maker"] = {"fee": cfg.maker_fee, "limit": "the open of the next day",
                        "fill_rule_bps_through": cfg.maker_through_bps, "unfilled": "cancelled at the end of the day"}
    if book is not None:
        d = w.dates[t]
        if cfg.anchor is not None:
            k_ = (-(d - pd.Timestamp(cfg.anchor)).days) % cfg.rebalance
            out["next_rebalance"] = str((d + pd.Timedelta(days=k_ or cfg.rebalance)).date()) if not book[
                "rebalance_today"] else str(d.date())
        out["rebalance_today"] = book["rebalance_today"]
        out["weights_are"] = ("the book after today's orders (filled at the next open)" if book["rebalance_today"]
                              else "the current holdings (no rebalance today)")
        o = book["orders"]
        out["orders"] = [{"symbol": str(w.symbols[i]), "delta_weight": round(float(o[i]), 4)}
                         for i in np.argsort(-np.abs(o)) if o[i] != 0]
        out["turnover_today"] = round(float(np.abs(o).sum()), 4)
    return out


def latest_report() -> dict:
    """Summary for the UI: data, factor report, model ladder, portfolio vs baselines, CPCV/DSR/PBO and the decision.

    Shape: ``{available, generated, data, universe, factors: [...], screen_rule, cost_model, model_ladder,
    portfolio: {selected, baselines, equity_curves, stress}, validation: {dsr, pbo, cpcv, costs_x2_psr}, decision}``.
    """
    man = _read("manifest.json") or {}
    fr = _read("factor_report.json")
    vr = _read("validation_report.json")
    mj = _read("model.json")
    if not fr and not vr:
        return {"available": False, "note": "run app.quant.lab.data --update, .research and .validate first"}
    out: dict = {"available": True, "generated": (vr or fr or {}).get("generated"),
                 "data": {k: man.get(k) for k in ("panel", "funding_daily", "last_update")}
                 | {"validation": (man.get("validation") or {}).get("passed")}}
    if fr and "factors" in fr:
        out["universe"] = fr.get("universe")
        out["screen_rule"] = fr.get("screen_rule")
        out["cost_model"] = fr.get("cost_model")
        out["factors"] = [{
            "name": f, "doc": r["doc"], "ic1": r["ic"]["1"]["mean"], "ic3": r["ic"]["3"]["mean"],
            "ic7": r["ic"]["7"]["mean"], "t_ic7": r["ic"]["7"]["t_nw"],
            "ic7_bull": r["ic7_by_regime"]["btc_above_200d"], "ic7_bear": r["ic7_by_regime"]["btc_below_200d"],
            "ic7_by_year": r["ic7_by_year"], "rank_autocorr": r["turnover"]["rank_autocorr_1d"],
            "ls_weekly_net_sharpe": r["quintile_ls_weekly"]["ls_net"]["sharpe"],
            "ls_daily_net_sharpe": r["quintile_ls_daily"]["ls_net"]["sharpe"], "passes": r["passes"],
        } for f, r in fr["factors"].items()]
        out["n_factors_passing"] = fr.get("n_passing")
    if vr:
        out["oos_start"] = vr["oos_start"]
        out["model_ladder"] = vr["ladder"]
        out["portfolio"] = {"selected": vr["selected"], "baselines": vr["baselines"],
                            "equity_curves": vr["equity_curves"], "stress": vr["stress"],
                            "grid_size": len(vr["grid"])}
        out["validation"] = {"dsr": vr["dsr"], "pbo": vr["pbo"], "cpcv": vr["cpcv"], "costs_x2_psr": vr["costs_x2_psr"]}
        out["decision"] = vr["decision"]
    ea, eb = _read("exec_retest_report.json"), _read("ctrend_conformal_report.json")
    if ea or eb:
        out["retests"] = {
            "execution": ea and {"prereg": ea.get("prereg"), "selected": ea["selected"]["key"],
                                 "sharpe": ea["selected"]["sharpe"], "dsr": ea["dsr"], "pbo": ea["pbo"],
                                 "decision": ea["decision"]},
            "ctrend_conformal": eb and {"prereg": eb.get("prereg"), "ctrend_pass_test": eb["ctrend"]["pass_test"],
                                        "ctrend_ladder": eb["ctrend"]["ladder"],
                                        "conformal_coverage": eb["conformal"]["coverage"],
                                        "gates": {k: {"passed": v["passed"], "dsr_p": v["dsr"]["p_value"]}
                                                  for k, v in eb["gates"].items()},
                                        "decision": eb["decision"]},
        }
    out["promoted_model"] = ({k: mj[k] for k in ("model", "version", "horizon_days", "trained_through", "portfolio")}
                             if mj else None)
    return out


def _through(panel) -> str:
    """The panel's last full day, judged by BTC: the archive publishes coin by coin, so the newest date on any coin can
    run ahead of the market (which made the daily loop think the update was done while most coins still lagged)."""
    import pandas as pd

    btc = panel.loc[panel["symbol"] == "BTCUSDT", "date"]
    return str(pd.Timestamp((btc if len(btc) else panel["date"]).max()).date())
