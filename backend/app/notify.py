"""Notification creation (deduplicated) and optional external delivery (Telegram bot, email)."""
import smtplib
from email.message import EmailMessage
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bus import bus
from app.config import get_settings
from app.logging import log
from app.models import Notification

CATEGORIES = ["opportunity_forming", "pre_signal", "confirmed_signal", "entry_reached", "stop_reached",
              "target_reached", "setup_invalidated", "high_impact_news", "abnormal_volatility",
              "model_degradation", "data_feed_failure"]
SEVERITY = {"confirmed_signal": "high", "stop_reached": "high", "target_reached": "high", "high_impact_news": "high",
            "data_feed_failure": "high", "model_degradation": "medium", "pre_signal": "medium",
            "entry_reached": "medium", "abnormal_volatility": "medium"}


async def notify(s: AsyncSession, category: str, title: str, body: str, dedupe_key: str, *, symbol=None,
                 timeframe=None, signal_id=None, snapshot_id=None, user_id=None) -> Notification | None:
    assert category in CATEGORIES, category
    if (await s.execute(select(Notification.id).where(Notification.dedupe_key == dedupe_key))).first():
        return None
    n = Notification(category=category, severity=SEVERITY.get(category, "info"), title=title, body=body,
                     dedupe_key=dedupe_key, symbol=symbol, timeframe=timeframe, signal_id=signal_id,
                     snapshot_id=snapshot_id, user_id=user_id)
    s.add(n)
    await s.flush()
    payload = {"id": n.id, "category": category, "severity": n.severity, "title": title, "body": body,
               "symbol": symbol, "timeframe": timeframe, "signal_id": signal_id, "snapshot_id": snapshot_id,
               "ts": n.ts.isoformat() if n.ts else None}
    bus.publish("notification", payload)
    if n.severity == "high":
        await deliver_external(title, body)
    return n


async def deliver_external(title: str, body: str) -> None:
    st = get_settings()
    if st.telegram_bot_token and st.telegram_chat_id:
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                await c.post(f"https://api.telegram.org/bot{st.telegram_bot_token}/sendMessage",
                             json={"chat_id": st.telegram_chat_id, "text": f"{title}\n{body}"[:4000]})
        except httpx.HTTPError as e:
            log.warning("telegram_failed", error=str(e))
    if st.smtp_url:
        # smtp_url format: smtp://user:pass@host:port/to_address
        try:
            u = urlparse(st.smtp_url)
            msg = EmailMessage()
            msg["Subject"], msg["From"], msg["To"] = title, u.username or "aitrade", u.path.lstrip("/")
            msg.set_content(body)
            with smtplib.SMTP(u.hostname, u.port or 587, timeout=10) as smtp:
                smtp.starttls()
                if u.username:
                    smtp.login(u.username, u.password or "")
                smtp.send_message(msg)
        except (OSError, smtplib.SMTPException) as e:
            log.warning("email_failed", error=str(e))
