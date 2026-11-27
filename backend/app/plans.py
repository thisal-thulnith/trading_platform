"""Packages: three plans, what each one gets, and the daily usage that is counted against them.

The expensive part of the platform is the AI desk (about 30 model calls per analysis), so the plans are built around it,
and around signal quality rather than signal count:

  starter  free      the dashboard with the 3 best-scored coins (the rest locked), 4h and 1d only, signals shown
                     15 minutes after the paid plans get them, 2 AI analyses a day, in-app alerts
  pro      monthly   every coin and timeframe in real time, 15 AI analyses a day, email and Telegram alerts, delivery
                     5 minutes after Desk
  desk     monthly   everything, 60 AI analyses a day, Apex's 3-vote decision, first in the delivery queue, API access

An AI analysis is counted only when the agents actually run: a session the free readiness gate stops costs nothing, so
nobody pays for "wait". Usage is kept per user (or per installation when nobody is logged in) per UTC day.

The delivery delays are also the crowding control (docs/PLATFORM_PLAN.md): the same signal does not reach every user at
the same moment, and each signal carries a capacity — the size the order book can absorb — so the platform can stop
recommending it once enough of it has been sent out.
"""
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings


@dataclass(frozen=True)
class Plan:
    key: str
    name: str
    price_usd_month: int
    tagline: str
    ai_analyses_per_day: int
    signals_visible: int | None   # coins shown on the dashboard (None: all)
    timeframes: tuple[str, ...]
    delay_min: int                # minutes after the first delivery wave that this plan sees a new ready signal
    alerts: tuple[str, ...]
    head_votes: int
    api: bool
    features: tuple[str, ...]


PLANS: dict[str, Plan] = {p.key: p for p in [
    Plan("starter", "Starter", 0, "Learn the market and see the best setups.", 2, 3, ("4h", "1d"), 15, ("in-app",), 1, False,
         ("Dashboard and market overview", "Top 3 signals, 15 min delayed", "4h and 1d timeframes",
          "2 AI desk analyses a day", "Readiness check on any coin")),
    Plan("pro", "Pro", 29, "Every signal in real time, for active traders.", 15, None, ("15m", "1h", "4h", "1d"), 5,
         ("in-app", "email", "telegram"), 1, False,
         ("Every signal, every timeframe", "Delivered 5 min after Desk", "15 AI desk analyses a day",
          "Email and Telegram alerts", "Full validation and plan details")),
    Plan("desk", "Desk", 99, "The full research desk, first in line.", 60, None, ("15m", "1h", "4h", "1d"), 0,
         ("in-app", "email", "telegram", "webhook"), 3, True,
         ("Everything in Pro", "First delivery wave", "60 AI desk analyses a day", "Apex decides by 3-vote majority",
          "API and webhook access")),
]}


def plan_for(user) -> Plan:
    """The user's plan: stored in their settings; admins get Desk; nobody logged in gets the installation's plan."""
    s = get_settings()
    if user is None:
        return PLANS.get(s.anonymous_plan, PLANS["starter"])
    if getattr(user, "role", None) == "admin":
        return PLANS["desk"]
    return PLANS.get((user.settings or {}).get("plan", "starter"), PLANS["starter"])


def usage_key(user) -> str:
    return user.id if user is not None else "installation"


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


async def used(db: AsyncSession, key: str, kind: str) -> int:
    from app.models import Usage

    row = (await db.execute(select(Usage).where(Usage.key == key, Usage.day == _today(), Usage.kind == kind))).scalar_one_or_none()
    return row.count if row else 0


async def consume(key: str, kind: str = "ai_desk") -> int:
    """Count one use (its own transaction, so a desk run can call it in the background). Returns today's total."""
    from app.db import session_factory
    from app.models import Usage

    async with session_factory()() as db:
        row = (await db.execute(select(Usage).where(Usage.key == key, Usage.day == _today(), Usage.kind == kind))).scalar_one_or_none()
        if row is None:
            row = Usage(key=key, day=_today(), kind=kind, count=0)
            db.add(row)
        row.count += 1
        await db.commit()
        return row.count


async def status(db: AsyncSession, user) -> dict:
    p = plan_for(user)
    n = await used(db, usage_key(user), "ai_desk")
    return {"plan": asdict(p), "usage": {"ai_desk_today": n, "ai_desk_left": max(0, p.ai_analyses_per_day - n)},
            "enforced": get_settings().plans_enforced, "plans": [asdict(x) for x in PLANS.values()]}
