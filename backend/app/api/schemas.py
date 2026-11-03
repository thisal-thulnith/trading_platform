from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class DrawingIn(BaseModel):
    symbol: str = Field(pattern=r"^[A-Z0-9]{4,20}$")
    timeframe: str
    kind: Literal["zone", "hline", "vline", "trendline", "ray", "channel", "fib", "label", "marker", "wave",
                  "position", "hide", "rect"]
    payload: dict
    parent_id: str | None = None  # set when the user edits/hides an AI annotation
    hidden: bool = False

    @field_validator("payload")
    @classmethod
    def small(cls, v):
        if len(str(v)) > 20_000:
            raise ValueError("payload too large")
        return v


class DrawingPatch(BaseModel):
    payload: dict | None = None
    hidden: bool | None = None


class LayoutIn(BaseModel):
    name: str = Field(max_length=64)
    payload: dict


class WatchlistIn(BaseModel):
    name: str = Field(max_length=64)
    symbols: list[str] = Field(max_length=100)


class OnboardingIn(BaseModel):
    starting_capital: float = Field(gt=0, le=1e9)
    base_currency: str = "USDT"
    exchange: str = "binance"
    market: Literal["spot", "perp"] = "perp"
    risk_per_trade_pct: float = Field(1.0, gt=0, le=5)
    max_drawdown_pct: float = Field(20, gt=0, le=90)
    timeframes: list[str] = ["1h", "4h"]
    max_open_positions: int = Field(5, ge=1, le=50)
    max_leverage: float = Field(3, ge=1, le=20)
    max_portfolio_risk_pct: float = Field(5, gt=0, le=30)
    notifications: dict = {"in_app": True, "browser": True, "telegram": False, "email": False}
    auto_follow_signals: bool = False
    reset: bool = False


class PositionIn(BaseModel):
    symbol: str
    side: Literal["LONG", "SHORT"]
    stop: float = Field(gt=0)
    take_profit: float | None = Field(None, gt=0)
    signal_id: str | None = None


class SizingIn(BaseModel):
    symbol: str
    side: Literal["LONG", "SHORT"]
    entry: float = Field(gt=0)
    stop: float = Field(gt=0)
    equity: float | None = Field(None, gt=0)
    risk_per_trade_pct: float | None = Field(None, gt=0, le=10)
    max_leverage: float | None = Field(None, ge=1, le=125)
    market: Literal["spot", "perp"] | None = None


class BacktestIn(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=10)
    timeframe: str = "1h"
    strategies: list[str] | None = None
    bars: int = Field(2000, ge=500, le=8000)
    step: int = Field(1, ge=1, le=12)
    initial_equity: float = 10_000
    risk_per_trade_pct: float = Field(1.0, gt=0, le=5)
    max_concurrent: int = Field(5, ge=1, le=20)
    fee: float = Field(0.0005, ge=0, le=0.01)
    slip_bps: float = Field(2.0, ge=0, le=100)


class CloseIn(BaseModel):
    reason: str = Field("manual", max_length=200)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    symbol: str | None = None
    timeframe: str | None = "1h"
    history: list[dict] = Field(default_factory=list, max_length=20)
