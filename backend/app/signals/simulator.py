"""Trade outcome simulation shared by live signal tracking and the backtester (identical rules).

Rules (documented in docs/STRATEGIES.md#execution-model):
- Entry: limit inside the entry zone. Long fills when bar low <= entry_high at min(open, entry_high);
  short mirror. Slippage added against us. If the zone is never reached within `expiry_bars`, EXPIRED.
- If the stop is touched before a fill the setup is INVALIDATED (never entered).
- Same-bar ambiguity (stop and target both inside one bar): assume the STOP happened first (conservative).
- Exits: 1/3 at TP1, 1/3 at TP2, 1/3 at TP3; after TP1 the stop moves to breakeven (fill price).
- Time stop: close at bar close after `max_hold_bars` → TIME_EXIT.
- Costs: taker fee on entry and each exit, slippage bps on entry and stop exits; funding is applied by the
  caller for perps when funding history is available.
"""
from dataclasses import asdict, dataclass, field


@dataclass
class TradeSim:
    direction: str
    entry_low: float
    entry_high: float
    stop: float
    targets: list[float]
    expiry_bars: int = 12
    max_hold_bars: int = 96
    fee: float = 0.0005
    slip_bps: float = 2.0
    state: str = "CONFIRMED"  # CONFIRMED -> ACTIVE -> terminal
    bars_waiting: int = 0
    bars_held: int = 0
    fill: float | None = None
    fill_time: int | None = None
    cur_stop: float | None = None
    remaining: float = 1.0
    realized_r: float = 0.0
    tps_hit: int = 0
    mfe_r: float = 0.0
    mae_r: float = 0.0
    exit_price: float | None = None
    exit_time: int | None = None
    outcome: str | None = None
    events: list[dict] = field(default_factory=list)

    @property
    def s(self) -> int:
        return 1 if self.direction == "LONG" else -1

    @property
    def risk(self) -> float:
        """R unit = *planned* risk (entry-zone midpoint to stop). Using the actual fill would make R explode when
        price gaps through the zone and fills right next to the stop (risk -> 0)."""
        return abs((self.entry_low + self.entry_high) / 2 - self.stop)

    def _r(self, price: float) -> float:
        return self.s * (price - self.fill) / self.risk

    def _exit(self, portion: float, price: float, t: int, slip: bool) -> None:
        px = price * (1 - self.s * self.slip_bps / 1e4) if slip else price
        cost_r = (px * self.fee) / self.risk
        self.realized_r += portion * (self._r(px) - cost_r)
        self.remaining -= portion
        self.exit_price, self.exit_time = px, t

    def _event(self, t: int, kind: str, price: float, reason: str) -> None:
        self.events.append({"time": t, "event": kind, "price": price, "reason": reason})

    @property
    def terminal(self) -> bool:
        return self.outcome is not None

    def on_bar(self, t: int, o: float, h: float, low: float, c: float) -> list[dict]:
        if self.terminal:
            return []
        n0 = len(self.events)
        s = self.s
        if self.state == "CONFIRMED":
            self.bars_waiting += 1
            stop_touched = (low <= self.stop) if s > 0 else (h >= self.stop)
            reach = (low <= self.entry_high) if s > 0 else (h >= self.entry_low)
            gapped_through_stop = (o <= self.stop) if s > 0 else (o >= self.stop)
            if gapped_through_stop:
                self.outcome = "INVALIDATED"
                self._event(t, "INVALIDATED", o, "bar opened beyond the stop before any fill")
                return self.events[n0:]
            if reach:
                px = min(o, self.entry_high) if s > 0 else max(o, self.entry_low)
                self.fill = px * (1 + s * self.slip_bps / 1e4)
                self.fill_time, self.cur_stop, self.state = t, self.stop, "ACTIVE"
                self.realized_r -= (self.fill * self.fee) / self.risk
                self._event(t, "ENTERED", self.fill, "price reached entry zone")
                # gap through the stop on the same bar: conservative stop
                if stop_touched:
                    self._exit(self.remaining, self.stop if (o - self.stop) * s > 0 else o, t, True)
                    self.outcome = "STOP_HIT"
                    self._event(t, "STOP_HIT", self.exit_price, "stop hit on entry bar (conservative)")
                    return self.events[n0:]
            elif stop_touched:
                self.outcome = "INVALIDATED"
                self._event(t, "INVALIDATED", self.stop, "stop level traded before entry")
                return self.events[n0:]
            elif self.bars_waiting >= self.expiry_bars:
                self.outcome = "EXPIRED"
                self._event(t, "EXPIRED", c, f"entry not reached within {self.expiry_bars} bars")
                return self.events[n0:]
            if self.state != "ACTIVE":
                return self.events[n0:]
            # continue evaluating targets for the fill bar below (stop already handled)
        self.bars_held += 1
        fav = h if s > 0 else low
        adv = low if s > 0 else h
        self.mfe_r = max(self.mfe_r, self._r(fav))
        self.mae_r = min(self.mae_r, self._r(adv))
        if ((low <= self.cur_stop) if s > 0 else (h >= self.cur_stop)) and self.fill_time != t:
            px = self.cur_stop if (o - self.cur_stop) * s > 0 else o  # gap -> open
            self._exit(self.remaining, px, t, True)
            self.outcome = "STOP_HIT" if self.tps_hit == 0 else f"TP{self.tps_hit}_THEN_STOP"
            self._event(t, "STOP_HIT", self.exit_price, "stop reached" if self.tps_hit == 0
                        else "breakeven/trailing stop reached after partial take-profit")
            return self.events[n0:]
        while self.tps_hit < len(self.targets):
            tp = self.targets[self.tps_hit]
            if not ((h >= tp) if s > 0 else (low <= tp)):
                break
            portion = 1 / len(self.targets) if self.tps_hit < len(self.targets) - 1 else self.remaining
            self._exit(portion, tp, t, False)
            self.tps_hit += 1
            self._event(t, f"TP{self.tps_hit}_HIT", tp, f"take-profit {self.tps_hit} reached")
            if self.tps_hit == 1:
                self.cur_stop = self.fill
        if self.tps_hit == len(self.targets):
            self.outcome = "TARGET_HIT"
            return self.events[n0:]
        if self.bars_held >= self.max_hold_bars:
            self._exit(self.remaining, c, t, True)
            self.outcome = "TIME_EXIT"
            self._event(t, "TIME_EXIT", c, f"max hold {self.max_hold_bars} bars")
        return self.events[n0:]

    def close_manual(self, t: int, price: float, reason: str = "manual") -> None:
        if self.terminal:
            return
        if self.state == "ACTIVE":
            self._exit(self.remaining, price, t, True)
        self.outcome = "CLOSED_MANUAL" if self.state == "ACTIVE" else "CANCELLED"
        self._event(t, self.outcome, price, reason)

    def d(self) -> dict:
        return asdict(self)
