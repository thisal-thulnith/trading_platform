export type Lesson = { slug: string; title: string; level: "start" | "beginner" | "intermediate" | "advanced"; diagram?: string; body: string; tryIt?: string };
export type Chapter = { id: string; title: string; lessons: Lesson[] };

export const CHAPTERS: Chapter[] = [
  {
    id: "platform",
    title: "Part 1 — Using this platform",
    lessons: [
      {
        slug: "getting-started", title: "Getting started in 5 minutes", level: "start",
        body: `**What this platform is:** one main signal a day from a trained model, tested on years it never saw, plus tools to check it (all coins, the chart, the AI agents, news) and a portfolio that tracks the trades you take.

**What it is not:** it never places orders and never promises profits. You place every order on Binance yourself.

1. **Home** shows today's signal, your account, results and the market. The six steps at the bottom are the daily routine.
2. **Today's signal** is the full plan: entry, stop loss, take profit, safe leverage, the three opinions (machine, simulation, AI agents) and whether it is still valid right now.
3. **All coins** ranks every coin the machine scores, live, with a confidence level. Click a card to see its plan.
4. **Portfolio**: enter your futures balance once. The Risk manager sizes each trade for it.
5. Placed a trade on Binance? Press **I took this trade** on any signal. The Risk manager checks it first, then Portfolio tracks it live until it closes.
6. **Results** and **History** show how every signal did. **Guide** (top right) explains every page and number; there is also a PDF.

Quick search: press **Ctrl/⌘ K** (or "/") anywhere and type a coin or a page.`,
      },
      {
        slug: "terminal-tour", title: "Tour of the Chart page", level: "start",
        body: `- **Left: coin list.** Live prices and 24h change. Search covers every USDT coin; the ★ adds a coin to your watchlist.
- **Centre: chart.** Candles, volume and indicators, with drawing tools (trendline, ray, lines, rectangle, Fibonacci, position box, text), **Indicators**, **Overlays**, save layout and screenshot. The **main signal** for the coin is drawn on the chart: entry, stop loss and take profit.
- **Right: Signal** (the main signal, the same card as everywhere else; first tab), **Chart reading (Lab)** and **Detail (Lab)** (the older chart scanner's code reading of this one chart, not the main signal), and **Analyst chat**.
- **Bottom:** the chart scanner's setups (Lab; off the chart unless you tick "show chart-scanner arrows"), alerts, order flow, positions and replay.

**Editing drawings:** click a drawn object to see why it is there and drag it to adjust; your version is saved separately. **Del** hides or deletes, **Esc** cancels a tool.`,
      },
      {
        slug: "reading-signals", title: "How to read a chart-scanner setup (Lab)", level: "start",
        body: `Every setup goes through a **lifecycle**:

| Stage | Meaning | What you do |
|---|---|---|
| **Forming** | Part of a pattern is present | Nothing — just awareness |
| **Pre-signal** | The pattern is complete; waiting for the trigger | Prepare, but don't act |
| **Confirmed** | Trigger happened **and** every safety check passed | Paper-trade it, or study it |
| **Active** | Price reached the entry zone | Manage the position |
| **Target hit / Stop hit / Expired / Invalidated** | Final outcome, recorded forever | Learn from it |

A confirmed signal shows the **entry zone, stop-loss, three targets, reward/risk after fees**, the conditions that were met, the validation gates and a **counter-argument review** (reasons it might fail). If a check fails (for example reward/risk too small, or the higher timeframe disagrees), the platform shows **NO TRADE** with the reasons. That is a feature: avoiding bad trades matters more than finding many trades.`,
      },
      {
        slug: "walkthrough-guide", title: "The chart scanner's step-by-step reading (Lab)", level: "start", tryIt: "walk",
        body: `The walkthrough copies how an experienced trader reads a chart, in this order:

1. **Big picture** on higher timeframes (e.g. 1d and 4h before a 1h chart)
2. **What kind of market** — trending, ranging or chaotic
3. **Market structure** — higher highs/lows or lower highs/lows
4. **Key support & resistance**
5. **Liquidity** — where stop orders probably sit
6. **Supply & demand zones** (order blocks)
7. **Imbalances** (fair value gaps)
8. **Cheap or expensive** within the range (premium/discount)
9. **Elliott Wave** (low weight — subjective)
10. **Momentum & volume** — indicators are used only to *confirm*
11. **Futures positioning** — funding, open interest
12. **News & sentiment**
13. **Scenarios** — what would make it bullish or bearish (trigger, target, invalidation)
14. **Trade plan & risk**
15. **Verdict**

Each step has a **verdict** (bullish / bearish / neutral). The summary weights them: the higher timeframe and structure count most, indicators least. It then reports a **confidence** of low, medium or high. When the steps disagree, confidence is low and the honest answer is usually "wait".`,
      },
      {
        slug: "paper-trading", title: "Paper trading & position sizing", level: "start",
        body: `Paper trading means practising with fake money and real prices. On the **Portfolio** page you enter your balance once; the **Risk manager** chooses the risk per trade (the % of the account you accept losing if the stop is hit: 1% to start) from the evidence, and limits the open risk and the number of positions.

When you open a position, the platform **calculates the size for you**:

> size = (account × risk %) ÷ (distance from entry to stop + fees + slippage)

Example: $1,000 account, 1% risk = $10. Entry $100, stop $98 → risk per coin ≈ $2.10 including costs → buy about **4.76 coins**. If the stop is hit you lose about $10, not your account. Leverage only changes how much margin is used, **not** how much you lose at the stop.`,
      },
      {
        slug: "research-lab-guide", title: "Backtests: how to read test results", level: "beginner",
        body: `A **backtest** replays history candle by candle and asks: "if this strategy had run, what would have happened?" This platform's backtester rebuilds the *exact* live analysis at every past candle, using only data available at that moment, and charges fees and slippage.

Read results with these numbers:
- **Expectancy (R)** — average result per trade in units of risk. +0.2R means that on average you make 20% of what you risk per trade. **Below 0 means the strategy loses money.**
- **95% CI** — the plausible range for that average. If the whole range is below 0, the strategy is reliably losing; if it straddles 0, the result is inconclusive.
- **n** — number of trades. Under 30 is too few to judge.
- **Out-of-sample** — the last 30% of the period. If it is much worse than in-sample, the strategy is probably overfit.

On this platform: **Results** grades every main signal on live prices, **History** shows any past day, and the **Simulator** (Lab) replays a strategy on any coin.`,
      },
    ],
  },
  {
    id: "basics",
    title: "Part 2 — Trading basics (A → Z for beginners)",
    lessons: [
      {
        slug: "what-is-trading", title: "What trading actually is", level: "beginner",
        body: `Trading is buying and selling an asset to profit from price changes. In crypto:
- **Going long** = buy first, sell later at a higher price.
- **Going short** = sell first (borrowed or via futures), buy back later at a lower price.

The hard truth: **most short-term traders lose money**, mainly because of fees, poor risk control and emotional decisions. Professionals focus on **risk first** — how much they can lose — and accept that many trades will be losers. A strategy can win only 35% of the time and still be profitable if winners are bigger than losers; it can win 70% of the time and still lose if one loss wipes out many wins.`,
      },
      {
        slug: "candlesticks", title: "Candlesticks", level: "beginner", diagram: "candle",
        body: `Each candle summarises price over one period (e.g. 1 hour):
- **Open** — first price of the period
- **High / Low** — the extremes (the thin lines called **wicks**)
- **Close** — last price
- **Body** — between open and close. **Green** = close above open (buyers won). **Red** = close below open (sellers won).

Long upper wicks mean price was pushed up but rejected. Long lower wicks mean selling was rejected. A big body with small wicks shows a strong, one-sided move (called **displacement** in SMC). **Only closed candles count**: the current candle can still change, which is why this platform analyses closed candles only.`,
      },
      {
        slug: "timeframes", title: "Timeframes & top-down analysis", level: "beginner",
        body: `The same market looks different on 5-minute, 1-hour and daily charts. Lower timeframes have more noise; higher timeframes show the dominant trend.

**Top-down analysis:** start on a higher timeframe (1d/4h) to find the direction and the big levels, then use the lower timeframe (1h/15m) to time an entry. A useful rule of thumb is to trade *with* the higher timeframe unless there is strong evidence of a reversal. Research supports that trend persistence (time-series momentum) is one of the most robust effects across markets, including crypto.

Shorter timeframes mean more trades, more fees and more noise. Beginners usually do better on 1h–4h.`,
      },
      {
        slug: "spot-vs-futures", title: "Spot vs futures, leverage & liquidation", level: "beginner",
        body: `- **Spot:** you actually buy the coin. Your maximum loss is what you paid.
- **Perpetual futures ("perps"):** contracts that track the price, let you go short, and allow **leverage**.

**Leverage** lets you control a big position with a small deposit (margin). At 10× leverage, a 10% move against you wipes out your margin, and you are **liquidated** (the exchange closes your position). Leverage does **not** make you more right; it makes mistakes more expensive.

Safe practice: decide your stop-loss first, size the position by risk (see Risk management), and keep the liquidation price far beyond your stop. The platform warns you if the estimated liquidation price sits before your stop.`,
      },
      {
        slug: "orders", title: "Order types, fees & slippage", level: "beginner",
        body: `- **Market order** — fills immediately at the best available price. You pay the **taker** fee and may suffer **slippage** (a worse price than expected when liquidity is thin).
- **Limit order** — fills only at your price or better (lower **maker** fee) but might not fill at all.
- **Stop-loss** — becomes a market order when price hits your level, to cap the loss.
- **Take-profit** — closes the position at your target.

Costs matter: a 0.05% fee on entry and exit plus slippage can turn a small edge into a loss. That is why every reward/risk number on this platform is **after costs**.`,
      },
      {
        slug: "support-resistance", title: "Support & resistance", level: "beginner",
        body: `**Support** is a price area where buying stopped a fall before; **resistance** is where selling stopped a rise. They form because traders remember those prices and place orders there.

Practical rules:
- Treat them as **zones**, not exact lines.
- The more often a level is tested, the more attention it gets, but each test also absorbs orders, so it can eventually break.
- A broken resistance often becomes support (and vice versa). The platform marks this as a **retest**.
- A **failed breakout** (price breaks a level then closes back inside within a few candles) is often a strong signal in the other direction.`,
      },
      {
        slug: "regimes", title: "Market regimes", level: "beginner",
        body: `Markets switch between moods:
- **Trending** (strong or weak, up or down) — trend-following works; fading moves is dangerous.
- **Ranging** — price bounces between support and resistance; breakouts often fail.
- **High volatility** — big random swings; stops get hit by noise. Reduce size or stand aside.
- **Breakout** — price just left a range; it either follows through or fails.

The platform measures regime with trend strength (ADX), the slope of the 50-EMA, the Choppiness Index and the volatility percentile. Every strategy declares which regimes it is suited for, and none is allowed to fire in the "wrong" regime.`,
      },
      {
        slug: "indicators", title: "Indicators — what they really do", level: "beginner",
        body: `Indicators are formulas applied to past prices and volume. Main families:
- **Trend:** moving averages (SMA/EMA), MACD, ADX, Supertrend, Ichimoku
- **Momentum (oscillators):** RSI, Stochastic, CCI, Williams %R — these mostly measure the *same thing*
- **Volatility:** ATR, Bollinger Bands, Keltner Channels
- **Volume/flow:** OBV, MFI, CMF, VWAP, volume profile, taker buy/sell imbalance

Key lessons:
1. Indicators **lag**: they describe what price already did.
2. **Redundancy:** RSI, Stochastic RSI and Williams %R agreeing is *one* opinion, not three. The platform groups indicators into families and caps each family's influence.
3. "Overbought" doesn't mean "will fall": in strong trends RSI can stay above 70 for a long time.
4. Use them to **confirm** price action, not to replace it.`,
      },
      {
        slug: "volume", title: "Volume, VWAP & volume profile", level: "intermediate",
        body: `- **Volume** shows participation. Breakouts on high relative volume (RVOL > 1.5) are more trustworthy than quiet ones.
- **Taker buy/sell volume** shows who was aggressive: buyers lifting the offer or sellers hitting the bid. Cumulative volume delta (CVD) adds this up over time.
- **VWAP** (volume-weighted average price) is the average price paid today. Institutions often benchmark against it; price far above or below VWAP in a range tends to revert.
- **Volume profile** shows volume traded *at each price*. The **POC** (point of control) is the busiest price; the **value area** holds 70% of volume. Low-volume areas are often crossed quickly.`,
      },
    ],
  },
  {
    id: "smc",
    title: "Part 3 — Price action & Smart Money Concepts",
    lessons: [
      {
        slug: "market-structure", title: "Market structure: HH, HL, BOS, CHoCH", level: "beginner", diagram: "structure", tryIt: "walk",
        body: `Price moves in swings. A **swing high** is a peak with lower highs on both sides; a **swing low** is the opposite.
- **Uptrend** = **H**igher **H**ighs and **H**igher **L**ows (HH, HL)
- **Downtrend** = **L**ower **H**ighs and **L**ower **L**ows (LH, LL)
- **BOS (Break of Structure)** — price closes beyond the last swing *in the trend's direction*: the trend continues.
- **CHoCH (Change of Character)** — price closes beyond the last swing *against* the trend: the first warning of a reversal.
- **MSS (Market Structure Shift)** — a CHoCH made with a strong, fast candle (displacement).

Important detail: a swing point is only known **after** a few candles have formed on its right side. The platform waits for that confirmation, so its structure labels never "repaint" (change after the fact).`,
      },
      {
        slug: "liquidity", title: "Liquidity, stop hunts & sweeps", level: "intermediate", diagram: "sweep",
        body: `Traders often place stop-losses just beyond obvious highs and lows, and breakout traders place buy-stops above highs. These clusters are called **liquidity**:
- **Buy-side liquidity (BSL)** — above swing highs and **equal highs**
- **Sell-side liquidity (SSL)** — below swing lows and **equal lows**
- Other common pools: previous day/week high and low (PDH/PDL, PWH/PWL) and session highs and lows (Asia, London, New York)

A **sweep** is when price spikes through a pool (triggering those stops) and then closes back inside. It looks like a breakout but traps traders. A sweep followed by a strong reversal candle is called a **stop-hunt candidate**.

Honesty note: stop orders are *inferred* from chart geometry; nobody outside the exchange can see them.`,
      },
      {
        slug: "order-blocks", title: "Order blocks & breakers", level: "intermediate", diagram: "ob",
        body: `An **order block (OB)** is the last opposite-coloured candle before a strong move that broke structure. A bullish OB is the last red candle before a rally that made a BOS. The theory: large orders were filled there, so price may react when it comes back.
- **Mitigated** — price has returned to the zone once.
- **Invalidated** — price closed through the far side. The zone then **flips** into a **breaker block** with the opposite role.

Evidence check: in this platform's own backtests, *order-block retest entries on their own lost money* (−0.47R on 1h). Use OBs as **context** (where a reaction might happen), combined with structure and liquidity, not as a standalone buy/sell rule.`,
      },
      {
        slug: "fair-value-gaps", title: "Fair value gaps (FVG)", level: "intermediate", diagram: "fvg",
        body: `A **fair value gap** appears when, over three candles, the low of candle 3 is above the high of candle 1 (bullish FVG), or the high of candle 3 is below the low of candle 1 (bearish FVG). The middle candle moved so fast that some prices barely traded.

- Price often comes back to **fill** part or all of the gap.
- If price closes through the gap, it becomes an **inverse FVG** with the opposite role.
- A **liquidity void** is a single, very large candle (≥ 2.5 ATR) with almost no wicks.

Evidence check: "buy the retrace into an FVG" lost money in this platform's backtests (−0.44R on 1h, −0.86R on 4h). Gaps describe *where* price might go, not *whether* to trade.`,
      },
      {
        slug: "premium-discount", title: "Premium, discount & OTE", level: "intermediate", diagram: "pd",
        body: `Draw the latest important swing from low to high. The midpoint is **equilibrium (50%)**.
- Above 50% = **premium** (relatively expensive): a better area to sell.
- Below 50% = **discount** (relatively cheap): a better area to buy.
- The **OTE (optimal trade entry)** zone is the 62%–79% retracement of the swing.

This is simply a structured way of saying "buy low, sell high within the current range". It works best when combined with the higher-timeframe direction.`,
      },
      {
        slug: "elliott-wave", title: "Elliott Wave (with healthy scepticism)", level: "advanced", diagram: "elliott",
        body: `Elliott Wave theory says trends unfold in **5 waves** (1-2-3-4-5: three moves with the trend and two pullbacks) followed by a **3-wave correction** (A-B-C). Hard rules:
1. Wave 2 never retraces more than 100% of wave 1.
2. Wave 3 is never the shortest of waves 1, 3 and 5.
3. Wave 4 doesn't overlap wave 1 (except in diagonal patterns).

Fibonacci guidelines are common (wave 3 ≈ 1.618 × wave 1; wave 2 retraces 50–61.8%).

The platform enumerates **only rule-valid counts**, shows a primary count and alternates, and shows the **invalidation price** for each. Counting is subjective and there is little scientific evidence of predictive power, so it carries the **lowest weight** in the analysis.`,
      },
      {
        slug: "sessions", title: "Trading sessions & killzones", level: "intermediate",
        body: `Crypto trades 24/7, but activity follows traditional markets:
- **Asia** ≈ 00:00–08:00 UTC — often quieter, forms ranges
- **London** ≈ 07:00–16:00 UTC — volatility picks up and often sweeps the Asian range
- **New York** ≈ 12:00–21:00 UTC — the highest-volume overlap with London

Session highs and lows, and the previous day's high/low, are common liquidity targets. The platform tracks them and marks sweeps of them. Times shift by an hour with daylight saving.`,
      },
    ],
  },
  {
    id: "derivs",
    title: "Part 4 — Futures data, news & sentiment",
    lessons: [
      {
        slug: "derivatives", title: "Funding, open interest & liquidations", level: "intermediate",
        body: `- **Funding rate:** perpetual futures pay a small fee between longs and shorts (usually every 8h) to keep the price near spot. Positive funding = longs pay shorts, meaning longs are crowded. Extreme funding often comes before sharp moves *against* the crowd (squeezes).
- **Open interest (OI):** the number of open contracts. Price up + OI up = new longs (healthy trend); price up + OI down = shorts closing (weaker rally); price down + OI up = new shorts; price down + OI down = longs closing.
- **Basis:** the difference between futures and spot prices. A large premium shows bullish leverage demand.
- **Liquidations:** forced closures of leveraged positions. Waves of liquidations accelerate moves. The platform shows live liquidation prints (Binance publishes at most one per symbol per second, so this is a lower bound) and *estimated* liquidation bands for 10×–100× leverage.`,
      },
      {
        slug: "news", title: "News & sentiment", level: "beginner",
        body: `News moves markets, but it is hard to trade: by the time you read a headline, price has often reacted. The platform:
- collects headlines from major crypto publications and the Federal Reserve
- groups duplicates and counts how many sources confirm a story
- tags the coins mentioned and the type of event (hack, regulation, ETF, macro…)
- estimates tone with a simple word-based method (a heuristic, not a prediction)

When **high-impact news** about a coin was published in the last 3 hours, the platform **stands aside** on that coin (the "news instability" regime). The **Fear & Greed index** summarises crowd emotion: extremes can mark turning points, but they are not timing signals.`,
      },
    ],
  },
  {
    id: "risk",
    title: "Part 5 — Risk, planning & psychology",
    lessons: [
      {
        slug: "risk-management", title: "Risk management (the most important lesson)", level: "beginner", diagram: "rr",
        body: `1. **Decide where you are wrong first.** The stop-loss goes where your idea is invalidated (e.g. beyond the order block or swing low), not at a random %.
2. **Risk a small, fixed % per trade** — 0.5–1% is common. Ten losses in a row then cost about 10%, not your account.
3. **Size the position from the stop distance:** size = account × risk% ÷ stop distance.
4. **Reward/risk (R:R):** if you risk $10 to make $20, R:R = 2. With R:R 2 you can be wrong 60% of the time and still roughly break even before costs.
5. **Think in R:** a +2R trade made twice your risk; a −1R trade lost exactly your risk.
6. **Limit total exposure:** correlated coins (most altcoins follow BTC) behave like one big position. The portfolio page warns about high correlation.
7. **Have a maximum drawdown** at which you stop and review (the platform pauses paper trading at your limit).`,
      },
      {
        slug: "scenarios", title: "Planning with scenarios", level: "beginner", tryIt: "walk",
        body: `Instead of predicting, plan for both directions:
- **Bullish scenario:** "If price closes above X (the last lower high), structure turns bullish → target Y (the next liquidity). Wrong if it falls back below Z."
- **Bearish scenario:** the mirror image.

Then let the market choose. Whichever trigger happens first tells you which plan is active, and the **invalidation** tells you exactly when to admit you're wrong. Step 13 of the walkthrough builds these scenarios automatically and draws the triggers and targets on the chart.`,
      },
      {
        slug: "decision-making", title: "Confluence, confidence & when NOT to trade", level: "intermediate",
        body: `**Confluence** means several *independent* reasons agree, e.g. higher-timeframe uptrend + price in discount + sell-side sweep + bullish CHoCH. Independence matters: five momentum indicators agreeing is still one reason.

The platform groups evidence into families (trend, structure, liquidity, momentum, volume, derivatives, ML, news, Elliott), caps each family, and requires **at least 3 independent families** before confirming a signal.

**When not to trade:**
- the higher timeframe and the lower timeframe disagree
- price is in the middle of a range (no edge in location)
- the reward/risk is below about 1.2 after costs
- right after major news, or in very thin or volatile markets
- you are tired, angry, or trying to "win back" a loss

NO TRADE is a valid and often the best decision.`,
      },
      {
        slug: "win-rate-trap", title: "Why a 95% win rate is a trap", level: "beginner",
        body: `Win rate on its own tells you almost nothing. What matters is **expectancy**: the average result per trade.

> expectancy = win rate × average win − loss rate × average loss

Anyone can get a 95% win rate: take profit after a tiny move and keep a wide stop. You win 19 times by $1, then lose once by $25, and you are down $6. Many "95% accurate" signal sellers work exactly like this.

This platform measured it on its own past trades:

| Take profit at | Win rate | Average per trade |
|---|---|---|
| 0.1 × risk | ~87% | **loses** |
| 1 × risk | ~60% | best result |
| 3 × risk | ~38% | about flat |

So pushing the win rate up made results **worse**. A realistic professional target is a 50–65% win rate where the average win is at least as big as the average loss, **after fees**, proven on data the strategy has never seen.

Be very sceptical of anyone, human or AI, who promises a 90%+ win rate.`,
      },
      {
        slug: "backtesting", title: "Why most strategies fail (backtesting pitfalls)", level: "intermediate",
        body: `- **Look-ahead bias:** using information that wasn't available yet, such as a swing point before it was confirmed or the close of a candle still forming. The platform tests every indicator and detector for this.
- **Overfitting:** tuning rules until they fit the past perfectly. They then fail on new data. Always check **out-of-sample** results.
- **Ignoring costs:** fees and slippage destroy many small edges.
- **Small samples:** 15 winning trades prove nothing. Look at **n** and the confidence interval.
- **Multiple testing:** try 100 strategies and a few will look great by luck.
- **Survivorship bias:** testing only coins that still exist today.

This platform's own honest result: its default rule-based strategies **lost money after costs** in testing. Learning *why* is more valuable than a pretty equity curve.`,
      },
      {
        slug: "machine-learning", title: "Can AI predict prices?", level: "advanced",
        body: `Machine learning can find weak statistical patterns, but crypto prices are noisy and patterns change (regimes shift). The platform:
- builds features only from past data
- labels outcomes with the **triple-barrier method** (did price hit a profit level, a loss level, or neither first?)
- tests models with **purged walk-forward validation**: train on the past, test on the future, with no overlap
- compares against simple baselines (the base rate and logistic regression)
- **rejects** any model that doesn't beat them after costs

The first model had a small ranking ability (AUC 0.54) but poor probability calibration, and its top feature was the day of the week, a sign it learned noise. It was rejected and is not used. Beware of anyone claiming an AI that predicts crypto with high accuracy.`,
      },
      {
        slug: "psychology", title: "Trading psychology", level: "beginner",
        body: `- **FOMO** (fear of missing out) makes you chase candles that already moved. The pre-signal stage exists so you can prepare instead of chase.
- **Revenge trading** after a loss leads to bigger, worse trades. Stick to fixed risk.
- **Moving your stop** further away to "give it room" turns small losses into large ones.
- **Overconfidence** after wins leads to oversizing.
- Keep a **journal**: why you entered, your plan, the result and what you learned. The signal timeline and paper portfolio give you this record automatically.
- **Paper-trade first** for weeks. If you can't follow your rules with fake money, you won't with real money.`,
      },
    ],
  },
];

