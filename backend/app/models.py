"""Persistent data model.

Immutability rules:
- Confirmed signal core fields (direction, entry, stop, targets, evidence snapshot) are never updated.
- Signal history is append-only via SignalEvent.
- AnalysisSnapshot rows are immutable; reruns create new versions.
- AI-generated drawings (origin='ai') are never mutated by user edits: editing an AI drawing creates a
  user copy (origin='user', parent_id -> AI drawing) and hides the AI original for that user only.
"""
import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


class Candle(Base):
    __tablename__ = "candles"
    symbol: Mapped[str] = mapped_column(String(24), primary_key=True)
    market: Mapped[str] = mapped_column(String(8), primary_key=True)  # spot | perp
    timeframe: Mapped[str] = mapped_column(String(4), primary_key=True)
    open_time: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # epoch ms UTC
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    quote_volume: Mapped[float] = mapped_column(Float, default=0.0)
    trades: Mapped[int] = mapped_column(Integer, default=0)
    taker_buy_base: Mapped[float] = mapped_column(Float, default=0.0)
    taker_buy_quote: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(16), default="binance")


class DerivativesSnapshot(Base):
    __tablename__ = "derivatives_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(24), index=True)
    ts: Mapped[int] = mapped_column(BigInteger, index=True)
    mark_price: Mapped[float | None] = mapped_column(Float)
    index_price: Mapped[float | None] = mapped_column(Float)
    spot_price: Mapped[float | None] = mapped_column(Float)
    funding_rate: Mapped[float | None] = mapped_column(Float)
    next_funding_time: Mapped[int | None] = mapped_column(BigInteger)
    open_interest: Mapped[float | None] = mapped_column(Float)
    open_interest_value: Mapped[float | None] = mapped_column(Float)
    long_short_ratio: Mapped[float | None] = mapped_column(Float)
    taker_buy_sell_ratio: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(16), default="binance")


class UniverseSnapshot(Base):
    __tablename__ = "universe_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    selected: Mapped[list] = mapped_column(JSON)
    rankings: Mapped[list] = mapped_column(JSON)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)


class Watchlist(Base):
    __tablename__ = "watchlists"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    symbols: Mapped[list] = mapped_column(JSON, default=list)


class ChartLayout(Base):
    __tablename__ = "chart_layouts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AnalysisSnapshot(Base):
    __tablename__ = "analysis_snapshots"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    symbol: Mapped[str] = mapped_column(String(24), index=True)
    timeframe: Mapped[str] = mapped_column(String(4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    as_of: Mapped[int] = mapped_column(BigInteger)  # open_time of last *closed* candle used
    engine_version: Mapped[str] = mapped_column(String(16))
    data_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)


class Drawing(Base):
    __tablename__ = "drawings"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True, nullable=True)
    symbol: Mapped[str] = mapped_column(String(24), index=True)
    timeframe: Mapped[str] = mapped_column(String(4))
    origin: Mapped[str] = mapped_column(String(8))  # ai | user
    parent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # AI annotation edited by user
    snapshot_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Signal(Base):
    __tablename__ = "signals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    symbol: Mapped[str] = mapped_column(String(24), index=True)
    timeframe: Mapped[str] = mapped_column(String(4))
    strategy: Mapped[str] = mapped_column(String(48))
    direction: Mapped[str] = mapped_column(String(5))  # LONG | SHORT
    state: Mapped[str] = mapped_column(String(16), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reference_price: Mapped[float] = mapped_column(Float)
    entry_low: Mapped[float | None] = mapped_column(Float)
    entry_high: Mapped[float | None] = mapped_column(Float)
    stop: Mapped[float | None] = mapped_column(Float)
    tp1: Mapped[float | None] = mapped_column(Float)
    tp2: Mapped[float | None] = mapped_column(Float)
    tp3: Mapped[float | None] = mapped_column(Float)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    snapshot_id: Mapped[str | None] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    # Outcome (written once, when the signal reaches a terminal state)
    outcome: Mapped[str | None] = mapped_column(String(24))
    entered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    entry_fill: Mapped[float | None] = mapped_column(Float)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_price: Mapped[float | None] = mapped_column(Float)
    r_multiple: Mapped[float | None] = mapped_column(Float)
    mfe_r: Mapped[float | None] = mapped_column(Float)
    mae_r: Mapped[float | None] = mapped_column(Float)
    dedupe_key: Mapped[str] = mapped_column(String(128), index=True)

    __table_args__ = (Index("ix_signals_symbol_state", "symbol", "state"),)


class SignalEvent(Base):
    __tablename__ = "signal_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[str] = mapped_column(ForeignKey("signals.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    from_state: Mapped[str | None] = mapped_column(String(16))
    to_state: Mapped[str] = mapped_column(String(16))
    price: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text)
    snapshot_id: Mapped[str | None] = mapped_column(String(32))
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)  # None = broadcast
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    category: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(8), default="info")
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text, default="")
    symbol: Mapped[str | None] = mapped_column(String(24))
    timeframe: Mapped[str | None] = mapped_column(String(4))
    signal_id: Mapped[str | None] = mapped_column(String(32))
    snapshot_id: Mapped[str | None] = mapped_column(String(32))
    dedupe_key: Mapped[str] = mapped_column(String(160), unique=True)
    read: Mapped[bool] = mapped_column(Boolean, default=False)


