# Competitors — what they offer, what we have, and a head-to-head accuracy test

Updated 2026-11-12. Prices and features come from vendor pages and review/comparison sites, which often disagree:
treat prices as indicative. Accuracy claims are quoted as the vendors publish them; none of them is independently audited.
The head-to-head test (section 4) is ours: `backend/app/sim/benchmarks.py`, page `/simulator` → "vs competitors".

## 1. The field

| Product | What it is | Price (indicative, 2026) | Published accuracy claim → status |
|---|---|---|---|
| Token Metrics | AI trader/investor grades, hourly bull/bear signals, chat agent, indices, API/MCP | ~$99–749 / mo (sources conflict) | "Signal ROI vs holding ROI" in newsletters, no method → **not audited**; Trustpilot UK 2.5/5 (billing, refunds) |
| 3Commas | DCA/grid/signal bots, TradingView webhooks, paper trading, AI assistant | $20 / $50 / $140 mo | None. 2022 API-key leak drained user accounts (~$15M+) |
| Cryptohopper | Cloud bots, strategy designer, backtest, paper, marketplace, copy bots | $29 / $69 / $129 mo | None. Complaints: bots stop executing, plan descriptions |
| TradingView | Charts, Pine strategies, alerts, screeners, broker links | ~$15–70 / mo (+17–20% in Apr 2026) | None |
| Pionex | Exchange with 16 free grid/DCA bots, "AI strategy" from a 7-day backtest | 0.05% fee | None. Trustpilot 2.5/5 (frozen withdrawals) |
| Bitget GetAgent / copy trading | Exchange AI agent with execution; copy trading | free + fees | "93% of futures copiers profited (H1 2023)" — **self-reported, leaderboard shows only profitable leads** |
| Binance copy trading | Exchange copy trading | profit share + fees | None |
| Coinrule / WunderTrading / Altrady / Bitsgap / Kryll | Rule bots, webhook bots, terminals, marketplaces | free – ~$150 / mo (Kryll: % of capital) | None |
| Cornix | Auto-executes Telegram signal groups | ~$25–90 / mo | Marketplace "historical performance"; providers not vetted |
| Learn2Trade (Telegram signals) | VIP signal groups | n/a | "76%" / "82%" win rate depending on source → **no verification** |
| Tickeron | AI robots and pattern signals (mostly stocks) | n/a | "+159% annualized, 90% win rate", "92% pattern accuracy" — annualized from **31–90-day windows**, not audited |
| Intellectia.ai | AI stock/crypto picks, signals, backtest add-on | free – $90 / mo | Reviews cite "60%+ annualized", "20x"; its own pricing page now says "does not have a 75% success rate" |
| altFINS | Screener, "AI chart patterns", setups | free – $60 / mo | "Up to 84%" pattern accuracy — vendor backtest |
| Stoic.ai | Hands-off AI rebalancing | ~$9 / mo or ~5% / yr | User anecdotes only; complaints: opaque, no risk management |
| AIXBT | LLM agent posting calls on X | token-gated | "83%" claimed vs "~31%" measured by another analyst — both unverified |
| Kaito | Narrative / mindshare search (Yaps shut Jan 2026) | ~$99–833 / mo | None (data product) |
| Santiment / CryptoQuant / Glassnode / Nansen / CoinGlass | Data: social, on-chain, exchange flows, smart money, derivatives & liquidation heatmaps | ~$29–999 / mo | None (data, not signals). CoinGlass heatmaps are model estimates |
| TrendSpider | Automated TA, backtests, bots (stocks first) | $82–321 / mo | None |
| Freqtrade / FreqAI | Open-source bot with ML | free | None; docs say FreqAI is not for production |