export const GLOSSARY: [string, string][] = [
  ["ADX", "Average Directional Index — trend strength (above 25 = trending), not direction."],
  ["ATR", "Average True Range — average candle range; used to size stops and measure volatility."],
  ["Backtest", "Simulating a strategy on historical data."],
  ["Basis", "Futures price minus spot price."],
  ["Bollinger Bands", "Moving average ± 2 standard deviations; band squeeze = low volatility."],
  ["BOS", "Break of Structure — close beyond the last swing in the trend's direction."],
  ["Breaker block", "A failed order block that flips to the opposite role."],
  ["BSL / SSL", "Buy-side / sell-side liquidity — stop clusters above highs / below lows."],
  ["Candlestick", "Open, high, low and close of one period."],
  ["CHoCH", "Change of Character — first break of structure against the trend."],
  ["Confluence", "Several independent reasons agreeing."],
  ["CVD", "Cumulative Volume Delta — running total of aggressive buy minus sell volume."],
  ["Discount / Premium", "Lower / upper half of the current swing range."],
  ["Displacement", "A large, fast candle (body ≥ 1.5 ATR) showing strong intent."],
  ["Drawdown", "Decline from an equity peak."],
  ["EMA / SMA", "Exponential / simple moving average."],
  ["Equal highs/lows", "Two swing points at nearly the same price — an obvious liquidity pool."],
  ["Expectancy", "Average result per trade (in R or %)."],
  ["FVG", "Fair Value Gap — 3-candle imbalance where wicks of candles 1 and 3 don't overlap."],
  ["Funding rate", "Periodic payment between longs and shorts on perpetual futures."],
  ["HH / HL / LH / LL", "Higher high, higher low, lower high, lower low."],
  ["Inducement", "A minor swing likely to be swept before price reaches a zone."],
  ["Invalidation", "The price that proves a trade idea wrong — where the stop belongs."],
  ["Leverage", "Borrowed exposure; amplifies both gains and losses."],
  ["Limit / Market order", "Fill at your price or better / fill immediately at the best price."],
  ["Liquidation", "Forced closure of a leveraged position when margin runs out."],
  ["Liquidity sweep", "Spike through a liquidity pool, then a close back inside."],
  ["MACD", "Difference of two EMAs and its signal line — a trend/momentum indicator."],
  ["Maker / Taker", "Adds liquidity with a resting order / removes it by crossing the spread."],
  ["MSS", "Market Structure Shift — CHoCH with displacement."],
  ["Open interest", "Number of outstanding futures contracts."],
  ["Order block", "Last opposite candle before a structure-breaking move."],
  ["OTE", "Optimal Trade Entry — 62–79% retracement of a swing."],
  ["Out-of-sample", "Data not used to build or tune a strategy; the real test."],
  ["Paper trading", "Simulated trading with real prices and no real money."],
  ["POC", "Point of Control — price with the most traded volume."],
  ["Profit factor", "Gross profit ÷ gross loss (above 1 = profitable)."],
  ["R multiple", "Trade result divided by initial risk."],
  ["Regime", "The market's current mood: trend, range, volatile…"],
  ["RSI", "Relative Strength Index (0–100); momentum oscillator."],
  ["Slippage", "Difference between expected and actual fill price."],
  ["Spread", "Gap between best bid and best ask."],
  ["Stop-loss", "Order that exits a losing trade at a set price."],
  ["Support / Resistance", "Price areas where buying / selling previously stopped moves."],
  ["Swing high / low", "Local peak / trough confirmed by candles on both sides."],
  ["Timeframe", "Duration represented by each candle."],
  ["VWAP", "Volume-weighted average price for the session."],
  ["Win rate", "Share of trades that made money. Meaningless without the size of wins vs losses."],
];
