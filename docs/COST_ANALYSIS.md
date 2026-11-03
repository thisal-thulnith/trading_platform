# Cost analysis (estimates, Nov 2026)

| Item | Free option | Typical paid | Notes |
|---|---|---|---|
| Market data | Binance public REST/WS — $0 | — | Within limits: about 300 weight/min in steady state vs 6000 allowed |
| Market caps | CoinGecko Demo — $0 (100 calls/min, 10k/month, attribution) | Analyst plan for commercial use | We call it once per 6h refresh |
| News | Publisher RSS — $0 | — | Respect each feed's terms; links go to the source |
| Compute | Local machine — $0 | VPS with 2 vCPU / 4 GB: **$12–25/month** | Engine uses <5% of one core; backtests and training are bursty |
| Database | SQLite / self-hosted TimescaleDB — $0 | Managed Postgres $15–50/month | Storage ≈ 20 symbols × 5 TFs × 1000 bars ≈ 100k rows; 1h history for training ≈ 250k rows. Under 1 GB/year including snapshots |
| LLM (optional) | Off by default | Claude Opus 5.5 at $4 / $20 per MTok input/output | Chief narrative ≈ 3k input + 400 output ≈ **$0.02**. Chat turn with tools ≈ 15–30k input + 1k output ≈ **$0.08–0.14** (system prompt cached). 100 chats/day ≈ $8–14/day. Never called per tick |
| Notifications | In-app/browser — $0; Telegram bot — $0 | SMTP provider $0–15/month | |

**Typical total:** $0 locally. About $15–30/month on a VPS without the LLM. Add $50–400/month with the LLM,
depending on chat volume. Switching `AIT_LLM_MODEL` to `claude-sonnet-5-5` halves that cost, but that is a
quality/cost trade-off for the operator to decide.

GPU: not required. LightGBM trains on 250k rows × 40 features in seconds on CPU. Deep models (PatchTST, TFT)
remain a research track (see ML_RESEARCH.md).
