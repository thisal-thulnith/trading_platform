"""Quant lab: survivorship-free Binance panel, factor research, model ladder, portfolio and validation.

See docs/QUANT_PLAN.md (phases 1-3). Public entry points:

- ``live_ranking(as_of=None) -> dict``  today's universe ranked by the promoted model (or the unpromoted composite)
- ``latest_report() -> dict``            factor report + model ladder + backtest + CPCV/DSR/PBO + promotion decision
"""
from app.quant.lab.live import latest_report, live_ranking

__all__ = ["latest_report", "live_ranking"]
