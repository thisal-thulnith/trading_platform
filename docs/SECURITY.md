# Security model

- **No trading keys.** The platform never asks for exchange API keys and never places orders. Withdrawal
  permissions are irrelevant by design. Live execution is out of scope; adding it would need separate explicit
  authorisation, key vaulting, kill switches and risk limits.
- **Secrets** come only from environment variables (`AIT_*`, see `.env.example`). `.env` is git-ignored.
  The browser only ever sees `NEXT_PUBLIC_API_URL`. The LLM key, Telegram token and SMTP credentials stay on
  the server.
- **Authentication:** bcrypt (cost 12) password hashes and HS256 JWTs (24h default). The app refuses to start
  with `AIT_ENV=prod` and the default `AIT_JWT_SECRET`. The first registered user becomes admin; later users
  are `user`.
- **Authorisation:** drawings, layouts, watchlists, portfolios and backtests are scoped to the owner. Only an
  admin can close platform signals.
- **Rate limiting:** auth 10/min/IP and chat 30/min/IP (in-memory; use Redis or a proxy limiter for several
  instances). At most 2 concurrent backtests.
- **Input validation:** Pydantic schemas with bounds (risk ≤ 5%, leverage ≤ 20, payload ≤ 20 KB, symbols
  `^[A-Z0-9]{4,20}$`). Indicator parameters are bounds-checked.
- **LLM safety:** the model can only call whitelisted tools with strict JSON schemas. Chart actions are
  re-validated (known annotation ids, plausible price ranges, consistent scenarios). Tool output is size-capped.
  There is no code execution and no free-form access to the database or network. Server-side refusal fallback
  is enabled.
- **Audit log:** registration, logins (including failures), onboarding, paper trades, backtests and signal
  closes are recorded in `audit_logs`.
- **Exchange terms:** Binance public market data only, accessed from permitted jurisdictions (Binance returns
  HTTP 451 to US IPs). Do not route around geo-restrictions.
- **Hardening to do before public exposure:** TLS termination (reverse proxy), CSP headers on the frontend,
  refresh-token rotation, Redis-backed rate limits, a database backup schedule (see DEPLOYMENT.md), and a
  dependency vulnerability scan in CI.
