# Fundamentals — how the desk reads a coin's value, supply and usage

The working knowledge of the Fundamentals team (Ledger with Mint, Bedrock and Beacon) and of Atlas's market scout
Compass. Data comes from `app/agents/fundamentals.py` (CoinGecko and DefiLlama, cached). The bands below are general
market practice, **not** measured by this project: treat them as reading aids, not thresholds with a proven edge.
Nothing here is a promise of returns.

## 1. When fundamentals matter
- **Horizon decides the weight.** On 15m and 1h, fundamentals are context only: they never decide a trade and rarely
  move price inside the holding window. On 4h they colour conviction and sizing. On 1d (position trades held days to
  weeks) they are a real input next to the higher-timeframe structure.
- **Fundamentals explain *what*, the chart explains *when*.** A cheap, growing protocol can fall for months; a
  dilutive coin can squeeze higher on positioning. Use fundamentals to choose which side you would rather be on and how
  much conviction to carry, never to time an entry.
- **Supply events beat narratives.** Large unlocks and emissions are the most reliable fundamental headwind on a
  multi-week horizon: new supply has to be absorbed by new buyers.

## 2. Tokenomics (Mint)
- **FDV / market cap** — fully diluted value divided by the circulating value.
  - About 1.0-1.3: most supply already trades; little dilution ahead.
  - 1.3-2.0: moderate future supply; check the pace (circulating % of max).
  - Above 2: heavy dilution ahead. Locked tokens unlock into the market over time; longs fight that supply. Above 3 is
    a structural headwind for multi-week longs.
- **Locked or unissued value (FDV − market cap)** — the dollar value still to come. Compare it with 24h volume: if it is
  many months of volume, the market cannot absorb it quickly.
- **Circulating % of max or total** — under 50% means most supply is still to come.
- **No max supply** — the coin can inflate (SOL, ETH, DOGE). Not bad by itself: what matters is issuance net of burns
  versus demand. A fixed max supply is not bullish by itself either.

## 3. Usage and valuation (Bedrock)
- **TVL (total value locked)** — deposits in a protocol or on a chain. Measures usage, not profit. Falling TVL with
  falling price means capital leaving; TVL falling only because token prices fell is weaker evidence.
- **Market cap / TVL** — under 1 is cheap for a DeFi protocol (the market values it below its deposits); 1-3 normal;
  over 10 the token is priced well above its usage. For L1 chains the ratio is naturally much higher (the token is
  also money and collateral); compare chains with chains, not with protocols.
- **Fees** — what users pay to use the network or protocol. **Revenue** — the part kept by the protocol or token holders.
  Fees show demand; revenue shows what a token holder can claim.
- **Market cap / annual fees (P/F)** and **/ annual revenue (P/S)** — like a price-to-sales multiple.
  - DeFi protocols: P/F under 10 is cheap, 10-50 normal, over 100 expensive.
  - L1 chains: P/F of several hundred is common; the market pays for the monetary premium.
  - No revenue (P/S empty) means token holders capture nothing directly: value depends on narrative and future switches.
- **Fees trend (last 30 days annualized ÷ last year)** — above 1 usage is accelerating; under 0.7 usage is fading.
  Accelerating fees with a falling price is the classic fundamental divergence; fading fees with a rising price is a
  rally on narrative.
- Meme, payments and store-of-value coins have no usage metric here. Their "fundamentals" are liquidity, supply and
  attention: say that plainly instead of forcing a valuation.

## 4. Market standing (Beacon)
- **Market cap rank** — top 20 are deep and institutionally held; past 100 thin books and higher manipulation risk.
- **Volume / market cap (24h)** — 1-10% is healthy. Under 1% thin trading: slippage and gaps. Over 25% speculative churn
  (often a pump, a listing or a squeeze) that tends to mean-revert.
- **Distance from all-time high** — more than 80% below after a year or more is a broken chart until proven otherwise;
  within 10% of the high means price discovery (no overhead supply) or a double top: the chart decides which.
- **Performance against BTC (percentage points)** — positive over 30 days and 1 year is relative strength: in a falling
  market, the coins that fell less usually lead the next rebound. Negative across 7d/30d/1y is persistent weakness;
  shorts in the weakest coins and longs in the strongest is the momentum-factor reading.
- **Community votes / watchlist users** — attention, not quality. Extreme bullish votes near a high are a crowding sign.

## 5. Whole-market context (Compass)
- **BTC dominance** — rising dominance in a falling market means money hiding in BTC: altcoin longs fight the tide.
  Falling dominance in a rising market is "alt season" conditions.
- **Stablecoin supply** — the market's dry powder. Growing supply (7d and 30d up) is new money arriving; a shrinking
  supply is liquidity leaving the asset class and a headwind for every long.
- **Breadth** — share of the top 100 coins up, and beating BTC, over 7 and 30 days. Under 30% up is a broad sell-off:
  single-coin longs need an exceptional reason. Over 70% up is a broad rally: shorts need one.
- **Total market cap change (24h)** — a move of more than ±5% in a day is a market-wide event: correlations go to one and
  coin-specific analysis matters less.

## 6. Turning fundamentals into a view
1. State the horizon first (from the timeframe). On 15m/1h say "context only" and keep the confidence low.
2. Name the one or two facts that matter most (a dilution overhang, accelerating fees, persistent weakness against
   BTC, shrinking stablecoin supply) — not a list of every metric.
3. Say what the fundamentals favour (long, short or neither) and how strongly, and what would change it.
4. Say what is unknown: no usage data, coin outside the top 250, no unlock schedule (the desk has no unlock calendar;
   FDV / market cap is the proxy).
