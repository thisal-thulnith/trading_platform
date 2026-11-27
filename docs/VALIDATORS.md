# Validators — how the desk checks its own research

Code: `backend/app/agents/validators.py`. Tests: `backend/tests/test_research_desk.py`. UI: the Validators panel on the
Research desk and in the add-trade form (`frontend/src/components/desk/ValidationPanel.tsx`).

## Where they run
The research desk has six stages: Research → Analysis → Debate → Plan → **Validate** → Decision.

1. **Validate (before the decision).** The Validator agent is deterministic code, not a language model, so it cannot be
   talked out of a failure. It checks the data, fills research gaps itself, tests the Risk Manager's plan for the side
   the team leans to, weighs the evidence and fact-checks every agent so far. The Head Trader must read this report
   (`validation_report` tool): a critical failure means WAIT.
2. **Final pass (after the decision).** The same checks run again on the side the Head Trader chose, plus a fact-check
   of the Head Trader and a consistency check. If anything critical fails, the gate turns a LONG/SHORT into WAIT and
   says which check blocked it.
3. **Add-trade form.** `POST /api/agent/plan` returns the data-quality, trade and evidence checks for the plan it proposes.

## The six groups
| Group | Kind | What it checks |
|---|---|---|
| Data quality | research | candles fresh (≤ 2 candles old, critical), 300+ candles, live price within 1-3% of the analysed close (critical above 3%), the coin moves enough to trade (ATR ≥ 0.15%, critical), spread |
| Research coverage | research | higher timeframe read and compared, ten methods reviewed, backtest evidence pulled, futures positioning available, news read, whole-market regime, every AI agent used tools, knowledge base consulted. **Missing research is run by the validator itself** and listed as "gaps filled". |
| Trade checks | evidence | side vs the chart read and vs the higher timeframe (critical when both are against it), regime stress, order valid on Binance (critical), reward/risk ≥ 2 after fees (critical), costs ≤ 20% of the risk (critical), stop 1-4 ATR, liquidation ≥ 1.5x the stop, risk within the mode (critical), crowded funding on our side, shorts research |
| Evidence & base rates | evidence | ≥ 3 independent evidence families for the side, a confirmed engine setup that passed every check, that strategy's backtest (fail when suspended), past cases (30+ wanted), methods with a measured out-of-sample edge that agree |
| Fact-check | research | every number an agent wrote is traced to platform data: tool results, the analysis, the plan, the playbook. Rounding is allowed (85.8 for 85.81, 69% for 0.69). Timeframes, small counts and years are skipped. Unverified numbers are listed; a Head Trader with mostly unverified numbers is critical. |
| Consistency | research | the decision vs the analysts (critical when both disagree), vs the validators (critical failures), and confidence vs the evidence |

## Scores
- **Research quality** (data, coverage, fact-check, consistency): how well the research was done.
- **Trade evidence** (trade checks, base rates): how strong the case for this trade is.
Each check scores pass 1, warn 0.5, fail 0; each group weighs the same; grades A ≥ 85, B ≥ 70, C ≥ 55, else D.
A high research score with a low evidence score means "well researched, weak trade" — the honest common case.


## Market & execution (0.22)
Warnings only, counted in the trade-evidence score: the market's tide (BTC trend and breadth against the side), a tested
opposing level less than 1R before the target, a stop within 0.15 ATR of resting liquidity (stop-hunt bait), funding paid
over a typical hold (15m 0.25 d, 1h 1 d, 4h 3 d, 1d 10 d) above 10% of the target profit, a high-impact headline in the last
24 hours, and a spread above 5% of the stop distance. Consistency also fails an invalidation on the wrong side of the
entry, warns when confident leads disagree and when confidence exceeds the 85% cap; every specialist's numbers are
fact-checked as one line per team.
