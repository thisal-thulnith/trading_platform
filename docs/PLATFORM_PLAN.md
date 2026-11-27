# Platform plan — market, packages, fair signals and scaling

How ConfluenceAI becomes a product many people pay for without the signals breaking when many people use them.
What is built is marked ✅; the rest is the plan. Not legal advice: check the rules for selling trading signals in
every country you sell in before charging anyone.

## 1. The market and the position

**Who it is for.** Retail crypto traders with $100–$10,000 accounts who trade by hand on Binance, and people learning.
They are served today by Telegram signal groups (no track record, no risk plan), TradingView ideas (opinions, no
sizing) and trading bots (custody risk, black boxes).

**The position: the desk that tells you when not to trade.** Every other product sells more signals. This one sells
fewer, better ones, and proves it:
- A signal only appears when the free readiness check finds a trade-quality setup; most of the time the answer is wait.
- Every signal comes with the full research, the counter-argument, a plan sized from the user's own budget and the risk
  style chosen from the evidence, and the price that proves it wrong.
- The desk's record is graded on the candles and shown as it is, losses included (the dashboard's desk record).
- No custody, no auto-trading: the user places the orders. That keeps the product decision support.

**Getting users.** The free Starter plan is the funnel: the dashboard and readiness check cost almost nothing to serve.
Then: the Learn section as content (search traffic), a public, graded track record page, a Telegram channel that posts
the ready signals with the delay of the free plan, referrals (a month of Pro for each paying referral), and an exchange
referral programme. Never advertise returns; advertise the process and the honest record.

## 2. Packages ✅ (`backend/app/plans.py`, `/plans`)

| | Starter | Pro | Desk |
|---|---|---|---|
| Price | free | $29 / month | $99 / month |
| Signals | top 3 on the dashboard, the rest locked | all | all |
| Delivery | 15 min after Desk | 5 min after Desk | first |
| Timeframes | 4h, 1d | all | all |
| AI desk analyses | 2 a day | 15 a day | 60 a day |
| Apex decision | single | single | 3-vote majority |
| Alerts | in-app | email, Telegram | + webhook, API |

- An AI analysis is counted only when the agents actually run; a session stopped by the readiness gate is free.
- The trader sets a budget once; Sentinel (the Risk Officer) chooses the risk style (1–3% of the budget at risk) and the
  size per trade from the evidence ✅.
- Billing is not connected: `POST /api/my/plan` switches plans for free. Before charging, connect a payment provider and
  set the plan only from its webhook. Self-hosted installs run as Desk; set `AIT_ANONYMOUS_PLAN=starter` in public.

**Setting the prices.** The cost that matters is the AI desk: about 30 model calls per analysis. Measure the real cost
per analysis C (log the tokens per session — next step), then keep a 70% gross margin at realistic use:

    price ≥ (analyses a user really runs per month × C) / 0.30

If a Pro user runs 6 analyses a day on average, that is about 180 a month. Cheaper specialists (`AIT_DESK_SUB_MODEL`)
and shared analyses (section 4) are the two levers that bring C down.

## 3. Fair signals when many people get the same one

**The problem.** If 5,000 users get "short HUMA now" at the same second, the first ones move the price, the last ones
fill at a worse price than the plan, their stop is now too close, and the signal stops being true. Everyone also places
the stop at the same price, which is exactly where a stop hunt goes.

**What is built ✅**
- **Capacity per signal** (`market_extras.signal_capacity`, the dashboard's Capacity column): the dollar size resting in
  the order book within 10 bps of the price on the side the entry takes, times 25% — the most our users together should
  take. Live examples on 2026-11-11: BTC about $4.1M, SOL about $665K, HUMA about $1.1K. A HUMA signal can serve about one
  user; BTC can serve thousands.
- **Delivery waves by plan** (dashboard): Desk sees a new signal first, Pro 5 minutes later, Starter 15 minutes later.
  The same signal never reaches everyone at once.
- **Fewer signals by design:** the readiness gate, the validators (clear path to target, stop not on resting liquidity,
  book can take the size, funding over the hold) and the crowding check on funding across three exchanges.

**What to build next**
1. **A delivery ledger per signal.** When a coin turns ready: compute its capacity, then deliver wave by wave. Each
   recipient adds their expected position (from their budget and Sentinel's sizing) to the signal's total; once the total
   reaches the capacity, later users see "capacity reached — wait for the next setup" instead of chasing the price.
2. **Entry zones and limit orders, never a market order.** Give a zone, not a price, and spread each user's limit price
   inside it (a small random offset), so the orders do not stack at one tick.
3. **De-clustered stops.** Keep each user's stop beyond the same invalidation but vary the buffer (for example ±0.1 ATR),
   so there is no single wall of stops.
4. **Expiry.** A signal expires when the price leaves the entry zone or after a set number of candles; anyone opening it
   later sees "expired", not a stale entry.
5. **Rotation.** When many coins are ready at once, give each user the ones that fit their budget, watchlist and risk,
   so the crowd spreads over several markets instead of piling into one.
6. **Measure the impact.** Let users report their fills (or read them through a read-only exchange key) and compare with
   the signal price. If slippage grows, lower the capacity share.

## 4. Scaling — compute per market, not per user

The rule that makes this affordable: everything that depends only on the market (data, analysis, readiness, the AI
desk's research) is computed once per coin and timeframe and shared; only sizing (code, free) is per user.

**Stage 0 — now (one user).** One process, SQLite, in-memory caches. Fine for building and paper trading.

**Stage 1 — up to about 1,000 users.**
- Postgres / TimescaleDB (the compose file exists) for candles, signals, journal, usage and users.
- Redis for caches, rate limits, the delivery ledger and WebSocket fan-out (pub/sub).
- Split the process: an API (stateless), one market-data ingester, one engine worker, and an AI worker pool fed by a job
  queue with a concurrency limit and per-user quotas.
- Payments with webhooks setting the plan; error tracking and metrics; daily backups.

**Stage 2 — up to about 50,000 users.**
- Several API instances behind a load balancer; the ingester and engine as single leaders (leader election), never one
  per user.
- **Shared AI analyses:** an analysis is keyed by coin + timeframe + the candle it was made on, and served to every user
  who asks for that coin in that candle. Per-user is only the plan sized from their budget. AI cost then grows with the
  number of ready coins, not the number of users.
- Run the desk automatically at candle close for coins that turn ready, so the first wave is already analysed.
- A notification service with a queue and per-channel limits (Telegram and email have them).

**Stage 3 — 100,000 users and more.**
- A dedicated market-data cluster and WebSocket gateway; read replicas in more than one region.
- Model routing: small models for specialists, the strongest for the Head Trader; batch calls where the provider allows.
- A data warehouse for research and for auditing every signal's outcome.
- Security and compliance work that grows with revenue (audits, regional rules on financial promotions).

## 5. Next steps, in order
1. Log AI tokens and cost per desk session; set the real prices.
2. Connect billing (webhook → plan) and switch public installs to `AIT_ANONYMOUS_PLAN=starter`.
3. Delivery ledger with capacity, entry zones with spread limit prices, expiry (section 3, items 1–4).
4. Shared analyses per coin, timeframe and candle (section 4, stage 2).
5. Postgres + Redis + job queue (stage 1) before the first hundred paying users.
6. A public, graded track record page; then the Telegram channel and referrals.
