"""Research desk: a team of AI agents that researches one coin first, then analyses, debates, plans, gets validated
and decides — and a journal that grades every call so the desk learns from its record.

How the desk works (docs/AGENT_SYSTEM.md). Each lead runs its specialists in parallel first, then synthesises them:

  1. Research   Atlas     Research Lead               Horizon (timeframes), Compass (whole market), Archive (coin record)
  2. Analysis   Vega      Chief Technical Strategist  Keystone (SMC/ICT), Pulse (momentum), Chronos (methods), Bastion
                                                      (levels)                                } the three leads run in
                Tide      Flow & Derivatives Lead     Torque (positioning), Undertow (book and  } parallel on the brief
                                                      liquidations), Echo (news, sentiment)   }
                Ledger    Fundamentals Lead           Mint (tokenomics), Bedrock (usage and valuation), Beacon (standing)
  3. Debate     Raven     Red Team Lead               Odds (base rates), Mirror (desk record), Fracture (pre-mortem);
                                                      the leading analyst then replies (concede or defend, update)
  4. Plan       Sentinel  Risk Officer                Anchor (stop/target), Ballast (leverage, liquidation, costs);
                                                      trade_plan computes every number
  5. Validate   Verity    Validator (code)            deterministic checks; fills research gaps; fact-checks every
                                                      number and sends agents with unverified numbers back once
  6. Decision   Apex      Head Trader                 Bull Case and Bear Case; LONG / SHORT / WAIT after the validation
                                                      report; a final validation pass blocks a trade with a critical failure
  7. Learn      Journal   records the call and grades it later on the candles (app/agents/journal.py)

The analysis is the AI's own: the tools hand the agents measurements (structure, levels, indicators, positioning,
fundamentals, news, backtest evidence), never a rule-made verdict, and only the Head Trader decides. There is no
rule-based fallback: without a working AI key the desk says the AI is unavailable and shows nothing in its place. Every
tool call is streamed as an event, and the desk page moves each lead's cursor from these real events (a specialist's
calls move its lead's cursor). AIT_DESK_SUBAGENTS=false runs the leads alone (about a third of the AI calls).
"""
import asyncio
import json
import time
import uuid

import httpx

from app.agents import desk_tools, knowledge, readiness
from app.agents.llm import note_failure, note_success, provider
from app.agents.llm import status as llm_status
from app.agents.toolkit import Tool, run_agent
from app.config import get_settings
from app.logging import log
from app.risk import futures as fut
from app.risk import plan as tp

SESSIONS: dict[str, dict] = {}
_BY_KEY: dict[tuple, str] = {}
CACHE_S = 1200
MAX_SESSIONS = 60
_filters: dict[str, tuple[float, fut.Filters]] = {}
FATAL = ("no credits", "insufficient_quota", "quota", "billing", "invalid api key", "invalid_api_key", "authentication",
         "error code: 401", "error code: 429")
TFS = ("15m", "1h", "4h", "1d")
_HTF = {"15m": "4h", "1h": "4h", "4h": "1d", "1d": "1d"}
DISCLAIMER = ("Decision support, not financial advice. Paper only: nothing is sent to Binance; you place and manage "
              "the orders yourself.")

STAGES = [("research", "Research"), ("analysis", "Analysis"), ("debate", "Debate"),
          ("plan", "Plan"), ("validate", "Validate"), ("decision", "Decision")]

# Each lead has a persona name (what the page shows) and a title (what it does). Ids stay stable: the journal, the
# validators and the page key on them.
AGENTS = [
    {"id": "researcher", "name": "Atlas", "title": "Research Lead", "stage": "research", "color": "#0a84ff", "kind": "ai",
     "role": "plans the research, then gathers the facts across timeframes, the whole market and the coin's record",
     "tools": ["chart_analysis", "market_regime", "market_macro", "derivatives_and_news", "coin_profile", "model_forecast",
               "chart_patterns", "search_knowledge"],
     "task": "Do the research before anyone forms a view. Write down for yourself the 3-4 questions that decide this trade, then answer them with tools: chart_analysis "
             "for the requested timeframe AND one higher timeframe (15m/1h: 4h; 4h: 1d; 1d: 1d only), market_regime, "
             "market_macro, derivatives_and_news and coin_profile. Search the knowledge base for the one or two concepts "
             "that matter most in what you found. Write a research brief: the facts that matter most, where the "
             "timeframes agree or conflict, whether the whole market is a tailwind or a headwind, what is unknown or "
             "stale, and the open questions for the analysts. Do not pick a side yet."},
    {"id": "technician", "name": "Vega", "title": "Chief Technical Strategist", "stage": "analysis", "color": "#7d5cf6",
     "kind": "ai", "role": "structure, liquidity, momentum and levels — with the price that proves the idea wrong",
     "tools": ["chart_analysis", "chart_patterns", "method_views", "key_levels", "search_knowledge"],
     "task": "Build the technical case. Use chart_analysis (requested and higher timeframe) and method_views (ten "
             "classic methods with their measured track records — weigh a method by its record, not by its story). "
             "Call key_levels for the side you lean to. Give: your direction and why, the two levels that matter, the "
             "bullish and bearish triggers and the exact invalidation. If the evidence is mixed, say neutral."},
    {"id": "flow", "name": "Tide", "title": "Flow & Derivatives Lead", "stage": "analysis", "color": "#e8890c", "kind": "ai",
     "role": "funding, open interest, positioning, liquidations, news and sentiment",
     "tools": ["derivatives_and_news", "order_flow", "funding_across", "options_market", "market_regime", "coin_profile",
               "research_facts", "search_knowledge"],
     "task": "Read positioning and context. Use derivatives_and_news (funding, open interest vs price, long/short and "
             "taker ratios, liquidations, news, fear & greed), market_regime and coin_profile (trend rank vs ~120 "
             "coins, crowding). Say whether positioning supports or fights the technical read, and whether news or "
             "the regime could override the chart. If futures data is missing, say what that leaves unknown."},
    {"id": "fundamental", "name": "Ledger", "title": "Fundamentals Lead", "stage": "analysis", "color": "#2f9e44",
     "kind": "ai", "role": "tokenomics and dilution, usage and valuation, relative strength and the market's liquidity",
     "tools": ["tokenomics", "onchain_usage", "market_standing", "market_macro", "search_knowledge"],
     "task": "Build the fundamental case. Use tokenomics (FDV vs market cap, supply still to come), onchain_usage (TVL, "
             "fees, revenue and what the market pays for them), market_standing (rank, liquidity, distance from the high, "
             "performance against BTC) and market_macro (BTC dominance, stablecoin supply, breadth). Search the knowledge "
             "base for 'fundamentals horizon' and the bands you need. Start from the horizon: on 15m and 1h fundamentals "
             "are context only — say so and keep your confidence near 0.5; on 4h they set conviction; on 1d they are a "
             "real input. Say which side the fundamentals favour, how strongly, the one or two facts that decide it, and "
             "what is unknown."},
    {"id": "quant", "name": "Cipher", "title": "Quant Lead", "stage": "analysis", "color": "#475569", "kind": "ai",
     "role": "the computational case: trained models, learned chart patterns, the backtested signal plan and the live "
             "signal test, each weighed by its accuracy on unseen years",
     "tools": ["tested_signal", "model_forecast", "chart_patterns", "signal_test_record", "strategy_evidence",
               "search_knowledge"],
     "task": "Build the computational case, the way a quant desk would. Use tested_signal (the platform's backtested "
             "daily plan for this coin: side, status TRADE or WATCH with the gates it failed, levels, and how the same "
             "pick did on 2025-26 against a random coin), model_forecast and chart_patterns (each probability against "
             "its base rate, only 'accepted' models count as evidence) and signal_test_record (how the platform's "
             "signals are doing live since they were frozen). Say which side the numbers favour, how strong the edge is "
             "in R per trade and in lift over the base rate, and whether the tested rule would take this trade today. "
             "The models work on daily candles with a 7-14 day horizon: on 15m/1h they are context for the direction, "
             "not a trigger. Where the computation and the chart reading disagree, say so plainly. Never present a "
             "weak or untested number as an edge."},
    {"id": "skeptic", "name": "Raven", "title": "Red Team Lead", "stage": "debate", "color": "#e5487f", "kind": "ai",
     "role": "attacks the leading idea with base rates, the desk's own record and a pre-mortem",
     "tools": ["strategy_evidence", "desk_record", "model_forecast", "chart_patterns", "tested_signal", "signal_test_record",
               "research_facts", "coin_profile", "search_knowledge"],
     "task": "Attack the leading view of the analysts above. Use strategy_evidence (backtests and past cases of the "
             "setups on this chart), desk_record (how the desk's own calls like this worked out), research_facts "
             "('win_rate', 'leverage', and 'shorts' if the lean is short) and the coin's record. What is the base rate "
             "for a trade like this? What is the most likely way it fails? Is this a real edge or a story? End with "
             "whether the idea survives your attack."},
    {"id": "risk", "name": "Sentinel", "title": "Risk Officer", "stage": "plan", "color": "#0e9f84", "kind": "ai",
     "role": "picks the stop and target from measured levels; the plan tool computes size, leverage and margin",
     "tools": ["key_levels", "trade_plan", "model_forecast", "tested_signal", "research_facts", "search_knowledge"],
     "task": "Build the trade plan for {side_hint}. Call key_levels for that side, choose the stop (a real "
             "invalidation from the options — not noise) and a target at 2R or a measured level beyond it, then call "
             "trade_plan with them. If the plan fails a check (minimum order, reward/risk under 2, costs over 20% of the "
             "risk), adjust once and call trade_plan again. Report the plan exactly as the tool returned it: entry, "
             "stop, target, leverage, margin, quantity, risk in dollars, liquidation, reward/risk. Explain why this "
             "leverage and not more. The tool also returns leverage_options (10x and 20x, each 'less margin' and "
             "'bigger'): state in dollars what the 10x and 20x versions lose at the stop and make at the target, whether "
             "they are liquidated before the stop, and which one you would accept, if any. When the trader left the risk "
             "style to the desk, choose it and pass it to trade_plan as style: conservative (1% of the budget at risk) by "
             "default; growth (2%) only when the engine setup is validated, the timeframes agree and the Red Team's "
             "attack did not break the idea; aggressive (3%) only when all of that holds, the readiness score is 85 or "
             "more and the timeframe is 4h or 1d. Say which style you chose and why."},
    {"id": "validator", "name": "Verity", "title": "Validator", "stage": "validate", "color": "#0b93b8", "kind": "checks",
     "role": "code, not a model: checks the data, fills research gaps, fact-checks every number, sends unverified text back",
     "tools": [], "task": ""},
    {"id": "head", "name": "Apex", "title": "Head Trader", "stage": "decision", "color": "#c98a0b", "kind": "ai",
     "role": "the desk's decision",
     "tools": ["validation_report", "desk_record", "model_forecast", "chart_patterns", "tested_signal", "signal_test_record",
               "chart_analysis", "strategy_evidence", "search_knowledge"],
     "task": "Make the call: LONG, SHORT or WAIT. First read validation_report: a critical failure means WAIT, and "
             "warnings lower your confidence. Check desk_record: how did calls like this work out? Weigh the research, "
             "the technical, flow and fundamental leads (fundamentals count little on 15m/1h and more on 4h/1d), the Red "
             "Team's attack and the reply, the bull and bear cases, the Quant Lead's computational case (the tested "
             "plan and whether its rule trades today) and the Risk Officer's plan. When the chart reading and the "
             "tested computation disagree, lower your confidence and say which one you follow and why. Take a "
             "trade only when the evidence agrees across timeframes and positioning, the Red Team's attack does not "
             "break it and the plan passes its checks; otherwise WAIT and say exactly what would make it a trade (the "
             "trigger price or event). If the trader wants 10-20x, say which "
             "leverage option you would allow and the dollar loss at the stop, or why none. Never change the plan's "
             "numbers."},
]
AI_IDS = [a["id"] for a in AGENTS if a["kind"] == "ai"]
BY_ID = {a["id"]: a for a in AGENTS}
ANALYSTS = ("technician", "flow", "fundamental", "quant")

