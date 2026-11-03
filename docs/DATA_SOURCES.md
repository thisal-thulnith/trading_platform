# Data Sources

Last verified: 2026-11-01. "Verified" means we hit the endpoint live (from a non-US IP) or read the official docs on that date. "Unverified" means it comes from secondary sources or memory, so confirm before relying on it.

---

## 1. Market data providers: comparison

| Provider | Base URLs | REST limits | WS limits | History depth (verified) | Derivatives data | Key needed | License / terms |
|---|---|---|---|---|---|---|---|
| **Binance Spot** | `https://api.binance.com` (also `api1..api4`, `api-gcp`); **market-data-only:** `https://data-api.binance.vision` | **6000 request weight/min per IP** (verified via `exchangeInfo.rateLimits`); also RAW_REQUESTS 300,000 per 5 min. 429 on breach, 418 = IP ban (escalating 2 min to 3 days) | `wss://stream.binance.com:9443` or `:443`; market-only `wss://data-stream.binance.vision`. **24 h max connection life; server ping every 20 s, pong within 1 min; 5 incoming msgs/s; 1024 streams/conn; 300 connection attempts per 5 min per IP** (verified, official docs) | Klines from **2017-08-17** (BTCUSDT 1d, verified) | n/a | No (public) | Binance Terms of Use; geo-restricted (HTTP 451 for US IPs) |
| **Binance USDⓈ-M Futures** | `https://fapi.binance.com` | **2400 weight/min per IP** (verified); `/futures/data/*` endpoints: weight 0 but **1000 req/5 min per IP** | `wss://fstream.binance.com` with **routed paths `/public`, `/market`, `/private`** (see §1.1). 24 h life; ping every 3 min, pong within 10 min; **10 incoming msgs/s**; 1024 streams/conn (verified, official docs) | Klines from **2019-09-08**; funding history from **2019-09-10** (BTCUSDT, verified) | Funding history, premiumIndex/mark/index, OI (current), **OI history, long/short ratios, taker buy/sell volume, basis: latest 30 days only** (verified), liquidation stream `!forceOrder@arr` | No for market data; yes for leverage brackets | Same as above |
| **Binance bulk dumps** | `https://data.binance.vision` | Static files (S3) | n/a | Spot, USDⓈ-M, COIN-M: daily/monthly zips of klines, aggTrades, trades, bookTicker (some), fundingRate, metrics (OI/LS ratio daily files for futures; coverage unverified per symbol). **Includes delisted symbols.** CHECKSUM files provided | Yes (fundingRate, metrics, premiumIndexKlines, markPriceKlines) | No | **Binance Vision Terms of Use** |
| **Bybit v5** | `https://api.bybit.com` (also `api.bytick.com`) | **600 req / 5 s per IP** default; 403 "access too frequent" causes ≥ 10 min IP ban (verified, official docs) | `wss://stream.bybit.com/v5/public/{spot,linear,inverse,option}`; 500 new connections per 5 min; ≤ 1000 market-data connections per IP per market type (verified) | **OI history back to 2020-09** (`/v5/market/open-interest`, verified); funding history back to 2020-05 (verified); klines (spot/linear) multi-year | Funding, OI (5min..1d), long/short ratio, mark/index/premium klines, liquidations WS | No | Bybit ToS; also geo-restricted (US, etc.; unverified list) |
| **OKX v5** | `https://www.okx.com` | Per endpoint, typically 20 to 40 req/2 s per IP for market data (unverified per endpoint; check docs) | `wss://ws.okx.com:8443/ws/v5/public`; 3 connection requests/s per IP; 480 subscribe ops/hour/connection; disconnect after 30 s without data, so send `ping` (verified) | `history-candles` back to **2018-03** for BTC-USDT 1D (verified) | Funding history (**recent only**; docs say ~3 months, unverified), OI and volume (rubik stats), long/short, liquidations | No | OKX ToS; geo-restricted (US uses separate OKX US) |
| **CoinGecko** | `https://api.coingecko.com/api/v3` (Demo key via `x_cg_demo_api_key`); Pro: `pro-api.coingecko.com` | **Demo: 100 calls/min, 10,000 calls/month** (official pricing page and docs, Nov 2026; older docs said 30/min). Keyless public: IP-based shared limit, low and unstable | Paid plans only | Daily OHLC/market-chart history; Demo historical range limited (on paid Basic: 2 years, per pricing page) | Basic derivatives tickers (aggregated) | Demo key (free) recommended | **Attribution required** ("Data provided by CoinGecko" + link). Demo is for testing/exploration; commercial use listed for paid plans |
| **CoinCap** | v2 `api.coincap.io`: **DNS NXDOMAIN, dead** (verified). v3 `https://rest.coincap.io/v3`: **401 without key** (verified) | Credit-based, account required (free allowance unverified) | v3 WS (key) | n/a | No | Yes | CoinCap ToS. **Do not build on v2.** |
| **CCXT** | Library (Python/JS) | Has built-in `enableRateLimit` throttling | `ccxt.pro` (WS) now merged into free CCXT | Depends on exchange | Unified `fetchFundingRateHistory`, `fetchOpenInterestHistory` where supported | n/a | **MIT** license; exchange terms still apply |

