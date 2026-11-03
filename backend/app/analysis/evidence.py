"""Evidence aggregation with redundancy control.

Correlated signals (e.g. RSI, Stoch RSI, Williams %R) belong to one *family*; a family contributes one
bounded score regardless of how many members agree. Family weights are documented priors until the
research lab replaces them with measured out-of-sample contributions.
"""
from dataclasses import asdict, dataclass

FAMILY_WEIGHTS = {
    "trend": 1.0,
    "structure": 1.0,
    "mtf_alignment": 1.0,
    "momentum": 0.6,
    "volume_flow": 0.6,
    "liquidity": 0.6,
    "derivatives": 0.5,
    "ml": 1.0,  # only populated when a model is accepted (validated) in the registry
    "news": 0.3,
    "elliott": 0.2,  # low weight: subjective, unvalidated
}


@dataclass
class Evidence:
    family: str
    name: str
    direction: int  # +1 bullish, -1 bearish, 0 neutral/context
    strength: float  # 0..1
    detail: str
    basis: str = "observed"  # observed | derived | inferred | model
    ref: str | None = None  # id of chart object / event

    def d(self) -> dict:
        return asdict(self)


def aggregate(items: list[Evidence]) -> dict:
    fams: dict[str, list[Evidence]] = {}
    for e in items:
        fams.setdefault(e.family, []).append(e)
    family_scores = {}
    for f, es in fams.items():
        directional = [e for e in es if e.direction != 0]
        if not directional:
            family_scores[f] = {"score": 0.0, "n": len(es), "bull": 0, "bear": 0}
            continue
        s = sum(e.direction * e.strength for e in directional) / len(directional)
        family_scores[f] = {"score": max(-1.0, min(1.0, s)), "n": len(es),
                            "bull": sum(1 for e in directional if e.direction > 0),
                            "bear": sum(1 for e in directional if e.direction < 0)}
    wsum = sum(FAMILY_WEIGHTS.get(f, 0.3) for f, v in family_scores.items() if v["bull"] + v["bear"] > 0)
    total = sum(FAMILY_WEIGHTS.get(f, 0.3) * v["score"] for f, v in family_scores.items())
    bias = total / wsum if wsum else 0.0
    supporting = sum(1 for v in family_scores.values() if v["score"] > 0.15)
    opposing = sum(1 for v in family_scores.values() if v["score"] < -0.15)
    return {
        "bias": round(bias, 4),
        "direction": "bullish" if bias > 0.15 else "bearish" if bias < -0.15 else "neutral",
        "families": family_scores,
        "independent_families_bull": supporting,
        "independent_families_bear": opposing,
        "weights": FAMILY_WEIGHTS,
        "method": "family-capped weighted mean (weights are documented priors)",
    }