# Each lead's specialists. They run in parallel with narrow tools and a focused brief; the lead then reads their
# reports, checks what is decisive with its own tools and writes the synthesis. {htf} and {side} are filled per run.
SUBS: dict[str, list[dict]] = {
    "researcher": [
        {"id": "horizon", "name": "Horizon", "focus": "multi-timeframe map", "tools": ["chart_analysis", "search_knowledge"],
         "task": "Map the timeframes. Call chart_analysis for the requested timeframe and for {htf}. For each: the trend and "
                 "structure (the last BOS or CHoCH; higher highs and lows or lower), where price sits in its range "
                 "(premium or discount, next to which level) and momentum. Then say where the timeframes agree, where they "
                 "conflict, and which one is in control."},
        {"id": "compass", "name": "Compass", "focus": "whole-market regime and liquidity",
         "tools": ["market_regime", "market_macro", "options_market", "search_knowledge"],
         "task": "Read the whole market before the coin. Call market_regime and market_macro: BTC's trend and the quant "
                 "regime, BTC dominance, the stablecoin supply change (the market's dry powder), the breadth of the top 100 "
                 "(share up and share beating BTC) and fear & greed. Is the tide in or out for longs, and for shorts?"},
        {"id": "archive", "name": "Archive", "focus": "the coin's character and record",
         "tools": ["coin_profile", "desk_record", "search_knowledge"],
         "task": "Profile the coin. Call coin_profile (trend rank among ~120 coins, crowding, its breakout record since "
                 "2020) and desk_record (how the desk's past calls on it worked out). Does this coin trend cleanly or chop? "
                 "Is it crowded? What does its own history say about setups like today's?"},
    ],
    "technician": [
        {"id": "keystone", "name": "Keystone", "focus": "structure, liquidity and zones (SMC / ICT)",
         "tools": ["chart_analysis", "search_knowledge"],
         "task": "Read the structure like an SMC/ICT trader. Call chart_analysis for the requested timeframe and {htf}: the "
                 "last BOS and CHoCH, premium or discount of the dealing range, the nearest unmitigated order block and fair "
                 "value gap in the trend's direction, and where resting liquidity sits (equal highs or lows, prior day and "
                 "week highs and lows). The high-quality sequence is a liquidity sweep, then displacement that breaks "
                 "structure, then a return into the zone it left. Say whether that sequence is present, partial or absent, "
                 "and the price that invalidates it."},
        {"id": "pulse", "name": "Pulse", "focus": "trend, momentum and volatility",
         "tools": ["chart_analysis", "search_knowledge"],
         "task": "Read trend and momentum from chart_analysis: price against the 20/50/200 EMAs and their order, ADX (trend "
                 "strength — under about 20 there is no trend), RSI (its regime, and divergence against price at the last "
                 "swing), the MACD histogram (momentum building or fading), the Supertrend direction, Bollinger %B and ATR "
                 "(volatility expanding or compressing). Trend signals count only when ADX shows a trend; oscillators count "
                 "only in a range. Say what momentum confirms and what it contradicts."},
        {"id": "chronos", "name": "Chronos", "focus": "classic methods with measured track records",
         "tools": ["method_views", "search_knowledge"],
         "task": "Call method_views: ten classic methods (Wyckoff, Dow, Gann, Market Profile, harmonics, ICT Power of 3, "
                 "NeoWave, Hurst, Quasimodo, Bill Williams), each with its measured out-of-sample record. Weigh each by its "
                 "record, not its story — a method with no measured edge is an opinion. Report which methods with a real "
                 "record agree, which disagree, and the Wyckoff or Elliott phase if one is clear."},
        {"id": "bastion", "name": "Bastion", "focus": "the levels that matter for both sides",
         "tools": ["key_levels", "search_knowledge"],
         "task": "Call key_levels for LONG and for SHORT. For each side: the structural stop option, the nearest target "
                 "that is at least 2R, and how many R away the first obstacle is. Which side has the cleaner geometry (a "
                 "tight, real invalidation and room to 2R)? Name the trigger price for each side."},
    ],
    "flow": [
        {"id": "torque", "name": "Torque", "focus": "leverage and positioning",
         "tools": ["derivatives_and_news", "funding_across", "research_facts", "search_knowledge"],
         "task": "Read leverage and positioning from derivatives_and_news: funding (8h and per year — persistently positive "
                 "means crowded longs paying), open interest against price over 1h, 4h and 24h (price up with OI up: new "
                 "longs; price up with OI down: short covering; price down with OI up: new shorts; price down with OI down: "
                 "long liquidation), the basis, the long/short and taker ratios and the squeeze flags. Where is the crowd, "
                 "and which side would be forced out if price moves against it? Check funding_across: is the crowding "
                 "the same on Binance, Bybit and OKX, or one venue only?"},
        {"id": "undertow", "name": "Undertow", "focus": "order flow, order book and liquidations",
         "tools": ["derivatives_and_news", "order_flow", "search_knowledge"],
         "task": "Read the plumbing from derivatives_and_news: recent liquidations (which side was flushed, and how much), "
                 "the estimated liquidation bands (forced orders cluster there and price is drawn to them), the order book "
                 "imbalance, depth and spread. Has one side already been cleared (fuel spent), or is a cluster still "
                 "waiting near the price? Call order_flow: are takers buying or selling into this move, and does the "
                 "cumulative volume delta confirm the price or diverge from it (a divergence is an early warning)?"},
        {"id": "echo", "name": "Echo", "focus": "news, sentiment and options",
         "tools": ["derivatives_and_news", "options_market", "market_regime", "search_knowledge"],
         "task": "Read news and sentiment: the headlines with their impact, sentiment and age, and fear & greed. Is there a "
                 "high-impact event inside the holding window that could override the chart? Is sentiment at an extreme "
                 "(extremes are contrarian at turning points, not signals by themselves)? Call options_market: is "
                 "implied volatility rising (fear) or falling, is the put/call ratio stretched, and is the near-term "
                 "implied vol above the one-month (stress) or below it (calm)?"},
    ],
    "fundamental": [
        {"id": "mint", "name": "Mint", "focus": "tokenomics and dilution", "tools": ["tokenomics", "search_knowledge"],
         "task": "Call tokenomics. Read FDV against market cap, the locked or unissued value still to come (compare it with "
                 "the 24h volume), the circulating share of the maximum and whether supply can inflate. Search the "
                 "knowledge base for 'FDV dilution' for the reading bands. Is supply a headwind or neutral for each side "
                 "over the holding horizon?"},
        {"id": "bedrock", "name": "Bedrock", "focus": "usage, revenue and valuation",
         "tools": ["onchain_usage", "search_knowledge"],
         "task": "Call onchain_usage. For a chain or DeFi protocol: TVL and its 7-day change, fees and revenue (last 30 "
                 "days annualized and last year), the fee trend, and market cap against TVL, annual fees and annual revenue. "
                 "Search the knowledge base for 'price to fees' for the bands of its kind (chain or protocol). Is usage "
                 "growing or fading, and is the token cheap or expensive for that usage? With no usage data, say what kind "
                 "of coin it is and that its valuation is unknown."},
        {"id": "beacon", "name": "Beacon", "focus": "market standing and relative strength",
         "tools": ["market_standing", "search_knowledge"],
         "task": "Call market_standing. Read the rank, 24h volume against market cap (liquidity or speculative churn), the "
                 "distance and time from the all-time high, the 7d/30d/1y change and — most important — the performance "
                 "against BTC in percentage points over each window (persistent relative strength or weakness). What do "
                 "its categories say about the sector narrative?"},
    ],
    "quant": [
        {"id": "gauge", "name": "Gauge", "focus": "the backtested signal plan",
         "tools": ["tested_signal", "search_knowledge"],
         "task": "Call tested_signal with no side, then with the other side. For each: is it TRADE or WATCH and which gates "
                 "failed, the entry, stop and targets, the rank among liquid coins, and how the same daily pick did on "
                 "2025-26 (trades, share in profit, average R) against a random coin on the same days. Which side has a "
                 "tested edge today, if any?"},
        {"id": "prism", "name": "Prism", "focus": "the trained models",
         "tools": ["model_forecast", "search_knowledge"],
         "task": "Call model_forecast. For every 'accepted' model compare the probability with its base rate and say how "
                 "far above or below it is (lift). Ignore 'rejected' models and call 'weak' ones weak. Which side do the "
                 "models favour, how strongly, and what is the drop risk?"},
        {"id": "mosaic", "name": "Mosaic", "focus": "learned chart patterns and live results",
         "tools": ["chart_patterns", "signal_test_record"],
         "task": "Call chart_patterns: what followed the 50 most similar past charts (against the base rates) and the "
                 "learned pattern this chart belongs to, with their measured accuracy. Then call signal_test_record: how "
                 "the platform's signals are doing on live prices so far, and whether the sample is still too small to "
                 "trust. Say whether both point the same way."},
    ],
    "skeptic": [
        {"id": "odds", "name": "Odds", "focus": "base rates from the backtests",
         "tools": ["strategy_evidence", "research_facts"],
         "task": "Call strategy_evidence and research_facts('win_rate'). For the setups on this chart: their backtest "
                 "expectancy and confidence interval, whether they are suspended, the checks they fail and their past cases "
                 "in this regime. What is the honest base rate for the trade the desk leans to?"},
        {"id": "mirror", "name": "Mirror", "focus": "the desk's own record", "tools": ["desk_record", "coin_profile"],
         "task": "Call desk_record and coin_profile. How did the desk's past calls on this coin and timeframe, and in total, "
                 "work out (target first, stop first, expired, average R), split by evidence score? Has the desk been "
                 "overconfident? Is the coin crowded?"},
        {"id": "fracture", "name": "Fracture", "focus": "pre-mortem: how this trade fails",
         "tools": ["research_facts", "derivatives_and_news", "search_knowledge"],
         "task": "Run a pre-mortem. Assume the leading idea lost money and write the two most likely ways it failed (a "
                 "fake-out at the level, a squeeze against crowded positioning, a news shock, the higher timeframe "
                 "reasserting, fees eating a tight stop, dilution). Use research_facts ('leverage', and 'shorts' if the "
                 "lean is short), derivatives_and_news and the knowledge base for what was measured about each."},
    ],
    "risk": [
        {"id": "anchor", "name": "Anchor", "focus": "stop and target geometry", "tools": ["key_levels", "search_knowledge"],
         "task": "Call key_levels for {side}. Compare the stop options: which is a real invalidation (beyond structure plus "
                 "a buffer, 1-4 ATR) and which is noise. Compare the targets by R. Recommend one stop and one target with "
                 "the reason; Sentinel computes the plan."},
        {"id": "ballast", "name": "Ballast", "focus": "leverage, liquidation, carrying costs and slippage",
         "tools": ["research_facts", "derivatives_and_news", "funding_across", "depth_slippage", "search_knowledge"],
         "task": "Call research_facts('leverage') and derivatives_and_news. What did the research measure about "
                 "liquidations by leverage? What does funding cost on the {side} side, and do the estimated liquidation "
                 "bands sit between the price and a sensible stop? Call depth_slippage for a position about 5x the trader's "
                 "margin: what does entering and exiting cost in the book? Give the highest leverage you would accept, "
                 "and why."},
    ],
    "head": [
        {"id": "bull", "name": "Bull Case", "focus": "the strongest honest case for LONG", "tools": [],
         "task": "Using only what the desk said above (no new numbers), write the strongest honest case for LONG in 3-4 "
                 "sentences: the independent evidence families that support it, the trigger that would confirm it, and "
                 "its weakest point."},
        {"id": "bear", "name": "Bear Case", "focus": "the strongest honest case against LONG", "tools": [],
         "task": "Using only what the desk said above (no new numbers), write the strongest honest case for SHORT (on spot: "
                 "for staying flat) in 3-4 sentences: the independent evidence families that support it, the trigger that "
                 "would confirm it, and its weakest point."},
    ],
}

