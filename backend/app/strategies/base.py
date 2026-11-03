"""Strategy framework.

A strategy inspects an analysis snapshot (deterministic, closed-bar data only) and returns zero or more
SetupCandidates. Conditions are split into:
  context  — market conditions the setup needs (regime, trend, location)
  setup    — the pattern itself is present
  trigger  — the confirmation event
Stage = forming (context+some setup), pre_signal (all context+setup, waiting for trigger), confirmed (all).
"""
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Condition:
    name: str
    kind: str  # context | setup | trigger
    met: bool
    detail: str = ""

    def d(self) -> dict:
        return asdict(self)


@dataclass
class SetupCandidate:
    strategy: str
    direction: str  # LONG | SHORT
    conditions: list[Condition]
    entry_low: float
    entry_high: float
    stop: float
    targets: list[float]
    invalidation: str
    anchor_id: str  # id of the chart object / event the setup is built around (dedupe key)
    expiry_bars: int = 12
    expected_hold_bars: int = 24
    notes: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def stage(self) -> str:
        ctx = [c for c in self.conditions if c.kind == "context"]
        setup = [c for c in self.conditions if c.kind == "setup"]
        trig = [c for c in self.conditions if c.kind == "trigger"]
        if not all(c.met for c in ctx):
            return "none"
        if all(c.met for c in setup) and all(c.met for c in trig):
            return "confirmed"
        if all(c.met for c in setup):
            return "pre_signal"
        if any(c.met for c in setup):
            return "forming"
        return "none"

    @property
    def missing(self) -> list[str]:
        return [c.name for c in self.conditions if not c.met]

    def d(self) -> dict:
        out = asdict(self)
        out["stage"] = self.stage
        out["missing"] = self.missing
        return out


@dataclass
class StrategySpec:
    id: str
    name: str
    family: str
    description: str
    regimes: list[str]
    required_data: list[str]
    entry: str
    exit: str
    invalidation: str
    stop_rule: str
    target_rule: str
    evidence_grade: str  # see docs/RESEARCH.md
    backtestable: bool = True

    def d(self) -> dict:
        return asdict(self)


class Strategy:
    spec: StrategySpec

    def evaluate(self, snap: dict) -> list[SetupCandidate]:  # pragma: no cover - interface
        raise NotImplementedError