### 1.1 Breaking change: Binance USDⓈ-M futures WebSocket routing
Binance split futures WS into `wss://fstream.binance.com/public/...` (high-frequency: `depth`, `bookTicker`), `/market/...` (regular: `aggTrade`, `kline`, `markPrice`, `ticker`, `forceOrder`, etc.), and `/private/...` (user data via listenKey). **Legacy un-routed URLs were decommissioned after 2026-04-23. An un-routed connection now receives only `/public` streams, so `@markPrice` and `@kline` silently push nothing.** Use the official "Important WebSocket Change Notice" for the exact stream-to-route mapping. Do not rely on older tutorials or library versions (e.g., ccxt/go-binance updated April 2026).

### 1.2 Other Binance notes
- **Spot klines:** `GET /api/v3/klines`, max `limit=1000`. Futures `GET /fapi/v1/klines` max 1500, with weight scaling by limit (unverified exact weights; read the `X-MBX-USED-WEIGHT-1M` response header and throttle on it).
- **Closed-bar detection:** in WS kline payloads `k.x == true` means the bar is closed. REST: drop the last row if `closeTime > now`.
- Timestamps are ms epoch UTC. Spot also supports `timeUnit=MICROSECOND`.
- **Funding interval** varies per symbol (`GET /fapi/v1/fundingInfo`).
- **Leverage brackets** (`/fapi/v1/leverageBracket`) require an API key (signed). Cache them; they change rarely.
- **Geo-blocking:** US IPs (including many US cloud regions) receive **HTTP 451**. Deploy ingestion outside restricted regions. **Do not use proxies to circumvent restrictions.** For US users, `api.binance.us` is a different venue with a different symbol set and liquidity.

---

## 2. What to use for what

| Need | Primary | Backup / backfill |
|---|---|---|
| Historical OHLCV (spot, perp) | data.binance.vision monthly + daily zips | Binance REST klines; Bybit/OKX via CCXT |
| Live bars | Binance WS kline (spot: stream.binance.com / data-stream.binance.vision; perp: fstream `/market`) | REST polling of last 2 bars every bar close; Bybit WS |
| Funding history | Binance `/fapi/v1/fundingRate` (from 2019) + data.binance.vision fundingRate | Bybit `/v5/market/funding/history` |
| Predicted funding, mark, index | Binance `/fapi/v1/premiumIndex`, WS `@markPrice@1s` (`/market` route) | Bybit tickers |
| OI history | **Self-collected snapshots** of `/fapi/v1/openInterest` (every 1 to 5 min) | Bybit `/v5/market/open-interest` (back to 2020) for backfill; Binance `/futures/data/openInterestHist` (30 days only) |
| Long/short ratio, taker volume, basis | Binance `/futures/data/*` (30 days) plus our own snapshots | Bybit account-ratio |
| Liquidations | Binance WS `!forceOrder@arr` (snapshot-throttled: at most 1 per symbol per 1000 ms, so it **undercounts**) | Bybit `allLiquidation` WS |
| Market cap, coin metadata, dominance | CoinGecko (Demo key, cache heavily, attribute) | CoinPaprika (free tier; unverified limits) |
| Order book | Binance WS `@depth@100ms` + REST snapshot sync (documented procedure) | Bybit orderbook WS |

### 2.1 Failover plan
1. **Health checks** per source: last-message age, HTTP status, and rate-limit headers (`X-MBX-USED-WEIGHT-1M`). Alert when age exceeds 2× the bar interval.
2. **WS reconnect:** exponential backoff with jitter. Proactively reconnect before the 24 h cutoff (e.g., at 23 h) with overlap to avoid gaps. Respect the connection-attempt limits.
3. **Gap repair:** on reconnect, backfill missing bars via REST (`startTime` = last stored close + 1 ms). Run a nightly reconcile against data.binance.vision daily files.
4. **Secondary venue:** if Binance is unreachable (451/5xx/ban) for over 5 min, switch display to Bybit (then OKX) for price, **flag the source change in the UI**, and never mix venues within one series without a `venue` column. Prices differ by basis and spreads.
5. **Rate budgeting:** a central token-bucket per (host, IP). Keep ≤ 50% of the weight budget for bursts. On 429, back off per `Retry-After`. On 418, stop all calls to that host.
6. **Storage schema:** `(venue, symbol, timeframe, open_time, ..., ingest_time, source)`. OI/funding snapshots keyed on the exchange timestamp, plus `ingest_time` for as-of joins.

---

## 3. News, macro and sentiment sources