GUARD = """You are {name}, the desk's {title} — {role} — on a professional crypto trading desk. The desk is analysing \
{symbol} on the {tf} chart for a trader on {venue_txt} who {money}.
{team}
Think like an experienced professional: research before conclusions, form a hypothesis and test it from several \
angles, prefer measured base rates over stories, admit uncertainty, and always say what would prove you wrong. \
Call the tools you need first — several when that gives a better answer. Use search_knowledge (when you have it) to \
check how a concept is defined or what the research measured before you rely on it.

The tools give you measurements, not verdicts: form your own read the way a human analyst would — look at the \
structure, the levels, momentum, positioning, fundamentals and news, weigh them, and decide what they mean.

Reason in this order: (1) the higher timeframe and the whole market's regime, (2) the evidence for and against, \
counted by independent family (structure, liquidity, momentum, positioning, fundamentals, news — three indicators of \
one family are one piece of evidence), (3) the base rate for this kind of setup, (4) what would prove you wrong.

Calibrate your confidence: 0.5 is a coin flip; above 0.65 needs the timeframes and positioning to agree; above 0.75 \
also needs a measured base rate behind it; never above 0.85. Mixed evidence is neutral, not a weak opinion.

The desk's playbook (docs/AGENT_PLAYBOOK.md): higher timeframes decide the side; regime decides the playbook; count \
independent evidence families, not indicators; a setup needs a real invalidation 1-4 ATR away and at least 2R after \
fees; size comes from the stop; leverage never changes a stop-sized risk, it only moves the liquidation; a \
liquidation at or inside the stop loses the whole margin; no target wins 90% of the time; WAIT is a position.

Hard rules:
1. Every number, price, percentage or probability you state must appear in a tool result in this conversation \
(or in a specialist's report). Never invent or extrapolate numbers. If a tool has no data, say so.
2. You cannot place orders. The plan's entry, stop, target, leverage, margin, quantity and risk come only from the \
trade_plan tool. Never promise returns.
3. Write like a colleague on the desk: first person, plain English, {length}, no headings or lists, numbers the \
way people say them (2.4% below, $3.20 margin, 5x) — never raw field names.
4. Finish with exactly one JSON object on its own line:
{finish}"""

TEAM = """
You lead a team of specialists. They have already researched their part and reported below; build on their work, \
resolve where they disagree, and check with your own tools anything decisive or in conflict before you rely on it."""

SUB_ROLE = "a specialist on {lead}'s team ({lead_title}): {focus}"

FINISH = ('{"text": "<your analysis for the trader>", "view": "bullish" | "bearish" | "neutral", '
          '"confidence": <0.0-1.0>, "key_points": ["<short fact>", "..."]}')
FINISH_HEAD = ('{"text": "<your decision and reasoning>", "decision": "LONG" | "SHORT" | "WAIT", '
               '"view": "bullish" | "bearish" | "neutral", "confidence": <0.0-1.0>, '
               '"conditions": ["<what must happen / hold>", "..."], "invalidation": "<the price or event that kills it>"}')


def _fatal(err: str) -> bool:
    e = err.lower()
    return any(k in e for k in FATAL)


def _short(err: str) -> str:
    import re

    msg = re.sub(r"^Error code: \d+ - ", "", str(err)).strip()
    return (msg.split(". ")[0].rstrip(".") + ".")[:160]


def _sym(s: str) -> str:
    s = s.upper().replace("/", "").strip()
    return s if s.endswith("USDT") else s + "USDT"


async def filters_for(symbol: str) -> fut.Filters | None:
    """Live Binance USD-M lot/tick/minimum for one symbol (public exchange info), cached an hour."""
    hit = _filters.get(symbol)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            got = await fut.fetch_filters(c, [symbol])
    except Exception as e:  # noqa: BLE001 - defaults still give a usable plan
        log.warning("filters_fetch_failed", symbol=symbol, error=str(e))
        return None
    f = got.get(symbol)
    if f:
        _filters[symbol] = (time.time(), f)
    return f


async def live_price(symbol: str, fallback: float) -> float:
    from app.portfolio.my_trades import marks

    try:
        return (await marks([symbol], {symbol: fallback})).get(symbol) or fallback
    except Exception:  # noqa: BLE001
        return fallback


# ------------------------------------------------------------------------------------------- compact views for agents

def _compact_walk(walk: dict, snap: dict) -> dict:
    ind = snap.get("indicators") or {}
    keep = ("close", "ema20", "ema50", "ema200", "rsi14", "adx", "atr14", "macd_hist", "supertrend_dir", "bb_percent_b")
    return {
        "symbol": walk["symbol"], "timeframe": walk["timeframe"], "price": walk["price"], "regime": walk.get("regime"),
        "measurements": [{"topic": s["title"].split(". ", 1)[-1], "observation": s["observation"], "details": s["bullets"][:4]}
                         for s in walk["steps"] if s["key"] not in ("verdict", "plan", "scenarios")],
        "indicators": {k: (round(ind[k], 6) if isinstance(ind.get(k), float) else ind.get(k)) for k in keep if k in ind},
    }


def _deriv_news(snap: dict) -> dict:
    d = snap.get("derivatives") or {}
    keep = ("funding_rate", "funding_annualized_pct", "basis_bps", "long_short_ratio", "taker_buy_sell_ratio",
            "oi_change_1h_pct", "oi_change_4h_pct", "oi_change_24h_pct", "squeeze_flags", "funding_extreme")
    liq = snap.get("liquidations") or []
    longs = sum(float(x.get("notional", 0) or 0) for x in liq if str(x.get("side", "")).upper() in ("SELL", "LONG"))
    shorts = sum(float(x.get("notional", 0) or 0) for x in liq if str(x.get("side", "")).upper() in ("BUY", "SHORT"))
    ob = snap.get("orderbook") or {}
    return {
        "derivatives": {k: d.get(k) for k in keep if k in d} | {
            "oi_vs_price": (d.get("oi_price_regime") or {}).get("label"),
            "liquidation_bands_estimate": d.get("liquidation_bands_estimate", [])[:3]} if d.get("available") else
        {"available": False},
        "recent_liquidations": {"prints": len(liq), "longs_liquidated_usd": round(longs), "shorts_liquidated_usd": round(shorts)},
        "orderbook": {k: ob.get(k) for k in ("imbalance", "spread_bps", "bid_depth_usd", "ask_depth_usd") if k in ob},
        "fear_greed": snap.get("fear_greed"),
        "news": [{"title": n.get("title"), "impact": n.get("impact"), "sentiment": n.get("sentiment"),
                  "age": n.get("published_at") or n.get("ts")} for n in (snap.get("news") or [])[:6]],
    }


# ------------------------------------------------------------------------------------------- the session

def _view(s: dict) -> dict:
    return {k: s.get(k) for k in ("id", "status", "symbol", "tf", "side_requested", "budget", "margin", "mode", "venue", "brain",
                                  "model", "stages", "agents", "events", "decision", "plan", "validation", "chart_objects", "research", "picked",
                                  "error", "tool_calls", "seconds", "started_at", "llm", "disclaimer", "readiness", "force", "quota")}


def session_view(sid: str) -> dict | None:
    s = SESSIONS.get(sid)
    return _view(s) if s else None


def start(symbol: str | None, tf: str, side: str, budget: float, mode: str, venue: str, refresh: bool = False,
          margin: float | None = None, force: bool = False, quota: dict | None = None) -> dict:
    """Start (or reuse) a desk session. Unless `force`, the free readiness gate runs first and the AI agents only run
    when it finds a trade-quality setup (app/agents/readiness.py)."""
    side = side.upper() if side else "AUTO"
    key = (symbol, tf, side, round(budget, 2), mode, venue, round(margin, 2) if margin else None, force,
           (quota or {}).get("key"))
    sid = _BY_KEY.get(key)
    if sid and not refresh and sid in SESSIONS:
        s = SESSIONS[sid]
        if s["status"] == "running" or time.time() - s["t"] < CACHE_S:
            return _view(s)
    sid = uuid.uuid4().hex[:12]
    s = {"id": sid, "t": time.time(), "status": "running", "symbol": symbol, "tf": tf, "side_requested": side,
         "budget": margin or budget, "margin": margin, "mode": mode, "venue": venue, "brain": None, "model": None,
         "stages": [{"key": k, "title": t, "status": "pending"} for k, t in STAGES],
         "agents": [{"id": a["id"], "name": a["name"], "title": a["title"], "role": a["role"], "stage": a["stage"],
                     "color": a["color"], "status": "waiting", "text": None, "view": None, "confidence": None,
                     "key_points": [], "tools_used": [], "error": None, "kind": a["kind"],
                     "subs": [{"id": x["id"], "name": x["name"], "focus": x["focus"], "status": "waiting", "text": None,
                               "view": None, "confidence": None, "key_points": [], "tools_used": [], "error": None}
                              for x in (SUBS.get(a["id"], []) if get_settings().desk_subagents else [])]}
                    for a in AGENTS],
         "events": [],
         "decision": None, "plan": None, "validation": None, "chart_objects": [], "research": None, "picked": None, "error": None,
         "tool_calls": 0, "seconds": None, "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
         "llm": llm_status(), "disclaimer": DISCLAIMER, "readiness": None, "force": force, "quota": quota}
    SESSIONS[sid] = s
    _BY_KEY[key] = sid
    if len(SESSIONS) > MAX_SESSIONS:
        for old in sorted(SESSIONS, key=lambda k: SESSIONS[k]["t"])[: len(SESSIONS) - MAX_SESSIONS]:
            SESSIONS.pop(old, None)
    asyncio.get_running_loop().create_task(_run(s))
    return _view(s)


def ai_ready() -> bool:
    return provider() is not None


class Ctx:
    """Everything one session's tools read: loaded once, shared by all agents, so they see the same facts."""

    def __init__(self, s: dict):
        self.s = s
        self.symbol: str = s["symbol"]
        self.walks: dict[str, tuple[dict, dict]] = {}
        self.plans: dict[str, dict] = {}
        self.schools: list | None = None
        self.filters: fut.Filters | None = None
        self.price: float | None = None
        self.results: list[str] = []          # every tool result of the session, for the fact-checker
        self.evidence: dict | None = None
        self.validation: dict | None = None   # the pre-decision report the Head Trader reads
        self.pre_checks: list[dict] = []
        self.results_by: dict[str, list[str]] = {}  # each agent's tool results, for its revision
        self.last_plan: dict | None = None           # the last plan an agent computed
        self.fund: dict | None = None                # the coin's fundamentals, fetched once per session
        self.macro: dict | None = None
        self.depth: dict | None = None               # live order-book slippage for the plan's size
        self.sem = asyncio.Semaphore(max(1, get_settings().desk_llm_concurrency))  # AI calls in flight

    async def walk(self, tf: str) -> tuple[dict, dict]:
        from app.api.chat import _walk

        if tf not in self.walks:
            self.walks[tf] = await _walk(self.symbol, tf)
        return self.walks[tf]

    @property
    def snap(self) -> dict:
        return self.walks[self.s["tf"]][1]

    async def fundamentals(self) -> dict:
        from app.agents import fundamentals

        if self.fund is None:
            self.fund = await fundamentals.coin(self.symbol)
        return self.fund

    async def market_macro(self) -> dict:
        from app.agents import fundamentals

        if self.macro is None:
            self.macro = await fundamentals.macro()
        return self.macro


