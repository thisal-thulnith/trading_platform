# Deployment

## Local development
See README. SQLite at `backend/data/aitrade.db`. Backend on :8100, frontend on :3100.

## Docker Compose (single host)
```bash
cp .env.example .env     # set POSTGRES_PASSWORD, AIT_JWT_SECRET (openssl rand -hex 32), public URLs
docker compose up -d --build
docker compose logs -f backend
```
Services: `db` (TimescaleDB pg17, volume `dbdata`), `backend` (API + engine, volume `models`), `frontend`.

## Production checklist
- Host **outside geo-restricted regions**: Binance returns HTTP 451 to US IPs, including many US cloud
  regions. Pick an EU or Asia region, for example.
- Put a TLS reverse proxy (Caddy or nginx) in front. Proxy `/ws` with WebSocket upgrade headers.
- Run exactly **one** backend process (the engine is a singleton). Scale API replicas only after moving the bus
  to Redis.
- Set `AIT_ENV=prod` and a strong `AIT_JWT_SECRET`. Restrict `AIT_CORS_ORIGINS`.
- Backups: `docker compose exec db pg_dump -U aitrade aitrade | gzip > backup_$(date +%F).sql.gz` daily, kept
  off-host. Test restores.
- Monitoring: poll `/api/system/health` (stream ages, failed tasks). Data-feed failures also create in-app,
  Telegram and e-mail alerts when configured.
- Optional: `AIT_ANTHROPIC_API_KEY` for LLM narratives and chat, `AIT_TELEGRAM_BOT_TOKEN` + `AIT_TELEGRAM_CHAT_ID`,
  `AIT_SMTP_URL`.

## Configuration (env, prefix `AIT_`)
`DATABASE_URL`, `JWT_SECRET`, `CORS_ORIGINS`, `UNIVERSE_SIZE` (20), `STREAM_TIMEFRAMES` (5m,15m,1h,4h,1d),
`SCAN_TIMEFRAMES` (15m,1h,4h), `BACKFILL_BARS` (1000), `RUN_ENGINE`, `RUN_NEWS`, `TAKER_FEE`, `SLIPPAGE_BPS`,
`LLM_MODEL` (claude-opus-5-5), `LLM_EFFORT` (medium), `COINGECKO_API_KEY`, `MODEL_DIR`. See `app/config.py`.

## Retraining models
```bash
docker compose exec backend /app/.venv/bin/python -m app.ml.train --tf 1h --horizon 24 --days 540
```
Models are registered as accepted or rejected. Only accepted models are served, and the engine picks up the
newest accepted model automatically.