All RSS feeds below were fetched on 2026-11-01 and returned valid RSS XML (HTTP 200) unless noted.

| Source | URL | Status |
|---|---|---|
| CoinDesk | `https://www.coindesk.com/arc/outboundfeeds/rss` (the trailing-slash form 308-redirects to this) | 200, RSS 2.0, verified |
| Cointelegraph | `https://cointelegraph.com/rss` | 200, RSS 2.0, verified |
| Decrypt | `https://decrypt.co/feed` | 200, RSS 2.0, verified |
| The Block | `https://www.theblock.co/rss.xml` | 200, RSS 2.0, verified |
| Bitcoin Magazine | `https://bitcoinmagazine.com/feed` | 200, RSS 2.0, verified. (`/.rss/full/` returns 403; avoid) |
| SEC press releases | `https://www.sec.gov/news/pressreleases.rss` | 200, verified. **SEC requires a descriptive User-Agent with contact email** and fair-access ≤ 10 req/s |
| Federal Reserve (all press releases) | `https://www.federalreserve.gov/feeds/press_all.xml` | 200, verified. FOMC-specific: `https://www.federalreserve.gov/feeds/press_monetary.xml` (unverified) |
| Binance announcements | No official RSS. The web app uses `https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&pageNo=1&pageSize=N` (returned JSON, verified; catalog 48 = "New Cryptocurrency Listing") | **Undocumented, may change or be blocked**; poll gently (≥ 60 s). The HTML page returned 202 (bot challenge) |
| Fear & Greed Index | `https://api.alternative.me/fng/?limit=N` (`limit=0` for full history; `&format=csv`) | 200 JSON, verified. Daily update. Attribution to alternative.me requested (per their API page; unverified wording) |

**Ingestion rules:** poll each feed every 2 to 5 min with `If-None-Match`/`If-Modified-Since`. Dedupe on canonical URL plus a title hash. Store `pub_date` (publisher-claimed) **and** `first_seen_at` (ours); backtests must use `first_seen_at`. Store only title, summary, link, and author. Link out for full text (copyright).

### 3.1 Social platforms
- **X (Twitter):** API v2 is paid. Tiers and pricing changed several times in 2023 to 2026 (verify current terms). Scraping violates the ToS. **Out of scope for the MVP.**
- **Reddit:** Data API terms (2023) require OAuth, impose a free-tier rate limit (about 100 queries/min per client; unverified current figure), and require a separate agreement for commercial use. Pushshift access is restricted. **Out of scope for the MVP**, or personal/non-commercial use only.
- **Telegram/Discord:** bot APIs work only for channels you are a member of; respect each community's rules. Out of scope.

### 3.2 On-chain (optional, mostly paid)
Glassnode, CryptoQuant, and Coin Metrics are paid for most useful metrics (Coin Metrics Community API has a free subset; check its license). Blockchain.com charts API offers basic BTC metrics free (unverified current status). Low priority given grade-C evidence.

---

## References
- Binance Spot API docs (limits, endpoints): https://developers.binance.com/docs/binance-spot-api-docs
- Binance Spot WebSocket streams (official GitHub mirror): https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md
- Binance USDⓈ-M WebSocket connect (routed endpoints): https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Connect
- Binance Important WebSocket Change Notice: https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Important-WebSocket-Change-Notice
- Binance Open Interest Statistics (1-month limit): https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Open-Interest-Statistics
- Binance public data (bulk): https://data.binance.vision and https://github.com/binance/binance-public-data
- Binance Vision Terms of Use: https://data.binance.vision/Binance_Vision-Terms_of_Use.pdf
- ccxt HTTP 451 issue: https://github.com/ccxt/ccxt/issues/15872
- Bybit v5 rate limits: https://bybit-exchange.github.io/docs/v5/rate-limit
- Bybit v5 market endpoints: https://bybit-exchange.github.io/docs/v5/market/open-interest
- OKX v5 API docs: https://www.okx.com/docs-v5/en/
- CoinGecko pricing: https://www.coingecko.com/en/api/pricing
- CoinGecko rate limits: https://docs.coingecko.com/docs/common-errors-rate-limit
- CoinGecko attribution guide: https://brand.coingecko.com/resources/attribution-guide
- CoinCap API (v3): https://pro.coincap.io/api-docs
- CoinPaprika note on CoinCap v2 shutdown (third-party): https://coinpaprika.com/education/migrating-from-coincap-api-to-coinpaprika/
- CCXT (MIT): https://github.com/ccxt/ccxt
- SEC developer fair access: https://www.sec.gov/os/accessing-edgar-data
- Federal Reserve RSS feeds: https://www.federalreserve.gov/feeds/feeds.htm
- Alternative.me Fear & Greed API: https://alternative.me/crypto/fear-and-greed-index/
- Reddit Data API terms: https://www.redditinc.com/policies/data-api-terms
- X API: https://developer.x.com/en/products/x-api