def build_tools(ctx: Ctx) -> dict[str, Tool]:
    s = ctx.s

    async def chart_analysis(timeframe: str | None = None) -> dict:
        tf = timeframe if timeframe in TFS else s["tf"]
        walk, snap = await ctx.walk(tf)
        return _compact_walk(walk, snap)

    def market_regime() -> dict:
        from app.quant import brain

        q = brain.peek() or {}
        out = {"quant_regime": q.get("regime"), "fear_greed": ctx.snap.get("fear_greed"),
               "this_chart_regime": ctx.snap.get("regime", {}).get("regime"),
               "regime_reasons": ctx.snap.get("regime", {}).get("reasons")}
        if not q:
            out["note"] = "the cross-coin quant regime is still computing"
        return out

    def derivatives_and_news() -> dict:
        return _deriv_news(ctx.snap)

    async def method_views() -> dict:
        from app.agents.room import record_line, track_record
        from app.api.chat import _schools

        if ctx.schools is None:
            try:
                ctx.schools = await _schools(ctx.symbol, s["tf"])
            except Exception as e:  # noqa: BLE001
                return {"error": f"method analysis unavailable: {e}"}
        rec = track_record(s["tf"])
        return {"timeframe": s["tf"], "methods": [
            {"method": m["title"], "verdict": m.get("verdict_word"), "observation": m["observation"],
             "details": m["bullets"][:3], "track_record": record_line(rec, m["key"], s["tf"])} for m in ctx.schools]}

    def coin_profile() -> dict:
        from app.maintenance.history import COINS
        from app.quant import brain

        q = brain.coin(ctx.symbol)
        rec = desk_tools.breakout_records().get(ctx.symbol)
        return {"symbol": ctx.symbol, "in_researched_daily_system": ctx.symbol.replace("USDT", "") in COINS,
                "quant_trend_rank": q, "daily_breakout_record_since_2020": rec,
                "note": None if q else "not in the quant brain's ~120-coin ranking (thin or new coin)"}

    async def strategy_evidence() -> dict:
        from app.engine import engine
        from app.quant.odds import setup_odds

        tf = s["tf"]
        gate = {k.split("|")[0]: {x: v.get(x) for x in ("status", "expectancy_r", "ci", "n")}
                for k, v in engine.research_gate.items() if k.endswith(f"|{tf}")}
        regime = (ctx.snap.get("regime") or {}).get("regime")
        setups = []
        for c in [c for c in ctx.snap.get("candidates", []) if "error" not in c][:4]:
            try:
                odds = await setup_odds(c["strategy"], c["direction"], tf, regime)
            except Exception:  # noqa: BLE001
                odds = None
            setups.append({"strategy": c["strategy"], "direction": c["direction"], "stage": c["stage"],
                           "passed_all_checks": (c.get("validation") or {}).get("passed"),
                           "failed_checks": [ch["name"] for ch in (c.get("validation") or {}).get("checks", [])
                                             if not ch["passed"]][:4],
                           "backtest": gate.get(c["strategy"]), "past_cases": odds})
        return {"timeframe": tf, "setups_on_this_chart": setups, "backtest_evidence_all_strategies": gate,
                "engine_decision": ctx.snap.get("decision")}

    def research_facts(topic: str) -> dict:
        return desk_tools.research_facts(topic)

    def search_knowledge(query: str, k: int = 3) -> dict:
        return knowledge.search(query, k)

    def key_levels(side: str) -> dict:
        side = side.upper()
        if side not in ("LONG", "SHORT"):
            return {"error": "side must be LONG or SHORT"}
        return tp.levels({**ctx.snap, "price": ctx.price or ctx.snap["price"]}, side)

    def trade_plan(side: str, stop: float, target: float | None = None, entry: float | None = None,
                   style: str | None = None) -> dict:
        side = side.upper()
        px = ctx.price or float(ctx.snap["price"])
        e = float(entry) if entry else px
        if abs(e / px - 1) > 0.15:
            return {"ok": False, "error": f"entry {e} is more than 15% from the price {px}; use the live price or a "
                                          f"nearby level"}
        fund = ((ctx.snap.get("derivatives") or {}).get("funding_annualized_pct"))
        if s.get("mode") not in tp.STYLES:  # the trader left the risk style to the desk: the planner chooses it
            s["mode_chosen"] = style if style in tp.STYLES else s.get("mode_chosen") or "conservative"
        p = tp.build(ctx.symbol, side, e, float(stop), float(target) if target else None, s["budget"], tp.mode_of(s),
                     s["venue"], ctx.filters, fund, margin=s.get("margin"))
        if "stop" in p:
            p["entry_type"] = "market at about the live price" if not entry else f"limit at {e:.6g}"
            p["planned_by"] = "agent"
            p["risk_style"] = tp.mode_of(s)
            p["risk_style_by"] = "the trader" if s.get("mode") in tp.STYLES else "Sentinel (Risk Officer)"
            ctx.plans[side] = p
            ctx.last_plan = p
        return p

    def desk_record() -> dict:
        from app.agents import calibration, journal

        return {"this_coin_and_timeframe": journal.stats(ctx.symbol, s["tf"]), "whole_desk": journal.stats(),
                "confidence_calibration": calibration.reliability(), "lead_skill": calibration.lead_skill()}

    def _fund_part(f: dict, key: str) -> dict:
        head = {k: f.get(k) for k in ("symbol", "name", "as_of") if f.get(k)}
        part = f.get(key)
        return {**head, key: part, "unavailable": f.get("unavailable", [])} if part else \
            {**head, "available": False, "unavailable": f.get("unavailable", []) or [f"no {key} data"]}

    async def tokenomics() -> dict:
        return _fund_part(await ctx.fundamentals(), "tokenomics")

    async def onchain_usage() -> dict:
        f = await ctx.fundamentals()
        out = _fund_part(f, "usage")
        if out.get("usage"):
            out["market_cap_usd"] = (f.get("tokenomics") or {}).get("market_cap_usd")
        return out

    async def market_standing() -> dict:
        return _fund_part(await ctx.fundamentals(), "standing")

    async def market_macro() -> dict:
        return {**await ctx.market_macro(), "fear_greed": ctx.snap.get("fear_greed")}

    async def order_flow(timeframe: str | None = None) -> dict:
        from app.agents import market_extras

        return await market_extras.order_flow(ctx.symbol, timeframe if timeframe in TFS else s["tf"])

    async def funding_across() -> dict:
        from app.agents import market_extras

        return await market_extras.funding_across(ctx.symbol)

    async def options_market() -> dict:
        from app.agents import market_extras

        return await market_extras.options_market()

    async def depth_slippage(notional_usd: float) -> dict:
        from app.agents import market_extras

        return await market_extras.depth_slippage(ctx.symbol, float(notional_usd))

    def chart_patterns() -> dict:
        """Patterns learned from every coin's history since 2019: analogs and the learned pattern for this chart."""
        from app.ml import patterns as pt

        r = pt.for_symbol(ctx.symbol)
        if not r.get("available") or not r.get("covered"):
            return r
        return {"as_of": r["as_of"], "charts_learned": r["charts_learned"],
                "how_to_use": "Compare each rate with base_rates (how often it happens for any chart). Analogs and "
                              "patterns are evidence of what usually followed similar charts, measured on years the "
                              "model never saw (accuracy); they are not certainties.",
                "analogs_50": {"rates": r["analog"], "share_up_after_7d": r["analog_up_share"],
                               "median_7d_move_pct": r["analog_median_fwd7_pct"],
                               "range_7d_pct_p10_p90": r["analog_p10_p90_fwd7_pct"],
                               "closest": [{k: e[k] for k in ("symbol", "date", "fwd7_pct")} for e in r["examples"]]},
                "learned_pattern": {k: r["pattern"][k] for k in ("name", "count", "rates", "median_fwd7_pct")},
                "base_rates": r["base_rates"], "outcomes": r["labels"], "accuracy": r["accuracy"]}

    def model_forecast() -> dict:
        """The platform's trained models for this coin, each with its measured out-of-sample accuracy."""
        from app.ml import forecast

        r = forecast.for_symbol(ctx.symbol)
        if not r.get("available") or not r.get("covered"):
            return r
        return {"as_of": r["as_of"], "plan_tested": r["plan"], "atr_pct": r["atr_pct"],
                "how_to_use": "Use only models with status 'accepted' as evidence; 'weak' ones are a small edge over a "
                              "coin flip; ignore 'rejected'. Compare a probability with its base_rate, not with 50%.",
                "models": [{k: m[k] for k in ("label", "what", "status", "value", "base_rate", "accuracy")}
                           for m in r["models"]]}

    def validation_report() -> dict:
        v = ctx.validation
        if not v:
            return {"note": "the validators have not run yet"}
        return {"research_quality": v["score"], "trade_evidence": v["evidence_score"], "summary": v["summary"],
                "critical": v["critical"],
                "not_passed": [{"check": c["name"], "status": c["status"], "detail": c["detail"]}
                               for c in v["checks"] if c["status"] != "pass"][:14]}

    async def tested_signal(side: str | None = None) -> dict:
        """The backtested daily signal plan for this coin (app/ml/signal_plan.py), the same as the Today's signal page."""
        from app.api.agent import _live
        from app.ml import signal_plan as sp
        from app.ml import unified as un

        sd = side.upper() if isinstance(side, str) and side.upper() in ("LONG", "SHORT") else None
        live = await _live(ctx.symbol)
        p = un.plan_for(ctx.symbol, sd, float(s.get("budget") or 100), live)
        if not p.get("covered"):
            p = sp.plan_for(ctx.symbol, sd, float(s.get("budget") or 100), live)
        if not p.get("available") or not p.get("covered"):
            return p

        def tested(t: dict) -> dict:
            h, b = t["holdout"], t["baseline_holdout"]
            return {"verdict": t["verdict"], "rule": f"filter {t['filter']}, confidence {t['confidence']}",
                    "unseen_2025_26": {k: h.get(k) for k in ("trades", "win_rate", "avg_r", "outcomes", "avg_days")},
                    "random_coin_same_days": {k: b.get(k) for k in ("win_rate", "avg_r")},
                    "beats_random_coin": t["beats_average_coin"]}

        sw, fa = p["plans"]["swing"], p["plans"]["fast"]
        lv = ("entry", "stop", "stop_pct", "tp1", "tp1_pct", "tp2", "tp2_pct", "days", "predicted_r")
        return {"how_to_use": "The unified engine's plan (daily candles, up to 14 days): one model per plan learned from "
                              "market features, indicators, human trading concepts (structure, liquidity, Wyckoff, VWAP, "
                              "price action) and learned chart patterns at once, tested walk-forward on 2025-26. "
                              "status 'trade' = the engine takes it today; 'watch' lists why not. 'why' = the model's own "
                              "contributions by concept group in R (positive helps this plan). R = result in units of "
                              "the risk.",
                "engine": p.get("engine", "fixed"), "plan": p.get("plan_id"),
                "why": [{"group": g["group"], "r": g["r"], "top": [f"{f['what']} = {f['value']} ({f['r']:+}R)"
                                                                   for f in g["features"][:2]]}
                        for g in (p.get("why") or [])[:6]],
                "as_of": p["as_of"], "side": p["side"], "status": p["status"], "gates": p["gates"],
                "market_trend": p["market"], "price": p["price"], "entry_type": p["entry_type"],
                "rank": f"{p['rank']} of {p['of']} {'liquid' if p['tier'] == 'core' else 'thinner'} coins for a {p['side'].lower()}",
                "swing_plan": {**{k: sw.get(k) for k in lv}, "max_safe_leverage": sw["leverage"]["safe_max"],
                               "tested": tested(sw["tested"])},
                "fast_plan": {**{k: fa.get(k) for k in lv}, "max_safe_leverage": fa["leverage"]["safe_max"],
                              "tested": tested(fa["tested"])},
                "evidence": [{k: e[k] for k in ("text", "value", "usual", "supports", "tested")} for e in p.get("evidence", [])],
                "risks": p["risks"], "notes": p["notes"], "simulation": _sim_brief(p)}

    def _sim_brief(p: dict) -> dict | None:
        """The engine's own past picks like this one (app/ml/simulate.py), so the agents weigh the same evidence the
        trader sees on the signal page."""
        if p.get("engine") != "unified":
            return None
        from app.ml import simulate as sm

        r = sm.simulate(p["symbol"], p["plan_id"], p["plans"]["swing"]["leverage"]["stop_pct"], float(s.get("budget") or 100))
        if not r.get("available"):
            return None
        keep = lambda x: {k: x.get(k) for k in ("n", "win_rate", "avg_r", "unseen")}  # noqa: E731
        return {"how_to_use": "The engine's real past picks (out of sample). 'unseen' = 2025-26, never used to choose "
                              "anything. Small n means a weak sample.",
                "its_trades_this_side": keep(r["side_stats"]), "this_exact_plan": keep(r["plan_stats"]),
                "on_this_coin": keep(r["coin"]), "this_plan_any_day_no_model": keep(r["coin_plan"]),
                "next_20_trades_at_margin": [{k: m.get(k) for k in ("leverage", "median", "p5", "p95", "chance_down",
                                                                     "liquidated_before_stop")} for m in r["monte_carlo"]]}

    async def signal_test_record() -> dict:
        """The live forward test: every platform signal frozen when given and graded on the live candles after it."""
        from app.ml import signal_test

        r = await signal_test.report()
        keep = ("finished", "open", "wins", "losses", "win_rate", "win_range_90", "avg_r", "sum_r", "expected_avg_r")
        g = lambda k: {pl: {x: r["stats"][k][pl].get(x) for x in keep} for pl in ("swing", "fast")}  # noqa: E731
        mine = [{k: x[k] for k in ("as_of", "side", "status", "entry", "stop", "tp1", "tp2", "state", "outcome", "r")}
                for x in r["signals"] if x["symbol"] == ctx.symbol][:5]
        return {"how_to_use": "Forward test on live prices (not a backtest). Each daily top LONG and top SHORT is saved "
                              "with frozen levels and graded with the backtest's rules. Under 10 finished signals it says "
                              "little: read win_range_90, the range the true win rate could be in.",
                "verdict": r["verdict"]["text"], "all_platform_signals": g("platform"),
                "said_trade": g("trade"), "said_watch": g("watch"), "longs": g("long"), "shorts": g("short"),
                "this_coin": mine}

    num = {"type": "number"}
    side_p = {"type": "string", "enum": ["LONG", "SHORT"]}
    return {t.name: t for t in [
        Tool("chart_analysis", "Full top-down analysis of this coin on one timeframe: higher-timeframe context, regime, "
             "market structure (BOS/CHoCH), support/resistance, liquidity, order blocks, fair value gaps, premium/"
             "discount, Elliott, momentum, derivatives, news, scenarios, the engine's setups and its decision.",
             chart_analysis, {"type": "object", "properties": {"timeframe": {"type": "string", "enum": list(TFS)}}}),
        Tool("market_regime", "The whole market's state (quant brain over ~120 coins: BTC trend, volatility, breadth) "
             "plus this chart's regime and the fear & greed index.", market_regime),
        Tool("derivatives_and_news", "Funding (8h and per year), basis, open interest change vs price, long/short and "
             "taker ratios, squeeze flags, estimated liquidation bands, recent liquidations, order book, news "
             "headlines with impact and sentiment, fear & greed.", derivatives_and_news),
        Tool("method_views", "Ten classic methods on this chart (Wyckoff, Dow, Gann, Market Profile, harmonics, ICT "
             "Power of 3, NeoWave, Hurst, Quasimodo, Bill Williams), each with its measured out-of-sample track "
             "record.", method_views),
        Tool("coin_profile", "This coin's trend rank among ~120 liquid coins (score, trend, probability up next week, "
             "crowding), its daily-breakout record since 2020, and whether the researched daily system trades it.",
             coin_profile),
        Tool("strategy_evidence", "Backtest evidence for every strategy on this timeframe (expectancy, confidence "
             "interval, suspended or not) and, for each setup on this chart, its checks and past cases.",
             strategy_evidence),
        Tool("research_facts", "Measured research results. topic: 'system' (the $100 daily account), 'leverage' "
             "(liquidations by leverage), 'win_rate' (win rate vs target), 'carry', 'shorts'.", research_facts,
             {"type": "object", "properties": {"topic": {"type": "string", "enum": ["system", "leverage", "win_rate",
                                                                                    "carry", "shorts"]}},
              "required": ["topic"]}),
        Tool("search_knowledge", "Search the desk's knowledge base: research docs with evidence grades, exact "
             "definitions of every concept (SMC, ICT, Elliott, Wyckoff, funding...), strategy specs and backtests, "
             "study findings, method primers.", search_knowledge,
             {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer"}},
              "required": ["query"]}),
        Tool("key_levels", "Measured stop and target options for a side: the last structural swing, a 2-ATR stop, the "
             "engine's setup stop, support/resistance and resting liquidity with their R multiples, and a default.",
             key_levels, {"type": "object", "properties": {"side": side_p}, "required": ["side"]}),
        Tool("trade_plan", "Compute the exact plan for a side, stop and target (entry = live price unless given): "
             "quantity, the lowest safe leverage, margin, risk in dollars, liquidation, reward/risk after fees, the "
             "Binance orders to type, warnings. These are the only numbers the plan may use.", trade_plan,
             {"type": "object", "properties": {"side": side_p, "stop": num, "target": num, "entry": num,
                                               "style": {"type": "string", "enum": list(tp.STYLES),
                                                         "description": "risk style when the trader left it to the desk"}},
              "required": ["side", "stop"]}),
        Tool("desk_record", "The desk's own graded record from its journal: how past calls (taken and waited) worked "
             "out — target first, stop first, expired, win rate, average R — for this coin and timeframe and for the "
             "whole desk, split by evidence score.", desk_record),
        Tool("validation_report", "The validators' report before the decision: research quality score and grade, critical "
             "failures (which force WAIT) and every check that did not pass, with its measured detail.", validation_report),
        Tool("tokenomics", "The coin's supply and dilution (CoinGecko): market cap, fully diluted value, FDV / market cap, "
             "the locked or unissued value still to come, circulating, total and max supply, circulating share of the "
             "max, and whether supply is capped.", tokenomics),
        Tool("onchain_usage", "What the chain or protocol actually does (DefiLlama): TVL and its 7-day change, fees and "
             "revenue (30 days, annualized, last year), the fee trend, and market cap against TVL, annual fees and annual "
             "revenue. Empty for coins with no usage metric (meme, payments).", onchain_usage),
        Tool("market_standing", "The coin's place in the market (CoinGecko): rank, 24h volume and volume / market cap, "
             "all-time high, distance and days from it, 7d/30d/1y change, performance against BTC in percentage points, "
             "categories, launch date, community votes.", market_standing),
        Tool("order_flow", "Who is pressing (Binance futures, spot if no perp): cumulative volume delta (taker buys minus "
             "sells) over the last 24 and 96 candles in coins and as % of volume, the share of taker buying, the price "
             "change over the same windows and whether price and CVD diverge.", order_flow,
             {"type": "object", "properties": {"timeframe": {"type": "string", "enum": list(TFS)}}}),
        Tool("funding_across", "Funding (8h and per year) and open interest on Binance, Bybit and OKX for this coin: the "
             "OI-weighted average, the spread between venues and the most crowded venue and side.", funding_across),
        Tool("options_market", "BTC and ETH options (Deribit), the market's fear gauge for every coin: DVOL implied "
             "volatility and its 24h change, the put/call open-interest ratio and the near vs one-month ATM implied-vol "
             "term structure.", options_market),
        Tool("depth_slippage", "Live order book: the expected slippage in basis points of a market order of the given "
             "dollar size, buying and selling, the spread and the depth within 1%.", depth_slippage,
             {"type": "object", "properties": {"notional_usd": {"type": "number"}}, "required": ["notional_usd"]}),
        Tool("market_macro", "The whole crypto market: total market cap and its 24h change, BTC / ETH / stablecoin "
             "dominance, stablecoin supply and its 7d/30d change (dry powder), breadth of the top 100 (share up and share "
             "beating BTC over 7d and 30d), BTC's 7d/30d/1y change, fear & greed.", market_macro),
        Tool("chart_patterns", "Chart patterns learned from every coin's daily chart since 2019 (~580,000 charts): the 50 "
             "most similar past charts across all coins and what happened after them (up after 7 days, a long or a short "
             "hitting 2R first, a 2-ATR drop, the 7-day move range), the closest five with dates, and the learned "
             "pattern this chart belongs to with its historical outcomes — each with its accuracy on unseen years.",
             chart_patterns),
        Tool("model_forecast", "The platform's trained LightGBM models for this coin (591 coins since 2019, walk-forward "
             "tested on years they never saw): chance it is up in 7 days, chance it beats the median coin, the expected "
             "7-day range, the chance of a 2-ATR drop, and the chance a long or a short (stop 1.5 ATR, target 3 ATR) "
             "hits its target first — each with its measured accuracy and status (accepted / weak / rejected).",
             model_forecast),
        Tool("tested_signal", "The unified engine's plan for this coin (the Today's signal page): every method in one "
             "model — market features, indicators, trading concepts (structure, liquidity, Wyckoff, VWAP, price action) "
             "and learned chart patterns. Returns the side and plan it would trade (stop in ATR, target in R), status "
             "TRADE or WATCH with why, the levels, the model's reasons by concept group, and how its picks did on "
             "2025-26 against a random coin with the same plan.",
             tested_signal, {"type": "object", "properties": {"side": side_p}}),
        Tool("signal_test_record", "The live forward test of the platform's signals: each daily top LONG and SHORT frozen "
             "when given and graded on live candles — finished count, share in profit with its 90% range, average R, "
             "TRADE vs WATCH, longs vs shorts, and this coin's tested signals.", signal_test_record),
    ]}


