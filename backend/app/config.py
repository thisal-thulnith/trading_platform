"""Runtime configuration. All values can be overridden with environment variables (prefix AIT_)."""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_UNIVERSE = [
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT",
    "LTC", "BCH", "TRX", "SUI", "TON", "XLM", "HBAR", "NEAR", "UNI", "APT",
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AIT_", env_file=".env", extra="ignore")

    env: str = "dev"
    database_url: str = "sqlite+aiosqlite:///./data/aitrade.db"
    redis_url: str | None = None
    jwt_secret: str = "dev-only-change-me"
    jwt_ttl_minutes: int = 60 * 24
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:3100", "http://127.0.0.1:3100"]

    # Market data
    quote_asset: str = "USDT"
    candidate_universe: list[str] = Field(default_factory=lambda: list(DEFAULT_UNIVERSE))
    universe_size: int = 20
    # Timeframes kept warm by the live engine. All 12 supported timeframes can be requested on demand.
    stream_timeframes: list[str] = ["5m", "15m", "1h", "4h", "1d"]
    scan_timeframes: list[str] = ["15m", "1h", "4h"]
    backfill_bars: int = 1000
    binance_spot_rest: str = "https://api.binance.com"
    binance_spot_rest_fallback: str = "https://data-api.binance.vision"
    binance_futures_rest: str = "https://fapi.binance.com"
    binance_spot_ws: str = "wss://stream.binance.com:9443/stream"
    binance_futures_ws: str = "wss://fstream.binance.com/market/stream"  # routed path required since 2026-04
    bybit_rest: str = "https://api.bybit.com"
    okx_rest: str = "https://www.okx.com"
    coingecko_rest: str = "https://api.coingecko.com/api/v3"
    coingecko_api_key: str | None = None

    # Engine toggles
    run_engine: bool = True
    run_news: bool = True
    scan_interval_s: int = 60
    derivatives_poll_s: int = 60
    universe_refresh_s: int = 6 * 3600
    news_poll_s: int = 300

    # Costs used for signals / paper / backtest (Binance USDⓈ-M VIP0 defaults, verify per account)
    taker_fee: float = 0.0005
    maker_fee: float = 0.0002
    slippage_bps: float = 2.0
    # Exit profile per timeframe (app/strategies/exits.py). Every scanned timeframe uses a minimum 1:2 reward/risk.
    exit_profiles: dict[str, str] = {"15m": "edge_tp2r", "1h": "edge_tp2r", "4h": "edge_tp2r"}

    # LLM (optional). The system operates fully without it.
    anthropic_api_key: str | None = None
    llm_model: str = "claude-opus-5-5"
    llm_effort: str = "medium"
    # Alternative provider: OpenAI (used when no Anthropic key is set, or when llm_provider="openai")
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.5"
    llm_provider: str = "auto"  # auto | anthropic | openai

    # Notifications (optional)
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    smtp_url: str | None = None

    model_dir: str = "./data/models"


@lru_cache
def get_settings() -> Settings:
    return Settings()
