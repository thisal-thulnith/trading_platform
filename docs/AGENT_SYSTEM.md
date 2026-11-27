# Agent system — how the research desk works

Code: `backend/app/agents/research_desk.py` (orchestration), `validators.py`, `journal.py`, `knowledge.py`,
`toolkit.py` (tool-use loop), `app/risk/plan.py` (every plan number). UI: `frontend/src/components/desk/`.

## Design principles
1. **Research before opinion.** Facts first, across timeframes and sources; views come after.
2. **The analysis is the AI's own.** The tools hand the agents measurements (structure, levels, indicators,
   positioning, news, backtest evidence), never a rule-made verdict, and only the Head Trader decides. There is no
   rule-based fallback: without a working AI the desk says "AI unavailable" and shows nothing in its place.
3. **Numbers come from tools.** Agents reason; deterministic code measures. The plan's entry, stop, target, leverage,
   margin, size and risk only come from `trade_plan`. Every number an agent writes is fact-checked.
4. **Adversarial by design.** Raven, the Red Team lead, attacks the leading view with base rates and the desk's own record, and the
   leading analyst must answer.
5. **Independent checks with a veto.** The Validator is code, not a model: it cannot be talked out of a failure, and a
   critical failure blocks a trade.
6. **Learn from outcomes.** Every call is journaled and graded on the candles; agents read that record.
7. **Visible work.** Every tool call is an event; each agent's cursor shows what it is doing, where, as it happens.

## The pipeline
Each AI lead first runs its specialists in parallel (narrow tools, a focused brief, a 3-5 sentence report), then reads
their reports, checks what is decisive with its own tools and writes the synthesis. Ids in brackets are the stable keys
the journal, validators and page use.

| # | Stage | Lead | Specialists | Output |
|---|---|---|---|---|
| 1 | Research | Atlas, Research Lead (`researcher`) | Horizon (timeframe map), Compass (whole market: regime, BTC dominance, stablecoins, breadth), Archive (coin rank, crowding, desk record) | a research brief: facts, conflicts, tailwind or headwind, unknowns, open questions |
| 2 | Analysis | Vega, Chief Technical Strategist (`technician`) | Keystone (SMC/ICT structure, liquidity, zones), Pulse (trend, momentum, volatility), Chronos (ten methods with track records), Bastion (levels for both sides) | a view, confidence, levels, triggers, invalidation |
| 2 | Analysis | Tide, Flow & Derivatives Lead (`flow`) | Torque (funding, OI vs price, ratios), Undertow (liquidations, bands, order book), Echo (news, fear & greed) | positioning: supports or fights the chart |
| 2 | Analysis | Ledger, Fundamentals Lead (`fundamental`) | Mint (tokenomics, dilution), Bedrock (TVL, fees, revenue, valuation), Beacon (rank, liquidity, ATH, strength vs BTC) | which side fundamentals favour, weighted by horizon |
| 3 | Debate | Raven, Red Team Lead (`skeptic`) → leading analyst replies | Odds (backtests, base rates), Mirror (desk record), Fracture (pre-mortem) | the attack, then concede/defend and an updated confidence |
| 4 | Plan | Sentinel, Risk Officer (`risk`) | Anchor (stop and target geometry), Ballast (leverage, liquidation, funding cost) | the plan exactly as `trade_plan` computed it, the 10x/20x options in dollars |
| 5 | Validate | Verity, Validator (`validator`, code) | — (check groups) | two scores, the gate; agents with unverified numbers revise once |
| 6 | Decision | Apex, Head Trader (`head`) | Bull Case, Bear Case (no tools: steelman each side from the conversation) | LONG / SHORT / WAIT, conditions, invalidation; a final validation pass may block it |
| 7 | Learn | Journal (code) | — | target first / stop first / expired, R; stats by coin, decision and evidence score |

The three analysis leads run in parallel on the same brief so none anchors on another. Fundamentals are context only on
15m/1h, set conviction on 4h and are a real input on 1d (docs/FUNDAMENTALS.md); on 4h/1d Ledger's view counts half in
the planned side and votes in the consistency check. A specialist's tool calls move its lead's cursor, labelled with
the specialist's name. `AIT_DESK_SUBAGENTS=false` runs the leads alone; `AIT_DESK_LLM_CONCURRENCY` (default 6) caps AI
calls in flight so bursts stay under provider rate limits.

Agents speak in one shared conversation: each sees what the desk said before it. The two analysts work in parallel
on the same brief so neither anchors on the other.

## Failure handling
- A provider error that every agent would hit (no credits, expired key, rate limit) stops the desk: before the first
  answer the session is "unavailable"; later, the agents that finished are shown and there is no decision.
- The add-trade form's plan comes from one AI agent, Pilot the Trade Planner; without the AI it returns 503 and the form
  offers manual entry of the user's own fill.
- One agent failing for another reason does not stop the desk.
- If Sentinel, the Risk Officer, never produced a valid plan, the server computes the default plan for the leaning side.

## Cursors
Each tool call emits an event with where it happens: `click` (a timeframe button), `el` (an agent card, the
validators panel, the decision), `points` (prices on the chart: levels, stop options, swings) or `draw` (the plan:
entry, then a drag to the stop and to the target). The page plays the events in order with human-like motion —
curved paths, Fitts's-law timing, overshoot, a hesitation before a click, imperfect aim, idle drift — and speeds up
when it falls behind the agents. "Catch up" jumps to the present; cursors can be switched off.

## Cost control
- **Readiness gate first** (app/agents/readiness.py): a free code-only score decides whether the agents run at all. Only a
  *ready* setup (score ≥ 70, at least a pre-signal, no hard block) is analysed; otherwise the trader sees what to wait for
  and gets an alert when it turns ready. "Analyse anyway" overrides it.
- Nothing runs on page load. The AI agents start only when the user has picked a coin, entered the margin and pressed
  Analyse. Results are cached for 20 minutes per coin, timeframe, side, margin, style and venue.
- The shortlist is code only: one price call for every coin, deep analysis of the streamed universe plus the 20 most
  liquid and active coins, refreshed every 15 minutes.
- One analysis is 7 AI leads plus 20 specialists — about 30 AI calls and 50-80 tool calls (about 9 AI calls with
  `AIT_DESK_SUBAGENTS=false`); the Validator costs nothing. Fundamentals data is cached (an hour to a day).

## Page structure (frontend/src/components/desk/ResearchDesk.tsx)
- **Top bar**: Coin and timeframe · Your trade (margin, risk style, venue, side) · Analyse · cursors and the AI status light.
- **Left**: the shortlist (code only) and search.
- **Centre**: the chart; under it the result — the steps before an analysis, live progress while the agents work,
  then the Head Trader's decision with tabs: Plan, Leverage options, Validation, How to place it.
- **Right**: the AI desk — stage progress and each agent's analysis, tools, confidence and fact-check.