def _lean(s: dict, ctx: Ctx) -> str:
    """The side to plan: the requested one, else the analysts' weighted view, else the chart's read."""
    if s["venue"] == "spot":
        return "LONG"
    if s["side_requested"] in ("LONG", "SHORT"):
        return s["side_requested"]
    score = 0.0
    from app.agents import calibration

    skill = calibration.lead_skill()["weights"]  # each lead's weight from its graded record (1.0 until it has one)
    weight = {k: v * skill.get(k, 1.0) for k, v in {
        "technician": 1.0, "flow": 1.0, "researcher": 1.0,
        "fundamental": 0.5 if s["tf"] in ("4h", "1d") else 0.0}.items()}  # fundamentals barely move a 15m/1h trade
    for a in s["agents"]:
        if weight.get(a["id"]) and a["view"] in ("bullish", "bearish"):
            score += (1 if a["view"] == "bullish" else -1) * float(a["confidence"] or 0.5) * weight[a["id"]]
    if score == 0:
        d = ctx.walks[s["tf"]][0]["synthesis"]["direction"]
        score = 1 if d == "bullish" else -1 if d == "bearish" else 0
    return "SHORT" if score < 0 else "LONG"


def _levels_near(snap: dict, price: float, n: int = 2) -> list[dict]:
    lv = (snap.get("price_action") or {}).get("levels") or []
    above = sorted([x for x in lv if x["price"] > price], key=lambda x: x["price"])[:n]
    below = sorted([x for x in lv if x["price"] < price], key=lambda x: -x["price"])[:n]
    return [{"price": x["price"], "type": x["type"], "touches": x.get("touches"),
             "time": int((x.get("last_time") or 0) // 1000) or None} for x in above + below]


# ------------------------------------------------------------------------------------------- live events (cursors)

def emit(s: dict, agent: str, kind: str, **kw) -> None:
    """One thing an agent is doing right now. The page moves that agent's cursor from these events: `click` an element,
    `el` hover an element, `points` on the chart (price, time), `draw` the plan; `label` is what it says it is doing."""
    if len(s["events"]) >= 900:
        return
    s["events"].append({"i": len(s["events"]), "t": int((time.time() - s.get("t0", s["t"])) * 1000), "agent": agent,
                        "kind": kind, **kw})


_emit = emit


def _last_t(ctx: Ctx) -> int:
    return int((ctx.snap.get("as_of") or time.time() * 1000) // 1000)


def _pts(ctx: Ctx, prices: list, t: int | None = None) -> list[dict]:
    return [{"price": float(p), "time": int(t or _last_t(ctx))} for p in prices if p]


def _swings(ctx: Ctx) -> list[dict]:
    sw = ((ctx.snap.get("smc") or {}).get("swings") or [])[-4:]
    return [{"price": float(x["price"]), "time": int(x["time"] // 1000)} for x in sw if x.get("time")]


LABEL = {"chart_patterns": "Finding similar past charts", "model_forecast": "Reading the trained models' forecast",
         "tested_signal": "Reading the backtested signal plan", "signal_test_record": "Checking the live signal test", "market_regime": "Checking the whole market's regime", "coin_profile": "Reading the coin's rank and record",
         "strategy_evidence": "Pulling the backtest evidence", "desk_record": "Checking the desk's own track record",
         "validation_report": "Reading the validators' report", "tokenomics": "Reading supply and dilution",
         "onchain_usage": "Reading TVL, fees and revenue", "market_standing": "Reading rank and strength against BTC",
         "market_macro": "Reading BTC dominance, stablecoin supply and breadth",
         "order_flow": "Reading who is pressing: cumulative volume delta", "funding_across": "Comparing funding on 3 exchanges",
         "options_market": "Reading BTC/ETH options: implied vol and put/call", "depth_slippage": "Measuring slippage in the book"}


def tool_event(s: dict, ctx: Ctx, agent: str, phase: str, name: str, args: dict, out, sub: str | None = None) -> None:
    """Turn one real tool call into a cursor action. A specialist's calls move its lead's cursor, labelled with its name."""
    def emit(s: dict, agent: str, kind: str, **kw) -> None:  # noqa: A001 - the module emit, with the specialist's name
        if sub and kw.get("label"):
            kw["label"] = f"{sub}: {kw['label']}"
        _emit(s, agent, kind, **kw)

    if phase == "start":
        if name == "chart_analysis":
            tf = args.get("timeframe") if args.get("timeframe") in TFS else s["tf"]
            emit(s, agent, "move", click=f"tf:{tf}", label=f"Opening the {tf} chart")
        elif name == "validation_report":
            emit(s, agent, "move", el="panel:validators", label=LABEL[name])
        elif name == "search_knowledge":
            emit(s, agent, "move", el=f"card:{agent}", label=f'Searching the knowledge base: "{args.get("query", "")}"')
        elif name == "research_facts":
            emit(s, agent, "move", el=f"card:{agent}", label=f"Reading the research on {args.get('topic', '')}")
        elif name in LABEL:
            emit(s, agent, "move", el=f"card:{agent}", label=LABEL[name])
        return
    if not isinstance(out, dict) or out.get("error"):
        return
    if name == "chart_analysis":
        tf = args.get("timeframe") if args.get("timeframe") in TFS else s["tf"]
        lv = _levels_near(ctx.walks[tf][1], ctx.price, 1) if tf in ctx.walks else []
        emit(s, agent, "move", points=_pts(ctx, [x["price"] for x in lv]) or _pts(ctx, [ctx.price]),
             label=f"Reading the {tf} chart" + "".join(f" · {x['type']} {x['price']:.6g}" for x in lv))
    elif name == "key_levels":
        stops = [x["price"] for x in out.get("stop_options", [])]
        tgts = [x["price"] for x in out.get("target_options", [])[:2]]
        emit(s, agent, "move", points=_pts(ctx, stops + tgts),
             label=f"Measuring {args.get('side', '').lower()} stops " + ", ".join(f"{p:.6g}" for p in stops))
    elif name == "trade_plan" and "stop" in out:
        emit(s, agent, "draw", draw={"side": out["side"], "entry": out["entry"], "stop": out["stop"], "target": out["target"],
                                     "time": _last_t(ctx)},
             label=f"Plan: SL {out['stop']:.6g} (−${out.get('risk_usd', 0):.2f}) · TP {out['target']:.6g} "
                   f"(+${out.get('reward_usd', 0):.2f}) · {out.get('leverage')}x")
    elif name == "derivatives_and_news":
        f = (out.get("derivatives") or {}).get("funding_annualized_pct")
        emit(s, agent, "move", points=_pts(ctx, [ctx.price]),
             label="Positioning: " + (f"funding {f:+.1f}%/yr" if f is not None else "no futures data")
                   + f" · {len(out.get('news') or [])} headlines")
    elif name == "method_views":
        emit(s, agent, "move", points=_swings(ctx), label=f"Reviewing {len(out.get('methods') or [])} classic methods")


# ------------------------------------------------------------------------------------------- the run

def _ag(s: dict, aid: str) -> dict:
    return next(x for x in s["agents"] if x["id"] == aid)


def _stage(s: dict, key: str, status: str) -> None:
    for stg in s["stages"]:
        if stg["key"] == key:
            stg["status"] = status


def _stage_done(s: dict, key: str) -> None:
    if all(x["status"] in ("done", "skipped") for x in s["agents"] if x["stage"] == key):
        _stage(s, key, "done")


def _system(s: dict, ctx: Ctx, a: dict, head: bool = False, team: bool = False) -> str:
    return GUARD.format(name=a["name"], title=a.get("title", a["name"]), role=a["role"], symbol=ctx.symbol, tf=s["tf"],
                        venue_txt="Binance USD-M futures" if s["venue"] == "futures" else "Binance spot",
                        money=_money(s), finish=FINISH_HEAD if head else FINISH, team=TEAM if team else "",
                        length="4-8 sentences" if team else "3-7 sentences")


def _sub_system(s: dict, ctx: Ctx, lead: dict, sub: dict) -> str:
    return GUARD.format(name=sub["name"], title=f"{sub['focus']} specialist",
                        role=SUB_ROLE.format(lead=lead["name"], lead_title=lead["title"], focus=sub["focus"]),
                        symbol=ctx.symbol, tf=s["tf"],
                        venue_txt="Binance USD-M futures" if s["venue"] == "futures" else "Binance spot",
                        money=_money(s), finish=FINISH, team="", length="3-5 sentences, only your specialty")


def _money(s: dict) -> str:
    if s.get("margin"):
        share = tp.MARGIN_RISK[tp.mode_of(s)]
        return (f"puts ${s['margin']:,.2f} margin on this trade in {tp.mode_of(s)} style: the desk chooses the leverage, size, "
                f"stop and target, and a stop-out may lose at most {share:.0%} of that margin "
                f"(${share * s['margin']:,.2f})")
    if s.get("mode") not in tp.STYLES:
        return (f"has a ${s['budget']:,.0f} trading budget and leaves the risk style and the size to the desk: the Risk "
                f"Officer chooses conservative (1% of the budget at risk), growth (2%) or aggressive (3%) from the evidence")
    return (f"has a ${s['budget']:,.0f} budget in {s['mode']} mode "
            f"({tp.RISK_MODES[s['mode']]['risk_pct']:g}% risk per trade)")


def _first(text: str, n: int = 170) -> str:
    t = " ".join(text.split())
    cut = t.find(". ", 40)
    return (t[: cut + 1] if 0 < cut < n else t[:n] + ("…" if len(t) > n else ""))


def _side_hint(s: dict) -> str:
    return (f"the {s['side_requested']} side the trader asked for" if s["side_requested"] in ("LONG", "SHORT")
            else "the side the team leans to (LONG on spot)")


async def _sub_turn(s: dict, ctx: Ctx, tools: dict, lead_id: str, spec: dict, said: list[str]) -> str | None:
    """One specialist's turn on its lead's team: a focused brief, narrow tools, a short report. Returns the fatal
    provider error, if any, so the lead is paused instead of faked."""
    lead = BY_ID[lead_id]
    x = next(v for v in _ag(s, lead_id)["subs"] if v["id"] == spec["id"])
    x["status"] = "working"
    emit(s, lead_id, "think", el=f"card:{lead_id}", label=f"{spec['name']}: {spec['focus']}")
    side = s["side_requested"] if s["side_requested"] in ("LONG", "SHORT") else ("LONG" if s["venue"] == "spot" else None)
    task = (spec["task"].replace("{htf}", f"the higher timeframe ({_HTF.get(s['tf'], s['tf'])})")
            .replace("{side}", side or _lean(s, ctx)))
    prev = ("\n\nWhat the desk said so far:\n" + "\n".join(said)) if said else ""
    res = None
    try:
        async with ctx.sem:
            res = await run_agent(_sub_system(s, ctx, lead, spec), f"Live price {ctx.price:.6g}. Your task: {task}{prev}",
                                  [tools[n] for n in spec["tools"]], max_steps=3 if spec["tools"] else 0,
                                  max_tokens=1400,
                                  on_tool=lambda ph, n, ar, o: tool_event(s, ctx, lead_id, ph, n, ar, o, spec["name"]),
                                  **({"model": get_settings().desk_sub_model} if get_settings().desk_sub_model else {}))
        if res is not None:
            note_success()
    except Exception as e:  # noqa: BLE001 - one specialist failing must not stop its lead
        log.warning("research_subagent_failed", agent=spec["id"], error=str(e))
        err = _short(str(e))
        x.update(status="skipped", error=err)
        if _fatal(str(e)):
            note_failure(str(e))
            return err
        return None
    got = [u.get("result", "") for u in (res.tools_used if res else [])]
    ctx.results += got
    ctx.results_by.setdefault(lead_id, []).extend(got)  # the lead's numbers may come from its team's tools
    data = (res.data if res else None) or {}
    text = (data.get("text") or (res.text if res else "") or "").strip()
    used = [{"name": u["name"], "args": u["args"], "ok": u["ok"]} for u in (res.tools_used if res else [])]
    s["tool_calls"] += len(used)
    if not text:
        x.update(status="skipped", tools_used=used, error="no answer")
        return None
    x.update(status="done", text=text, tools_used=used,
             view=data.get("view") if data.get("view") in ("bullish", "bearish", "neutral") else None,
             confidence=data.get("confidence") if isinstance(data.get("confidence"), (int, float)) else None,
             key_points=[str(k) for k in (data.get("key_points") or [])][:4])
    emit(s, lead_id, "say", el=f"card:{lead_id}", text=f"{spec['name']}: {_first(text)}")
    return None


async def _lead_turn(s: dict, ctx: Ctx, tools: dict, aid: str, said: list[str], fatal: str | None) -> str | None:
    """A lead's turn: its specialists research in parallel first, then the lead synthesises their reports."""
    ag = _ag(s, aid)
    if fatal or not ag.get("subs"):
        return await _ai_turn(s, ctx, tools, aid, said, fatal)
    _stage(s, BY_ID[aid]["stage"], "running")
    ag["status"] = "working"
    outs = await asyncio.gather(*(_sub_turn(s, ctx, tools, aid, spec, list(said)) for spec in SUBS[aid]))
    fatal = next((f for f in outs if f), None)
    team = [f"- {x['name']} ({x['focus']})"
            + (f", {x['view']}" + (f" {x['confidence']:.0%}" if isinstance(x.get("confidence"), (int, float)) else "")
               if x.get("view") else "") + f": {x['text']}"
            for x in ag["subs"] if x.get("text")]
    return await _ai_turn(s, ctx, tools, aid, said, fatal, team)


async def _ai_turn(s: dict, ctx: Ctx, tools: dict, aid: str, said: list[str], fatal: str | None,
                   team: list[str] | None = None) -> str | None:
    """One AI agent's turn. Returns the fatal provider error (if any) so later agents are paused, not faked."""
    a, ag = BY_ID[aid], _ag(s, aid)
    _stage(s, a["stage"], "running")
    if fatal:
        ag.update(status="skipped", error=f"AI paused — {fatal}")
        for x in ag.get("subs", []):
            if x["status"] in ("waiting", "working"):
                x.update(status="skipped", error=f"AI paused — {fatal}")
        _stage_done(s, a["stage"])
        return fatal
    ag["status"] = "working"
    emit(s, aid, "think", el=f"card:{aid}", label="Weighing the team's reports" if team else a["role"])
    prev = ("\n\nWhat the desk said before you (build on it, do not repeat it):\n" + "\n".join(said)) if said else ""
    rep = ("\n\nYour specialists reported:\n" + "\n".join(team)) if team else ""
    res, err = None, None
    try:
        async with ctx.sem:
            res = await run_agent(_system(s, ctx, a, aid == "head", bool(team)),
                                  f"Live price {ctx.price:.6g}. Your task: {a['task'].replace('{side_hint}', _side_hint(s))}"
                                  f"{rep}{prev}",
                                  [tools[n] for n in a["tools"]], max_steps=4 if team else 6, max_tokens=2500,
                                  on_tool=lambda ph, n, ar, o: tool_event(s, ctx, aid, ph, n, ar, o))
        if res is not None:
            note_success()
    except Exception as e:  # noqa: BLE001 - one agent failing must not kill the desk
        log.warning("research_agent_failed", agent=aid, error=str(e))
        err = _short(str(e))
        if _fatal(str(e)):
            fatal = err
            note_failure(str(e))
            emit(s, aid, "say", el=f"card:{aid}", text=f"Paused — the AI provider says: {err}")
    if res:
        got = [u.get("result", "") for u in res.tools_used]
        ctx.results += got
        ctx.results_by[aid] = ctx.results_by.get(aid, []) + got
    data = (res.data if res else None) or {}
    text = (data.get("text") or (res.text if res else "") or "").strip()
    used = [{"name": u["name"], "args": u["args"], "ok": u["ok"]} for u in (res.tools_used if res else [])]
    if text:
        ag.update(text=text, by="ai", status="done", tools_used=used,
                  view=data.get("view") if data.get("view") in ("bullish", "bearish", "neutral") else None,
                  confidence=data.get("confidence") if isinstance(data.get("confidence"), (int, float)) else None,
                  key_points=[str(x) for x in (data.get("key_points") or data.get("conditions") or [])][:5])
        said.append(f"- {a['name']} ({a['title']}): {text}")
        emit(s, aid, "say", el=f"card:{aid}", text=_first(text))
        if aid == "head":
            act = data.get("decision") if data.get("decision") in ("LONG", "SHORT", "WAIT") else "WAIT"
            n_votes = max(get_settings().desk_head_votes, (s.get("quota") or {}).get("head_votes") or 1)
            votes = await _head_votes(s, ctx, a, act, prev + rep, n_votes) if n_votes > 1 else None
            s["decision"] = {"action": votes["action"] if votes else act,
                             "confidence": ag["confidence"], "summary": text, "by": f"{a['name']} · {a['title']}",
                             "conditions": [str(x) for x in (data.get("conditions") or [])][:4],
                             "invalidation": data.get("invalidation"), **({"votes": votes} if votes else {})}
    else:
        ag.update(status="skipped", tools_used=used, error=f"AI paused — {fatal}" if fatal else (err or "no answer"))
    s["tool_calls"] += len(used)
    _stage_done(s, a["stage"])
    return fatal


async def _head_votes(s: dict, ctx: Ctx, a: dict, first: str, context: str, n: int | None = None) -> dict:
    """Self-consistency: Apex decides again (no tools, the same conversation and validation report) until it has
    `desk_head_votes` independent calls; the majority wins and a split means WAIT. Steadier than one sample."""
    n = n or get_settings().desk_head_votes
    rep = json.dumps(build_tools(ctx)["validation_report"].fn(), default=str)[:4000]
    task = (f"Live price {ctx.price:.6g}. Decide LONG, SHORT or WAIT for this trade, independently, from the desk's "
            f"conversation and the validation report below. A critical failure means WAIT.\n\nValidation report: {rep}"
            f"{context}")

    async def one() -> str | None:
        try:
            async with ctx.sem:
                r = await run_agent(_system(s, ctx, a, True), task, [], max_steps=0, max_tokens=700)
            d = (r.data if r else None) or {}
            return d.get("decision") if d.get("decision") in ("LONG", "SHORT", "WAIT") else None
        except Exception as e:  # noqa: BLE001 - a failed vote is simply not counted
            log.warning("head_vote_failed", error=str(e))
            return None

    got = [first, *[v for v in await asyncio.gather(*(one() for _ in range(n - 1))) if v]]
    top = max(set(got), key=got.count)
    action = top if got.count(top) > len(got) / 2 else "WAIT"
    return {"votes": got, "action": action, "note": f"{got.count(top)} of {len(got)} votes for {top}"
                                                    + ("" if action == top else " — no majority, so WAIT")}


async def _rebuttal(s: dict, ctx: Ctx, said: list[str], fatal: str | None) -> str | None:
    """The leading analyst answers the Red Team: concede what is right, defend what the data supports, update."""
    sk = _ag(s, "skeptic")
    if fatal or sk.get("by") != "ai" or not sk.get("text"):
        return fatal
    cands = [_ag(s, i) for i in ANALYSTS if _ag(s, i).get("by") == "ai"
             and _ag(s, i).get("view") in ("bullish", "bearish")]
    if not cands:
        return fatal
    lead = max(cands, key=lambda x: x.get("confidence") or 0)
    emit(s, lead["id"], "think", el=f"card:{lead['id']}", label=f"Replying to {sk['name']}")
    task = (f"{sk['name']}, the Red Team lead, attacked the desk's leading view: \"{sk['text']}\"\n\nYour earlier analysis: "
            f"{lead['text']}\n\n"
            "Reply in 2-4 sentences: concede what is right, defend only what your tool results support, and give your "
            "updated view and confidence. You have no tools now; use only numbers already in this conversation.")
    try:
        async with ctx.sem:
            res = await run_agent(_system(s, ctx, BY_ID[lead["id"]]), task, [], max_steps=0, max_tokens=900)
    except Exception as e:  # noqa: BLE001
        if _fatal(str(e)):
            note_failure(str(e))
            return _short(str(e))
        return fatal
    data = (res.data if res else None) or {}
    text = (data.get("text") or (res.text if res else "") or "").strip()
    if text:
        lead["rebuttal"] = text
        if data.get("view") in ("bullish", "bearish", "neutral"):
            lead["view"] = data["view"]
        if isinstance(data.get("confidence"), (int, float)):
            lead["confidence"] = data["confidence"]
        said.append(f"- {lead['name']} (reply to {sk['name']}): {text}")
        emit(s, lead["id"], "say", el=f"card:{lead['id']}", text=_first(text))
    return fatal


def _skip_rest(s: dict, why: str) -> None:
    for a in s["agents"]:
        if a["status"] in ("waiting", "working"):
            a.update(status="skipped", error=why)
        for x in a.get("subs", []):
            if x["status"] in ("waiting", "working"):
                x.update(status="skipped", error=why)
    for stg in s["stages"]:
        if stg["status"] != "done":
            stg["status"] = "skipped"


NO_KEY = ("No AI key is set, so the agents cannot analyse. Add AIT_OPENAI_API_KEY or AIT_ANTHROPIC_API_KEY to "
          "backend/.env and restart the backend.")


async def _run(s: dict) -> None:
    from app.agents import journal

    t0 = time.time()
    s["t0"] = t0
    from app.agents import costs

    costs.FEATURE.set("desk")   # this task's AI calls are counted as the desk, per run
    costs.RUN.set(s["id"])
    try:
        s["symbol"] = _sym(s["symbol"])
        if get_settings().desk_ready_gate and not s.get("force"):
            # the free check first: the AI only runs when there is a trade-quality setup to study
            try:
                r = await readiness.check(s["symbol"], s["tf"], s["side_requested"] if s["side_requested"] in
                                          ("LONG", "SHORT") else None, s["venue"])
            except Exception as e:  # noqa: BLE001 - without the gate the desk simply runs
                log.warning("readiness_failed", symbol=s["symbol"], error=str(e))
                r = None
            s["readiness"] = r
            if r and r["verdict"] != "ready":
                _skip_rest(s, "not run: no trade-quality setup yet — the AI cost was saved")
                s.update(status="not_ready", error=None)
                return
        prov = provider()
        if prov is None:
            _skip_rest(s, "AI unavailable")
            s.update(status="unavailable", error=NO_KEY)
            return
        q = s.get("quota")
        if q and q.get("enforced"):  # the plan's daily AI analyses: counted only now that the agents really run
            if q["left"] <= 0:
                _skip_rest(s, "not run: today's AI analyses are used up")
                s.update(status="quota", error=f"Your {q['plan']} plan includes {q['limit']} AI analyses a day and they "
                                               "are used up. The readiness check is still free; more analyses tomorrow "
                                               "or on a bigger plan.")
                return
            from app import plans

            await plans.consume(q["key"])
        ctx = Ctx(s)
        walk, snap = await ctx.walk(s["tf"])
        ctx.filters = await filters_for(ctx.symbol)  # futures lot/tick also stand in for spot
        ctx.price = await live_price(ctx.symbol, float(snap["price"]))
        s["research"] = {"price": ctx.price, "chart_close": snap["price"], "as_of": snap.get("as_of"),
                         "regime": walk.get("regime"), "knowledge_sections": knowledge.index()["n"]}
        tools = build_tools(ctx)
        st = get_settings()
        s["model"] = st.openai_model if prov == "openai" else st.llm_model
        s["brain"] = "ai"
        said: list[str] = []
        fatal = await _lead_turn(s, ctx, tools, "researcher", said, None)
        if fatal:
            _skip_rest(s, "AI unavailable")
            s.update(status="unavailable", error=f"The AI provider is unavailable ({fatal}) — nothing is shown in its "
                                                 "place. Renew the key or add credits, then press Analyse again.")
            return
        # the three analysts (technical, flow, fundamental) work in parallel on the same brief, each with its team
        outs = await asyncio.gather(*(_lead_turn(s, ctx, tools, aid, list(said), fatal) for aid in ANALYSTS))
        for aid in ANALYSTS:
            if _ag(s, aid).get("by") == "ai":
                said.append(f"- {BY_ID[aid]['name']} ({BY_ID[aid]['title']}): {_ag(s, aid)['text']}")
        fatal = next((f for f in outs if f), fatal)
        fatal = await _lead_turn(s, ctx, tools, "skeptic", said, fatal)
        fatal = await _rebuttal(s, ctx, said, fatal)
        fatal = await _lead_turn(s, ctx, tools, "risk", said, fatal)
        await _validate_pre(s, ctx, tools, said, fatal)
        fatal = await _lead_turn(s, ctx, tools, "head", said, fatal)
        if fatal:
            s["error"] = (f"The AI stopped during the analysis ({fatal}). The agents that finished are shown; there is "
                          "no decision.")
        _finish(s, ctx)
        if s["decision"]:
            try:
                journal.record(s)
            except Exception as e:  # noqa: BLE001 - the journal must never break a session
                log.warning("journal_record_failed", error=str(e))
            try:  # the agents' verdict, kept apart from the machine's, joined in the signal test
                from app.ml import signal_test

                await signal_test.save_agent_verdict(s)
            except Exception as e:  # noqa: BLE001
                log.warning("agent_verdict_save_failed", error=str(e))
        s["status"] = "done"
    except Exception as e:  # noqa: BLE001
        log.warning("research_desk_failed", error=str(e), symbol=s.get("symbol"))
        s.update(status="error", error=str(getattr(e, "detail", None) or e)[:300])
    finally:
        s["seconds"] = round(time.time() - t0, 1)
        s["llm"] = llm_status()
        log.info("research_desk_done", symbol=s.get("symbol"), status=s["status"], brain=s.get("brain"),
                 tool_calls=s["tool_calls"], seconds=s["seconds"])


def _pre_report(s: dict, ctx: Ctx, side: str, plan: dict, filled: list[dict]) -> dict:
    from app.agents import validators as V

    facts, table = V.fact_checks(s, V.session_corpus(s, ctx, ctx.results))
    return V.report(ctx.pre_checks + V.trade_checks(s, ctx, plan, side) + V.evidence(s, ctx, side) + facts, filled, table)


async def _validate_pre(s: dict, ctx: Ctx, tools: dict, said: list[str], fatal: str | None) -> None:
    """The Validator's turn: data quality, research coverage (filling gaps), the plan for the leaning side, the evidence
    and a fact-check of every agent so far. AI agents with unverified numbers revise their text once. The Head Trader
    reads the resulting report before deciding."""
    from app.agents import validators as V

    ag = _ag(s, "validator")
    _stage(s, "validate", "running")
    ag["status"] = "working"
    emit(s, "validator", "think", el="card:validator", label="Checking data, coverage, numbers and the plan")
    side = _lean(s, ctx)
    plan = ctx.plans.get(side) or _measured_plan(ctx, tools, side)
    dq = V.data_quality(s, ctx)
    cov, filled = await V.research_coverage(s, ctx, tools, s["agents"])
    for f in filled:
        if f["name"] == "chart_analysis":
            emit(s, "validator", "move", click=f"tf:{f['args'].get('timeframe')}",
                 label=f"Gap: nobody checked the {f['args'].get('timeframe')} chart — checking it")
        elif f["name"] == "method_views":
            emit(s, "validator", "move", points=_swings(ctx), label="Gap: the ten methods — reviewing them")
        else:
            emit(s, "validator", "move", el="card:validator", label="Gap: backtest evidence — pulling it")
    ctx.pre_checks = dq + cov
    if plan and plan.get("notional_usd"):
        from app.agents import market_extras

        try:
            ctx.depth = await market_extras.depth_slippage(ctx.symbol, float(plan["notional_usd"]))
        except Exception as e:  # noqa: BLE001 - the check is skipped without the book
            log.warning("depth_failed", error=str(e))
    rep = _pre_report(s, ctx, side, plan, filled)
    # revision loop: an AI agent whose numbers do not trace gets one chance to fix them from its own tool results
    revised = []
    if not fatal:
        cp = V.session_corpus(s, ctx, ctx.results)
        for aid, fc in list(rep["fact_check"].items())[:8]:
            x = _ag(s, aid)
            if x.get("by") != "ai" or not fc["unverified"] or len(revised) >= 3:
                continue
            emit(s, "validator", "move", el=f"card:{aid}",
                 label=f"{x['name']}: {len(fc['unverified'])} number(s) not traced — sending back")
            task = (f"The Validator could not trace these numbers in your analysis to any tool result: "
                    f"{', '.join(fc['unverified'])}. Rewrite your analysis so every number comes from the tool results "
                    f"below — fix or remove the untraceable ones and keep everything else.\n\nTool results you saw:\n"
                    + "\n".join(ctx.results_by.get(aid, []))[:6000] + f"\n\nYour analysis:\n{x['text']}")
            try:
                async with ctx.sem:
                    res = await run_agent(_system(s, ctx, BY_ID[aid], aid == "head"), task, [], max_steps=0,
                                          max_tokens=1500)
            except Exception as e:  # noqa: BLE001
                if _fatal(str(e)):
                    break
                continue
            data = (res.data if res else None) or {}
            new = (data.get("text") or (res.text if res else "") or "").strip()
            if not new:
                continue
            nf = V.fact_check(new, cp)
            if nf["checked"] == 0 or nf["traced"] / nf["checked"] >= fc["traced"] / max(fc["checked"], 1):
                x["text"] = new
                x["revised"] = (f"revised after the fact-check: {len(fc['unverified'])} unverified number(s) "
                                f"({', '.join(fc['unverified'][:4])}) fixed or removed")
                revised.append(x["name"])
                emit(s, aid, "say", el=f"card:{aid}", text="Revised: " + _first(new))
        if revised:
            rep = _pre_report(s, ctx, side, plan, filled)
    ctx.validation = rep
    bad = [c for c in rep["checks"] if c["status"] != "pass"]
    lines = "; ".join(f"{c['name']} ({c['status']}: {c['detail']})" for c in bad[:5])
    text = (f"{rep['summary']} Checked the {side.lower()} plan. "
            + (f"Sent back for revision: {', '.join(revised)}. " if revised else "")
            + (f"Not passed: {lines}." if lines else "Every check passed."))
    ag.update(text=text, view=None, confidence=None, key_points=[], by="validators", status="done",
              tools_used=[{k: f[k] for k in ("name", "args", "ok")} for f in filled])
    emit(s, "validator", "move", el="panel:validators",
         label=f"Research {rep['score']}/100 ({rep['grade']}) · evidence {rep['evidence_score']}/100 "
               f"({rep['evidence_grade']}) · gate {rep['gate']}")
    s["tool_calls"] += len(filled)
    said.append(f"- Validator: {text}")
    _stage(s, "validate", "done")


def _measured_plan(ctx: Ctx, tools: dict, side: str) -> dict:
    """Numbers for a side no agent planned (used only to validate): the measured default stop and target."""
    lv = tp.levels({**ctx.snap, "price": ctx.price}, side)
    p = tools["trade_plan"].fn(side, lv["default"]["stop"], lv["default"]["target"])
    p["planned_by"] = "measured levels"
    ctx.plans.pop(side, None) if p.get("planned_by") != "agent" else None
    return p


def _finish(s: dict, ctx: Ctx) -> None:
    """The Head Trader's decision with the plan for its side, the final validation and the gate, and the chart. With
    no decision (the AI stopped) the agents' work so far is kept and nothing is decided for them."""
    from app.agents import validators as V

    d = s["decision"]
    if not d:
        side = _lean(s, ctx)
        plan = ctx.plans.get(side)
        s["plan"], s["validation"] = plan, ctx.validation
        s["chart_objects"] = chart_objects(plan, side, _last_t(ctx), conditional=True) if plan else []
        return
    side = d["action"] if d["action"] in ("LONG", "SHORT") else _lean(s, ctx)
    plan = ctx.plans.get(side) or _measured_plan(ctx, build_tools(ctx), side)
    pre = ctx.validation or {"checks": [], "score": 100}
    facts, table = V.fact_checks(s, V.session_corpus(s, ctx, ctx.results))
    by_agent = plan.get("planned_by") == "agent"
    own = [V.check("Trade checks", "Plan chosen by the Risk Officer", "pass" if by_agent else "warn",
                   "Sentinel, the Risk Officer, chose this stop and target" if by_agent else
                   f"no agent planned the {side.lower()} side; the stop and target are the measured defaults")]
    checks = (ctx.pre_checks + own + V.trade_checks(s, ctx, plan, side) + V.evidence(s, ctx, side) + facts
              + V.consistency(s, d, pre, plan))
    s["validation"] = V.report(checks, (pre or {}).get("filled"), table)
    guard = None
    if d["action"] in ("LONG", "SHORT"):
        if s["venue"] == "spot" and d["action"] == "SHORT":
            guard = "Spot cannot short."
        elif s["validation"]["critical"]:
            guard = "validators failed: " + ", ".join(s["validation"]["critical"])
    if guard:
        d = {**d, "action": "WAIT", "guard": f"Guard changed {side} to WAIT: {guard}"}
    d["plan_side"] = side
    d["plan_is"] = "the trade" if d["action"] in ("LONG", "SHORT") else "conditional: only if the trigger prints"
    if isinstance(d.get("confidence"), (int, float)):
        from app.agents import calibration

        adj = calibration.adjust(d["confidence"])  # the raw confidence stays as recorded; this is what the record says
        d["confidence_calibrated"], d["calibration"] = adj["calibrated"], adj["basis"]
    s["decision"], s["plan"] = d, plan
    s["chart_objects"] = chart_objects(plan, side, _last_t(ctx), conditional=d["action"] == "WAIT")
    emit(s, "head", "move", el="panel:decision",
         label=f"Decision: {d['action']}" + (f" ({d['guard']})" if d.get("guard") else ""))


def chart_objects(plan: dict, side: str, t: int, conditional: bool = False) -> list[dict]:
    """The plan on the chart with its numbers: entry, stop with the dollar loss, target with the dollar profit, the
    liquidation of the safe plan and of 20x."""
    if not plan or "stop" not in plan:
        return []
    lev = plan.get("leverage", 1)
    tag = " (if triggered)" if conditional else ""
    objs = [
        {"id": "desk_plan", "kind": "position", "origin": "ai", "concept": "plan", "time1": t, "direction": side,
         "entry": plan["entry"], "stop": plan["stop"], "target": plan["target"], "label": f"{side} plan{tag}"},
        {"id": "desk_entry", "kind": "hline", "origin": "ai", "concept": "plan", "price1": plan["entry"], "time1": t,
         "color": "#3a3a3c", "style": "solid",
         "label": f"ENTRY {plan['entry']:.6g} · {side} {lev}x · margin ${plan.get('margin_usd') or 0:.2f}"},
        {"id": "desk_sl", "kind": "hline", "origin": "ai", "concept": "plan", "price1": plan["stop"], "time1": t,
         "color": "#ef4444", "style": "dashed",
         "label": f"SL {plan['stop']:.6g} ({plan.get('stop_pct')}%) · loss -${plan.get('risk_usd') or 0:.2f}"},
        {"id": "desk_tp", "kind": "hline", "origin": "ai", "concept": "plan", "price1": plan["target"], "time1": t,
         "color": "#22c55e", "style": "dashed",
         "label": f"TP {plan['target']:.6g} ({plan.get('target_pct')}%) · profit +${plan.get('reward_usd') or 0:.2f}"
                  f" · {plan.get('rr_net')}R"},
    ]
    if plan.get("liquidation") and lev > 1:
        objs.append({"id": "desk_liq", "kind": "hline", "origin": "ai", "concept": "liquidation", "time1": t,
                     "price1": plan["liquidation"], "color": "#f87171", "style": "dotted",
                     "label": f"LIQ {lev}x {plan['liquidation']:.6g}"})
    o20 = next((o for o in plan.get("leverage_options", []) if o["leverage"] == 20), None)
    if o20:
        objs.append({"id": "desk_liq20", "kind": "hline", "origin": "ai", "concept": "liquidation", "time1": t,
                     "price1": o20["liquidation"], "color": "#f97316", "style": "dotted",
                     "label": f"LIQ 20x {o20['liquidation']:.6g}" + (" · before SL!" if o20["liquidated_before_stop"] else "")})
    return objs


class AIUnavailable(Exception):
    """The AI cannot run (no key, no credits, expired key): nothing is computed in its place."""


PLANNER = {"id": "planner", "name": "Pilot", "title": "Trade Planner", "stage": "plan", "color": "#0e9f84", "kind": "ai",
           "role": "plans one trade for the trader's margin: side, stop, target, leverage and size",
           "tools": ["chart_analysis", "derivatives_and_news", "model_forecast", "chart_patterns", "key_levels", "trade_plan", "search_knowledge"]}


async def quick_plan(symbol: str, side: str, tf: str, budget: float, mode: str, venue: str,
                     entry: float | None = None, stop: float | None = None, target: float | None = None,
                     margin: float | None = None) -> dict:
    """The add-trade form's plan, made by one AI agent (the Trade Planner) with the desk's tools: it reads the chart and
    positioning, chooses the side (or plans the side the trader chose), picks the stop and target from measured levels
    and calls trade_plan for the exact numbers. Raises AIUnavailable when the AI cannot run."""
    prov = provider()
    if prov is None:
        raise AIUnavailable(NO_KEY)
    sym = _sym(symbol)
    s = {"symbol": sym, "tf": tf, "side_requested": side.upper() if side else "AUTO", "budget": margin or budget,
         "margin": margin, "mode": mode, "venue": venue, "agents": [], "events": [], "t": time.time(), "tool_calls": 0}
    ctx = Ctx(s)
    walk, snap = await ctx.walk(tf)
    ctx.filters = await filters_for(sym)
    ctx.price = await live_price(sym, float(snap["price"]))
    tools = build_tools(ctx)
    req = s["side_requested"]
    task = (f"Plan one trade on {sym} ({tf} chart). "
            + ("Spot can only buy: plan a long. " if venue == "spot" else
               f"The trader wants to go {req.lower()}: plan that side, and say plainly if the chart argues against it. "
               if req in ("LONG", "SHORT") else "Decide the side from your own read of the chart and positioning. ")
            + (f"The trade already has its levels: stop {stop}, target {target}; use them in trade_plan. " if stop else
               "Call key_levels for your side, choose a stop that is a real invalidation and a target at 2R or a "
               "measured level beyond it, then call trade_plan. ")
            + "Read chart_analysis for this timeframe and one higher, and derivatives_and_news, before you decide. "
              "Then explain like an experienced trader: the side and why, the levels that matter, what would prove "
              "you wrong, and the plan's dollars at the stop and at the target.")
    try:
        res = await run_agent(_system(s, ctx, PLANNER), f"Live price {ctx.price:.6g}. Your task: {task}",
                              [tools[n] for n in PLANNER["tools"]], max_steps=6, max_tokens=2000)
    except Exception as e:  # noqa: BLE001
        if _fatal(str(e)):
            note_failure(str(e))
            raise AIUnavailable(f"The AI provider is unavailable ({_short(str(e))})") from e
        raise
    note_success()
    data = (res.data if res else None) or {}
    p = (ctx.plans.get(data.get("side")) if data.get("side") in ("LONG", "SHORT") else None) or ctx.last_plan
    if not p:
        raise ValueError("The Trade Planner did not produce a plan this time; press the button again.")
    from app.agents import validators as V

    ctx.evidence = await tools["strategy_evidence"].fn()
    validation = V.report(V.data_quality(s, ctx) + V.trade_checks(s, ctx, p, p["side"]) + V.evidence(s, ctx, p["side"]))
    text = (data.get("text") or (res.text if res else "") or "").strip()
    conf = data.get("confidence") if isinstance(data.get("confidence"), (int, float)) else None
    return {"symbol": sym, "tf": tf, "side": p["side"], "by": "Pilot · Trade Planner", "model": get_settings().openai_model
            if prov == "openai" else get_settings().llm_model,
            "why_side": "the side you chose" if req in ("LONG", "SHORT") else "the Trade Planner's own read",
            "agrees_with_read": (conf or 0.5) >= 0.5, "confidence": conf, "opinion": text, "plan": p,
            "tools_used": [{"name": u["name"], "args": u["args"], "ok": u["ok"]} for u in (res.tools_used if res else [])],
            "price": ctx.price, "validation": validation,
            "chart_objects": chart_objects(p, p["side"], int((snap.get("as_of") or time.time() * 1000) // 1000)),
            "disclaimer": DISCLAIMER}