**Independent evidence on accuracy in this industry:** no audit of any vendor's signals exists. LLMs trading real money
(Nof1 Alpha Arena, ~17 days, Oct–Nov 2025): Qwen3-Max ~+22%, GPT-5 ~−63%, Gemini 2.5 Pro ~−57% (too short to mean
much). Copy trading raises risk-taking and lowers welfare (Apesteguia et al., *Management Science* 2020). BIS: ~73–81%
of retail crypto-app users lost money on bitcoin. Regulators: CFTC advisory (2024) names "AI bots" and signal services
promising high returns as fraud red flags; the SEC fined two advisers for "AI washing" (2024).

**What users complain about:** unverifiable or cherry-picked win rates (deleted losers, backtests, short windows
annualized); billing and price hikes; bots silently stopping; API-key theft and frozen withdrawals; slow support;
opaque trades without risk management; metered AI add-ons.

## 2. Capabilities — what we have and what we don't

| Capability | Competitors that have it | ConfluenceAI |
|---|---|---|
| Multi-agent AI desk (analysts, red team, risk, validator, head) | Bitget GetAgent, Token Metrics agent, Nansen agent (single-agent chats) | ✅ 8 leads + 20 specialists, tool-grounded, fact-checked — **off right now: the OpenAI key expired** |
| Trade plan with entry / stop / target / size / leverage / liquidation | Signal groups, Cornix, Token Metrics | ✅ Today's 5, sized from the budget, risk style chosen by the evidence, validators |
| Evidence-weighted final signal that explains itself | — | ✅ unique: every component's score, weight and reason |
| Quant factor research, CPCV, deflated Sharpe, PBO | — (Freqtrade has hyperopt only) | ✅ quant lab, 591 coins survivorship-free |
| Backtest | 3Commas, Cryptohopper, TradingView, TrendSpider, Coinrule | ✅ event backtester, historical simulation, evolution loop |
| Every-coin simulator with luck test and Monte Carlo | — | ✅ new (`/simulator`) |
| Paper trading / forward test | 3Commas, Cryptohopper, Coinrule | ✅ daily-picks sandbox, quant sandbox (2 books), paper portfolio |
| Public graded track record, never deleted | **nobody** | ✅ `/record` — but only days old (11 picks, none closed yet) |
| Derivatives: funding across venues, OI, options, depth/slippage | CoinGlass, CryptoQuant | ✅ + own order-book recorder (20 perps) |
| Fundamentals (supply, TVL, fees, revenue) | Token Metrics, Messari | ✅ |
| Screener | TradingView, altFINS, CoinGlass | ✅ `/markets` |
| Charting terminal with drawings | TradingView, TrendSpider | ✅ (TradingView is far deeper) |
| Alerts | everyone | ✅ in-app, Telegram, email |
| **Automated execution on the user's exchange** | 3Commas, Cryptohopper, Pionex, Bitget, Cornix, WunderTrading | ✗ we show the orders; the user places them |
| **Grid / DCA bots** | 3Commas, Pionex, Bitsgap, exchanges | ✗ (and the test below shows why we should not sell them as an edge) |
| **Copy trading / social** | Bitget, Binance, Cryptohopper | ✗ |
| **Mobile app** | most | ✗ (web only, no PWA yet) |
| **Public API / MCP for users** | Token Metrics, CryptoQuant, Glassnode, Nansen | ✗ |
| **Social / narrative data** | Kaito, Santiment | ✗ (keyword news sentiment only) |
| **On-chain exchange flows, labelled wallets** | CryptoQuant, Glassnode, Nansen | ✗ (paid data) |
| **Liquidation heatmap** | CoinGlass | ✗ (recording order books now; liquidation history still needed) |
| Stocks / ETFs | Tickeron, TrendSpider, Intellectia | ✗ crypto only |

## 3. Our accuracy — measured, not claimed

| Record | Result |
|---|---|
| Live daily picks (sandbox, since 2026-11-10) | 11 picks, 5 open, 6 waiting, **0 closed — no live accuracy yet** |
| Quant sandbox books (forward, since 2026-11-10) | composite −0.37%, Book C −0.01% after 1 day — too early |
| Historical simulation of the daily-5 method (4h, out of sample) | 33% win rate, −0.07R per trade — **lost; not promoted** |
| Fused signal replayed on every coin, 2021→now | 47.4% of 15,005 trades won, average trade +0.4%, direction right on 50.1% of days |
| Factor composite (ranks coins against each other) | rank IC +0.114, t 12.6, positive every year since 2022 — real, but its own portfolio lost after costs |

