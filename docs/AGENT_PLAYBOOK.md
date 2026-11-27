# Desk playbook — how the agents trade

The working knowledge the research desk agents search (`app/agents/knowledge.py`). General trading practice is marked
as such; numbers measured by this project cite their study. Nothing here is a promise of returns.

## 1. The process: research before opinion
1. **Top-down.** Weekly and daily decide the side; 4h and 1h decide the area; 15m only times the entry. A lower-timeframe
   signal against the higher-timeframe structure needs a clear break of that structure first.
2. **Regime first.** Trend, range, high volatility and news shock each need a different playbook. In a range, breakouts
   fail more often; in a trend, buying dips beats selling rallies. When the regime is stressed (news, liquidity), size down
   or wait.
3. **Hypothesis, then test.** State the idea ("SOL continues down while below the last lower high at X"), then look for
   what would prove it wrong. Count independent evidence families (structure, liquidity, momentum, positioning, news);
   three indicators of the same family are one piece of evidence, not three.
4. **Base rates beat stories.** Before trusting a setup, ask how often this kind of setup worked in the backtests and in
   live paper trading (strategy_evidence, method track records). A method without a measured edge is an opinion.
5. **WAIT is a position.** Most of the time there is no trade. A desk that always finds a trade is overtrading.

## 2. Entry checklist (all must be true)
- Higher-timeframe structure agrees with the side, or has clearly broken.
- Price is at a reason: a retest of broken structure, an order block or fair value gap in the trend's direction, a
  liquidity sweep followed by displacement, or a confirmed breakout close.
- A real invalidation exists close enough that the stop is 1-4 ATR, not noise.
- Reward to the first target is at least 2R **after fees and slippage** (project research, CHANGELOG 0.8.0).
- Costs are at most 20% of the risk; tighter stops get eaten by fees (project research).
- No high-impact news inside the holding window, funding not extreme against the side, and the coin is liquid.

## 3. Stops
- The stop goes where the idea is wrong: beyond the last structural swing (plus a buffer of about a quarter ATR) or
  beyond the zone that should hold. Not at a round dollar amount the trader is willing to lose.
- If the structural stop is less than 1 ATR away it sits inside noise; if it is more than 4 ATR away the position becomes
  tiny. Use 2 ATR then, or skip.
- Size comes from the stop, never the other way round: quantity = dollar risk / (stop distance + costs).
- Never widen a stop after entry. Move it only in the trade's favour (to entry after the first target, then trail).

## 4. Targets and win rate
- First target at 2R or the first measured level beyond 2R after costs (resistance/support, resting liquidity, the
  opposite side of the range). Take a third there, move the stop to entry, trail the rest.
- Win rate and payoff trade off. Project research (leverage_study, 2020-26, same signal and stop): a 0.25R target won
  74-83% but earned about zero after costs; 2R won 38-48% for +0.13 to +0.42R per trade; 3R won 32-42% and earned the
  most. No target reached a 90% win rate. A strategy that wins 90% almost always has a far bigger average loss than win.
- Expectancy per trade = win rate x average win - loss rate x average loss (in R). Judge a method on 30+ trades.

## 5. Position sizing
- Risk a fixed share of the account per trade: conservative 1%, growth 2%, aggressive 3% (the platform's risk modes).
- Limit total open risk (4 / 10 / 18% by mode) and positions per coin: crypto coins fall together, so five longs are
  closer to one big trade than five independent ones.
- Losing streaks are normal. With a 40% win rate the chance that the next five trades all lose is 0.6^5, about 8%; ten
  in a row is 0.6^10, about 0.6%. After ten straight losses an account risking 2% per trade keeps 0.98^10 = 82%; risking
  20% per trade it keeps 0.8^10 = 11%.
- Drawdowns need bigger gains to recover: -10% needs +11%, -20% needs +25%, -30% needs +43%, -50% needs +100%, -75%
  needs +300%, -90% needs +900%.

## 6. Leverage and liquidation (Binance USD-M)
- Leverage does not change the risk of a stop-sized position; it changes the margin locked and where liquidation sits.
  The same trade at 2x and 20x loses the same dollars at the stop — as long as the liquidation is beyond the stop.
- Isolated long liquidation ≈ entry x (1 - 1/leverage + maintenance margin rate); a short mirrors it above. With a 1%
  maintenance rate (most altcoins) the liquidation is about 9% away at 10x and about 4% away at 20x; BTC (0.4%) about
  9.6% and 4.6%. Binance uses the mark price and tiered rates, so its number is the one that counts.
- If the liquidation is at or inside the stop, the exchange closes the trade first: the whole margin is lost plus the
  liquidation fee, and the stop never fires. Keep the liquidation at least 1.5x the stop distance away (wicks hit the
  mark price).
- "Higher return" from leverage only comes from a bigger position. Keeping the same margin at 20x makes the position
  ten times larger than at 2x: ten times the profit at the target and ten times the loss at the stop.
- Project research (leverage_study, the system's 197 trades 2020-now): stops sat a median 16.6% away and the median
  winner first fell 6.4%; at 20x 86% of trades and 68% of winners were liquidated before paying, and $100 traded at 20x
  with 20% margin per trade ended at about $4. Every stop tight enough for 20x lost money in every period tested.
- When a trader insists on 10-20x: keep the stop inside the liquidation, keep the dollar loss at the stop within the
  plan, prefer the "same risk, less margin" form over the "same margin, bigger position" form, and say the loss in
  dollars before the trade.

## 7. Futures positioning
- Funding: positive means longs pay shorts (longs crowded); extreme funding often precedes squeezes against the crowd.
- Open interest rising with price = new longs (trend support); rising while price falls = new shorts; falling with
  price rising = short covering (weaker rally).
- Liquidation clusters act as magnets; a sweep of a cluster followed by reversal is a common turning point.
- Funding is paid every 8 hours while a position is open; on a multi-day hold it is a real cost.

## 8. Managing the trade
- Before entry: write the entry, stop, first target, size, leverage and the dollar loss at the stop.
- At 2R: take a third, move the stop to entry. Then trail (the system uses 3 ATR below the close for longs).
- Time stop: a swing that has not moved after its expected window frees the capital (the system uses 60 days on daily).
- Never add to a loser. Never move a stop away from price. Record every exit with its reason.

## 9. Sessions and news
- Asia often ranges; London and New York opens bring the expansion and the false breaks (ICT "Judas swing").
- CPI, FOMC, large unlocks and exchange incidents can override any chart; stand aside or size down before them.
- Weekend liquidity is thinner: wicks are larger and stops slip more.

## 10. Psychology and discipline
- The plan is made before the trade, when there is no position to defend.
- After two or three losses in a row, the next trade is the most dangerous one (revenge trading). The platform's loss
  guard pauses entries when recent trades average below the plan.
- A winning trade with a broken plan is still a mistake; a losing trade that followed the plan is a normal cost.