class PaperAccount(Base):
    __tablename__ = "paper_accounts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    base_currency: Mapped[str] = mapped_column(String(8), default="USDT")
    starting_capital: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    risk: Mapped[dict] = mapped_column(JSON, default=dict)


class PaperPosition(Base):
    __tablename__ = "paper_positions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(ForeignKey("paper_accounts.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(24))
    market: Mapped[str] = mapped_column(String(8), default="perp")
    side: Mapped[str] = mapped_column(String(5))
    qty: Mapped[float] = mapped_column(Float)
    leverage: Mapped[float] = mapped_column(Float, default=1.0)
    entry_price: Mapped[float] = mapped_column(Float)
    stop: Mapped[float | None] = mapped_column(Float)
    take_profit: Mapped[float | None] = mapped_column(Float)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_price: Mapped[float | None] = mapped_column(Float)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    funding: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(8), default="open")
    close_reason: Mapped[str | None] = mapped_column(String(24))
    signal_id: Mapped[str | None] = mapped_column(String(32))


class EquityPoint(Base):
    __tablename__ = "equity_points"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("paper_accounts.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    equity: Mapped[float] = mapped_column(Float)


class NewsItem(Base):
    __tablename__ = "news_items"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    source: Mapped[str] = mapped_column(String(64))
    url: Mapped[str] = mapped_column(String(1024), unique=True)
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    coins: Mapped[list] = mapped_column(JSON, default=list)
    sentiment: Mapped[float] = mapped_column(Float, default=0.0)
    event_type: Mapped[str] = mapped_column(String(32), default="general")
    credibility: Mapped[float] = mapped_column(Float, default=0.5)
    impact: Mapped[str] = mapped_column(String(8), default="low")
    cluster_key: Mapped[str] = mapped_column(String(64), index=True)
    corroborations: Mapped[int] = mapped_column(Integer, default=1)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    config: Mapped[dict] = mapped_column(JSON)
    config_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(12), default="running")
    results: Mapped[dict] = mapped_column(JSON, default=dict)


class ModelRecord(Base):
    __tablename__ = "model_records"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    target: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(12))  # candidate | accepted | rejected | retired
    features: Mapped[list] = mapped_column(JSON)
    params: Mapped[dict] = mapped_column(JSON)
    metrics: Mapped[dict] = mapped_column(JSON)
    artifact_path: Mapped[str | None] = mapped_column(String(512))


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    user_id: Mapped[str | None] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class Forecast(Base):
    """Forecast Ledger: every probability any forecaster (agent, model, team) states, and what then happened.

    Event: "the close H bars after `created_ms` is above `ref_price`" (direction-neutral, always resolvable).
    Rows are written once; only the outcome fields are filled when the horizon passes.
    """
    __tablename__ = "forecasts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    created_ms: Mapped[int] = mapped_column(BigInteger, index=True)
    session_id: Mapped[str | None] = mapped_column(String(32), index=True)
    source: Mapped[str] = mapped_column(String(32), index=True)  # agent id | quant | team
    brain: Mapped[str] = mapped_column(String(16), default="rules")  # llm | rules | model
    symbol: Mapped[str] = mapped_column(String(24), index=True)
    timeframe: Mapped[str] = mapped_column(String(4))
    regime: Mapped[str | None] = mapped_column(String(24))
    p_up: Mapped[float] = mapped_column(Float)
    ref_price: Mapped[float] = mapped_column(Float)
    horizon_bars: Mapped[int] = mapped_column(Integer)
    due_ms: Mapped[int] = mapped_column(BigInteger, index=True)
    outcome: Mapped[int | None] = mapped_column(Integer)  # 1 = closed above ref_price, 0 = not
    exit_price: Mapped[float | None] = mapped_column(Float)
    resolved_ms: Mapped[int | None] = mapped_column(BigInteger)


class MyTrade(Base):
    """A trade the user chose to take (from a system-account ticket or by hand), tracked like a Binance position card:
    marked to market, managed by the daily-trend rules, and kept after the close with its realised result."""
    __tablename__ = "my_trades"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    symbol: Mapped[str] = mapped_column(String(24))
    venue: Mapped[str] = mapped_column(String(8), default="spot")  # spot | futures
    side: Mapped[str] = mapped_column(String(5), default="LONG")
    leverage: Mapped[float] = mapped_column(Float, default=1.0)
    entry: Mapped[float] = mapped_column(Float)
    qty: Mapped[float] = mapped_column(Float)
    stop: Mapped[float] = mapped_column(Float)
    target: Mapped[float] = mapped_column(Float)
    risk_usd: Mapped[float] = mapped_column(Float)
    budget_usd: Mapped[float] = mapped_column(Float, default=100.0)
    mode: Mapped[str] = mapped_column(String(16), default="conservative")
    partial_done: Mapped[bool] = mapped_column(Boolean, default=False)
    partial_price: Mapped[float | None] = mapped_column(Float)
    partial_qty: Mapped[float | None] = mapped_column(Float)
    partial_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(8), default="open")  # open | closed
    exit_price: Mapped[float | None] = mapped_column(Float)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_reason: Mapped[str | None] = mapped_column(String(24))
    pnl_usd: Mapped[float | None] = mapped_column(Float)
    r_multiple: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(16), default="manual")  # system | manual
    note: Mapped[str | None] = mapped_column(Text)
