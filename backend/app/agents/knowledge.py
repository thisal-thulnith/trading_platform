"""The desk's knowledge base: everything the project has written down, searchable by the agents.

Sources: the research docs (docs/RESEARCH.md with evidence grades, TRADING_CONCEPTS.md with the exact definition of
every detection, STRATEGIES.md with specs and backtest results, ML_RESEARCH.md), the CHANGELOG's study findings
(edge, exit, leverage, win-rate, carry studies) and the method primers of the specialist agents (SMC, ICT, Wyckoff,
Elliott/NeoWave, Gann, Market Profile, harmonics, Hurst, Quasimodo, Bill Williams, Dow, momentum, derivatives).

Each document is split at its headings; `search()` ranks sections by TF-IDF over the query words (titles weigh more)
and returns the best few, clipped, so an agent can cite how a concept is defined or what the research measured.
"""
import math
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DOCS = ["docs/AGENT_PLAYBOOK.md", "docs/FUNDAMENTALS.md", "docs/AGENT_SYSTEM.md", "docs/VALIDATORS.md", "docs/RESEARCH.md", "docs/TRADING_CONCEPTS.md", "docs/STRATEGIES.md", "docs/ML_RESEARCH.md", "CHANGELOG.md"]
CLIP = 1800
_WORD = re.compile(r"[a-z0-9][a-z0-9\-]{2,}")
_STOP = set("the and for with that this from are was were not but can its it's into than then they them their has have "
            "had you your our all any each per when what which who how why also only more most less over under".split())
_index: dict = {}


def _words(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP]


def _split(source: str, text: str) -> list[dict]:
    out, title, buf = [], source, []
    for line in text.splitlines():
        if line.startswith("#"):
            if "".join(buf).strip():
                out.append({"source": source, "title": title, "text": "\n".join(buf).strip()})
            title, buf = line.lstrip("#").strip() or source, []
        else:
            buf.append(line)
    if "".join(buf).strip():
        out.append({"source": source, "title": title, "text": "\n".join(buf).strip()})
    return out


def _build() -> dict:
    from app.agents.live_room import KNOWLEDGE

    sections: list[dict] = []
    for rel in DOCS:
        p = ROOT / rel
        if p.exists():
            sections += _split(rel, p.read_text(errors="ignore"))
    names = {"atlas": "Top-down / higher timeframe", "sage": "SMC market structure", "hunter": "Liquidity",
             "mason": "Order blocks and fair value gaps", "wave": "Elliott Wave", "pulse": "Momentum and volume",
             "flow": "Derivatives and news", "composite": "Wyckoff", "dow": "Dow Theory", "angle": "Gann",
             "auction": "Market Profile / auction theory", "fib": "Harmonic patterns", "session": "ICT time concepts",
             "neo": "NeoWave", "cycle": "Hurst cycles", "qm": "Quasimodo", "chaos": "Bill Williams chaos",
             "skeptic": "Risk of ruin and base rates"}
    for k, v in KNOWLEDGE.items():
        sections.append({"source": "method primers", "title": names.get(k, k), "text": v})
    for s in sections:
        s["tf"] = Counter(_words(s["text"]))
        s["tt"] = Counter(_words(s["title"]))
        s["len"] = sum(s["tf"].values()) or 1
    df = Counter(w for s in sections for w in set(s["tf"]) | set(s["tt"]))
    return {"sections": sections, "df": df, "n": len(sections)}


def index() -> dict:
    if not _index:
        _index.update(_build())
    return _index


def search(query: str, k: int = 4) -> dict:
    ix = index()
    q = _words(query)
    if not q or not ix["sections"]:
        return {"query": query, "results": [], "note": "empty query or no knowledge files found"}
    scored = []
    for s in ix["sections"]:
        sc = 0.0
        for w in q:
            idf = math.log(1 + ix["n"] / (1 + ix["df"].get(w, 0)))
            sc += idf * (s["tf"].get(w, 0) / math.sqrt(s["len"]) * 10 + 3 * s["tt"].get(w, 0))
        if sc > 0:
            scored.append((sc, s))
    scored.sort(key=lambda x: -x[0])
    k = max(1, min(int(k), 6))
    return {"query": query, "results": [{"source": s["source"], "section": s["title"],
                                          "text": s["text"][:CLIP] + ("…" if len(s["text"]) > CLIP else "")}
                                         for _, s in scored[:k]]}


def topics() -> dict:
    ix = index()
    by: dict[str, list[str]] = {}
    for s in ix["sections"]:
        by.setdefault(s["source"], []).append(s["title"])
    return {"sections": ix["n"], "by_source": {k: v[:60] for k, v in by.items()}}
