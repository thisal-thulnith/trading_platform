"""News intelligence: RSS collection, dedup/clustering, entity extraction, event typing, lexicon sentiment,
credibility and impact scoring. Feed URLs verified 2026-11-01 (docs/DATA_SOURCES.md).

Sentiment here is a transparent lexicon heuristic (method 'lexicon_v1'), not a validated predictor.
Headlines never trigger trades by themselves; they only add context/abstention to analysis."""
import asyncio
import hashlib
import re
from datetime import UTC, datetime, timedelta

import feedparser
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import log
from app.models import NewsItem

FEEDS = {
    "CoinDesk": ("https://www.coindesk.com/arc/outboundfeeds/rss/", 0.8),
    "Cointelegraph": ("https://cointelegraph.com/rss", 0.7),
    "Decrypt": ("https://decrypt.co/feed", 0.7),
    "The Block": ("https://www.theblock.co/rss.xml", 0.8),
    "Bitcoin Magazine": ("https://bitcoinmagazine.com/feed", 0.6),
    "Federal Reserve": ("https://www.federalreserve.gov/feeds/press_all.xml", 1.0),
}

COINS = {
    "BTC": ["bitcoin", "btc"], "ETH": ["ethereum", "ether", "eth"], "SOL": ["solana"], "BNB": ["bnb", "binance coin"],
    "XRP": ["xrp", "ripple"], "DOGE": ["dogecoin", "doge"], "ADA": ["cardano"], "AVAX": ["avalanche", "avax"],
    "LINK": ["chainlink"], "DOT": ["polkadot"], "LTC": ["litecoin"], "BCH": ["bitcoin cash"], "TRX": ["tron", "trx"],
    "SUI": ["sui"], "TON": ["toncoin", "the open network"], "XLM": ["stellar", "xlm"], "HBAR": ["hedera", "hbar"],
    "NEAR": ["near protocol"], "UNI": ["uniswap"], "APT": ["aptos"], "ZEC": ["zcash"], "HYPE": ["hyperliquid"],
    "AAVE": ["aave"], "ENA": ["ethena"], "ONDO": ["ondo"], "PEPE": ["pepe"], "TAO": ["bittensor"],
}
EVENT_RULES = [
    ("hack_exploit", r"\b(hack|hacked|exploit|drained|stolen|breach|attack)\b"),
    ("regulation", r"\b(sec|cftc|regulat\w*|lawmakers?|bill|ban|sanction\w*|mica|compliance)\b"),
    ("etf", r"\betfs?\b"),
    ("listing", r"\b(lists?|listing|delist\w*)\b"),
    ("macro", r"\b(fed|fomc|rate (cut|hike)|inflation|cpi|jobs report|treasury|powell|tariffs?)\b"),
    ("legal", r"\b(lawsuit|sues?|court|charged|indict\w*|settle\w*)\b"),
    ("upgrade", r"\b(upgrade|hard fork|mainnet|launch(es|ed)?)\b"),
    ("partnership", r"\b(partner\w*|integrat\w*|acquir\w*)\b"),
    ("flows", r"\b(inflows?|outflows?|treasury company|buys? \$?\d)\b"),
    ("market_move", r"\b(surge\w*|rall\w*|plung\w*|crash\w*|soar\w*|tumbl\w*|record high|all-time high)\b"),
]
POS = {"surge", "surges", "rally", "rallies", "soar", "soars", "approve", "approved", "approval", "inflow", "inflows",
       "record", "high", "gain", "gains", "bullish", "partnership", "adoption", "upgrade", "launch", "wins", "rebound",
       "rebounds", "jump", "jumps", "climb", "climbs", "breakout", "accumulate", "buys", "boost"}
NEG = {"hack", "hacked", "exploit", "drained", "stolen", "lawsuit", "sues", "ban", "bans", "crash", "crashes",
       "plunge", "plunges", "outflow", "outflows", "delist", "delisting", "liquidation", "liquidations", "bankruptcy",
       "charged", "fraud", "bearish", "tumble", "tumbles", "drop", "drops", "slump", "falls", "sell-off", "selloff",
       "warning", "probe", "investigation", "fine", "fined", "halt", "halts", "losses", "dump"}
STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "as", "is", "at", "by", "with", "its", "after", "from"}


def tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9$\-]+", text.lower()) if w not in STOP]


def cluster_key(title: str) -> str:
    return hashlib.sha1(" ".join(sorted(set(tokens(title)))[:12]).encode()).hexdigest()[:16]


def jaccard(a: str, b: str) -> float:
    x, y = set(tokens(a)), set(tokens(b))
    return len(x & y) / len(x | y) if x | y else 0.0


