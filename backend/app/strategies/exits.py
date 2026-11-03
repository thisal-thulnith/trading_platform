"""Exit profiles: how a validated setup is turned into an order plan (entry price, targets, quality floor).

Shared by live signals (signals/manager.py), the backtester and the exit study, so research and live trading use
identical rules. Evidence for each profile: docs/STRATEGIES.md#exit-profile-study.
"""
import copy

EXIT_PROFILES: dict[str, dict] = {
    # strategy-native: limit anywhere in the entry zone, three structural targets
    "default": {"entry": "zone", "targets": None, "min_quality": None},
    # single limit at the zone edge nearest price (risk measured from it, so a stop-out costs ~1R), one take-profit
    # at +1R, and only setups with validation quality >= 50
    "edge_tp1r_q50": {"entry": "edge", "targets": [1.0], "min_quality": 50.0},
    # 1:2 minimum: single limit at the zone edge, one take-profit at +2R
    "edge_tp2r": {"entry": "edge", "targets": [2.0], "min_quality": None},
}


def apply_exit_profile(c: dict, name: str | None) -> dict:
    """Return a copy of strategy candidate `c` with the profile applied. Targets are always 3 long (repeated when the
    profile has fewer) so persisted signals keep tp1..tp3; the simulator closes everything on the first hit."""
    prof = EXIT_PROFILES.get(name or "default", EXIT_PROFILES["default"])
    if prof["entry"] == "zone" and not prof["targets"] and prof["min_quality"] is None:
        return c
    c = copy.deepcopy(c)
    long = c["direction"] == "LONG"
    if prof["entry"] == "edge":
        edge = c["entry_high"] if long else c["entry_low"]
        c["entry_low"] = c["entry_high"] = edge
    if prof["targets"]:
        ref = c["entry_high"] if long else c["entry_low"]
        risk = abs(ref - c["stop"])
        sgn = 1 if long else -1
        tps = [ref + sgn * m * risk for m in prof["targets"]]
        c["targets"] = (tps + [tps[-1]] * 3)[:3]
    c["exit_profile"] = name
    v = c.get("validation")
    if v is not None and prof["min_quality"] is not None:
        ok = (v.get("quality_score") or 0) >= prof["min_quality"]
        v["checks"] = [*v.get("checks", []), {"name": "exit_profile_quality",
                                             "passed": ok, "detail": f"quality {v.get('quality_score', 0):.0f} vs "
                                             f"minimum {prof['min_quality']:.0f} for exit profile {name}"}]
        v["passed"] = bool(v.get("passed")) and ok
    return c


def min_quality(name: str | None) -> float | None:
    return EXIT_PROFILES.get(name or "default", EXIT_PROFILES["default"])["min_quality"]