## 4. Head-to-head: our signal vs what competitors sell, on every coin

Same 591-coin survivorship-free panel (delisted coins included), 2021-01-01 → 2026-11-11, same rules for everyone:
decided at the close, filled at the next open, fee + slippage by liquidity, funding on perps, 3 × ATR stop (the DCA bot
without a stop, as sold), one $10,000 account per coin, pooled with equal capital across held coins. Default settings,
nothing tuned. "Beat luck": coins where it beat the same trades at random times (p < 0.05) vs the number luck gives;
none of the 14 strategies has a single coin that survives the multiple-testing correction.

| Strategy (sold by) | Sides | Return | 2022 → now | Sharpe | Max DD | Win rate | Day hit rate |
|---|---|---|---|---|---|---|---|
| **ConfluenceAI signal (live weights)** | long + short | +104% | **−13%** | 0.51 | 74% | 47.4% | 50.1% |
| ConfluenceAI signal | long only | −71% | −94% | 0.12 | 97% | 42.9% | 48.8% |
| ConfluenceAI signal (prior weights) | long + short | −60% | −64% | −0.44 | 82% | 37.3% | 48.3% |
| EMA 20/50 cross (TradingView, 3Commas, Cryptohopper, Coinrule) | long + short | **+393%** | **+7%** | **0.79** | **49%** | 36.6% | 49.6% |
| EMA 20/50 cross | long only | −92% | −98% | −0.05 | 99% | 23.6% | 47.7% |
| Supertrend 10/3 (TradingView, signal bots) | long + short | +262% | −24% | 0.69 | 62% | 37.7% | 49.0% |
| MACD cross (TradingView, Cryptohopper) | long + short | +3% | −54% | 0.27 | 74% | 35.5% | 49.0% |
| 20-day breakout (TradingView, TrendSpider) | long + short | +122% | −48% | 0.53 | 73% | 37.3% | 48.7% |
| RSI < 30 buy (Cryptohopper, Coinrule, Pionex) | long only | −100% | −100% | −0.74 | 100% | 52.3% | 49.0% |
| Bollinger lower band (Cryptohopper, Coinrule) | long only | −100% | −100% | −0.58 | 100% | 53.6% | 49.7% |
| DCA bot, 5 safety orders, +3% TP (3Commas, Pionex, Bitget) | long only | −36% | −84% | 0.28 | 88% | **98.9%*** | 48.7% |
| Top-10 momentum index, weekly (AI index products) | long 10 | −100% | −100% | −0.56 | 100% | — | — |
| ConfluenceAI factor composite top-10, weekly | long 10 | −64% | −88% | 0.18 | 94% | — | — |
| Buy and hold every tradeable coin | long | +90% | −83% | 0.57 | 89% | — | — |

\* closed cycles only — losing cycles never close, so they never count. This is how 90%+ "win rates" are produced.

**What it shows**
1. **Win rate is not accuracy.** The highest win rates (DCA 98.9%, Bollinger 53.6%, RSI 52.3%) belong to strategies that
   lost 36–100%. Every "X% win rate" in section 1 should be read with this in mind.
2. **Long-only anything lost** since 2022 on a survivorship-free universe (holding every coin: −83%). Strategies sold as
   long-only spot bots — most of what 3Commas, Cryptohopper, Pionex and Coinrule users run — were on the wrong side.
3. **Our signal is second of 14 since 2022** (−13% vs EMA cross +7%, everyone else −24% to −100%), and the only one with a
   clearly positive 2022 (+33%). But a plain EMA 20/50 long/short beat it over the whole period, with a smaller drawdown.