def extract_coins(text: str) -> list[str]:
    low = text.lower()
    found = []
    for sym, names in COINS.items():
        if any(re.search(rf"(?<![a-z]){re.escape(n)}(?![a-z])", low) for n in names) or re.search(rf"\${sym}\b", text):
            found.append(sym)
    if "BCH" in found and "BTC" in found and "bitcoin cash" in low and low.count("bitcoin") == low.count("bitcoin cash"):
        found.remove("BTC")
    return found


def classify_event(text: str) -> str:
    low = text.lower()
    for name, pat in EVENT_RULES:
        if re.search(pat, low):
            return name
    return "general"


def sentiment(text: str) -> float:
    ws = tokens(text)
    p = sum(1 for w in ws if w in POS)
    n = sum(1 for w in ws if w in NEG)
    if p + n == 0:
        return 0.0
    return round((p - n) / (p + n) * min(1.0, (p + n) / 3), 3)


def impact(event_type: str, credibility: float, corroborations: int, coins: list[str] | None = None) -> str:
    """High impact needs a market-relevant event type *and* either specific coins, a macro source, or
    corroboration across sources."""
    relevant = bool(coins) or event_type == "macro" or corroborations >= 2
    if (event_type in ("hack_exploit", "regulation", "etf", "macro") and credibility >= 0.7 and relevant) \
            or corroborations >= 3:
        return "high"
    if event_type in ("listing", "legal", "flows", "market_move") or corroborations >= 2:
        return "medium"
    return "low"


def _published(e) -> datetime | None:
    t = e.get("published_parsed") or e.get("updated_parsed")
    return datetime(*t[:6], tzinfo=UTC) if t else None


async def fetch_feed(client: httpx.AsyncClient, name: str, url: str) -> list[dict]:
    try:
        r = await client.get(url, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError as e:
        log.warning("feed_failed", feed=name, error=str(e))
        return []
    parsed = feedparser.parse(r.content)
    out = []
    for e in parsed.entries[:40]:
        link = e.get("link")
        if not link:
            continue
        summary = re.sub(r"<[^>]+>", " ", e.get("summary", ""))[:800]
        out.append({"source": name, "url": link, "title": e.get("title", "").strip(), "summary": summary.strip(),
                    "published_at": _published(e)})
    return out


async def collect_news(db: AsyncSession) -> dict:
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "aitrade-news/0.1 (research; RSS reader)"}) as c:
        results = await asyncio.gather(*(fetch_feed(c, n, u) for n, (u, _) in FEEDS.items()))
    items = [i for r in results for i in r]
    existing = set((await db.execute(select(NewsItem.url).where(NewsItem.url.in_([i["url"] for i in items])))).scalars())
    since = datetime.now(UTC) - timedelta(hours=12)
    recent = (await db.execute(select(NewsItem).where(NewsItem.fetched_at >= since))).scalars().all()
    added = 0
    for it in items:
        if it["url"] in existing:
            continue
        text = f"{it['title']}. {it['summary']}"
        cred = FEEDS[it["source"]][1]
        ck = cluster_key(it["title"])
        twins = [r for r in recent if r.source != it["source"] and (r.cluster_key == ck or jaccard(r.title, it["title"]) >= 0.5)]
        corr = 1 + len({t.source for t in twins})
        for t in twins:
            t.corroborations = max(t.corroborations, corr)
            t.impact = impact(t.event_type, t.credibility, t.corroborations, t.coins)
        et = classify_event(text)
        n = NewsItem(source=it["source"], url=it["url"], title=it["title"], summary=it["summary"],
                     published_at=it["published_at"], coins=extract_coins(text), sentiment=sentiment(text),
                     event_type=et, credibility=min(1.0, cred + 0.05 * (corr - 1)), cluster_key=twins[0].cluster_key if twins else ck,
                     corroborations=corr)
        n.impact = impact(et, n.credibility, corr, n.coins)
        db.add(n)
        recent.append(n)
        existing.add(it["url"])
        added += 1
    await db.commit()
    return {"fetched": len(items), "added": added, "feeds_ok": sum(1 for r in results if r), "feeds_total": len(FEEDS)}


async def fear_greed() -> dict | None:
    """alternative.me Crypto Fear & Greed Index (free, attribution requested)."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            d = (await c.get("https://api.alternative.me/fng/?limit=1")).json()["data"][0]
        return {"value": int(d["value"]), "label": d["value_classification"], "ts": int(d["timestamp"]) * 1000,
                "source": "alternative.me"}
    except (httpx.HTTPError, KeyError, ValueError, IndexError):
        return None