4. **No strategy — ours or theirs — beats random timing on any coin after correcting for testing 541 coins.** Per coin,
   nobody's timing is proven. Any product claiming high per-signal accuracy is claiming something this test does not find.
5. With literature-only weights our signal loses (−60%); its result depends on the factor-composite weight learned on part
   of this history. That is the honest caveat on our number.

## 5. What to do with this (in order of value)
1. **Use the gap nobody fills: a verified record.** Keep logging every pick before the fact, never delete, after fees;
   publish the simulator and the luck test; later, a third-party check. This is also what the SEC/CFTC expect of AI claims.
2. **Fix the AI key** — the multi-agent desk, our most visible difference, is off.
3. **Add trend as a component and test it**: the EMA-cross long/short is the best single rule here; add it to the fused
   signal as a component with a literature prior, and let the simulator decide the weight out of sample (pre-register
   first — it was chosen after seeing this table).
4. **Execution** (the biggest feature gap): one-click order placement on Binance/Bybit through the user's API key, with
   withdrawal-disabled keys only (3Commas' 2022 leak is the cautionary tale).
5. Mobile: a PWA with push alerts.
6. Public API / MCP of the signal and the record (Token Metrics, CryptoQuant and Glassnode all ship one).
7. Do **not** add DCA/grid bots as an edge — the test shows the advertised win rate hides the loss; if added for
   convenience, show the full record including open cycles.
8. Data gaps, by cost: liquidation history (heatmap), social attention, on-chain exchange flows.

## Sources
- Token Metrics: https://marketplace.quicknode.com/add-on/token-metrics-api · https://zoftwarehub.com/products/token-metrics/pricing · https://uk.trustpilot.com/review/tokenmetrics.com
- 3Commas: https://comparedge.com/tools/3commas · https://www.theblock.co/post/179237/security · https://blockworks.co/news/crypto-trading-bot-platform-3commas-rocked-by-critical-api-leak
- Cryptohopper: https://www.cryptohopper.com/pricing · https://gainium.io/review/cryptohopper
- TradingView: https://chartinglens.com/blog/tradingview-price-increase-2026
- Pionex: https://www.trustpilot.com/review/Pionex.com · Bitget: https://www.bitget.com/blog/articles/bitget-copy-trading-report · https://www.theblock.co/post/361537/bitget-launches-ai-trading-assistant-to-simplify-crypto-strategies
- Cornix: https://intercom.help/cornix/en/articles/5814936-marketplace · Learn2Trade: https://coinspot.io/en/best-crypto-trading-telegram/learn2trade-signals/
- Tickeron: https://tickeron.com/trading-investing-101/tickerons-ai-trading-robots-deliver-159-returns-with-90-win-rates-on-leveraged-etfs/ · https://alphasignal-56o.pages.dev/reviews/tickeron
- Intellectia: https://intellectia.ai/pricing · altFINS: https://madeonsol.com/blog/altfins-review · Stoic: https://ca.trustpilot.com/review/cindicator.com
- Data platforms: https://costbench.com/software/onchain-analytics/santiment/ · https://www.costbench.com/software/onchain-analytics/cryptoquant/ · https://comparedge.com/tools/coinglass/pricing · https://www.costbench.com/software/onchain-analytics/glassnode/ · https://www.costbench.com/software/onchain-analytics/nansen/
- Kaito: https://blockeden.xyz/blog/2026/04/29/kaito-yaps-sunset-attention-markets-pivot-infofi-platform-fragility/
- Evidence: https://iweaver.ai/blog/alpha-arena-ai-trading-season-1-results · https://arxiv.org/pdf/2510.00332v2 · https://econpapers.repec.org/RePEc:inm:ormnsc:v:66:y:12:i:2020:p:5608-5622 · https://www.bis.org/publ/bisbull69.htm
- Regulators: https://www.cftc.gov/LearnandProtect/AdvisoriesAndArticles/AITradingBots.html · https://www.morganlewis.com/pubs/2024/03/sec-charges-investment-advisers-with-making-false-and-misleading-statements-about-their-use-of-ai
