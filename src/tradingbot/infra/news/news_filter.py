"""
Finnhub Economic Calendar — News Filter
Fetches HIGH impact USD events and blocks trading 15 min before/after.
Caches results for 1 hour to avoid hammering the API (60 calls/min free tier).
"""

import logging
import requests
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
FINNHUB_CALENDAR_URL = "https://finnhub.io/api/v1/calendar/economic"
BLOCK_CURRENCIES     = {"USD", "US"}   # events that affect XAUUSD
BLOCK_IMPACT         = "high"
BLACKOUT_MINUTES     = 15             # block window before AND after event
CACHE_TTL_SECONDS    = 3600           # refresh calendar once per hour


class NewsFilter:
    """
    Fetches Finnhub economic calendar once per hour and blocks
    trading during HIGH-impact USD events ± BLACKOUT_MINUTES.

    Usage:
        nf = NewsFilter(api_key="your_key")
        blocked, reason = nf.is_news_blackout()
        if blocked:
            print(reason)  # "HIGH_IMPACT_NEWS_BLACKOUT: FOMC Rate Decision"
    """

    def __init__(self, api_key: str, blackout_minutes: int = BLACKOUT_MINUTES):
        self.api_key          = api_key
        self.blackout_minutes = blackout_minutes
        self._cache: list     = []
        self._cache_ts: Optional[datetime] = None

    # ── Public ────────────────────────────────────────────────────────────────

    def is_news_blackout(
        self,
        now_utc: Optional[datetime] = None,
    ) -> tuple[bool, Optional[str]]:
        """
        Returns (True, reason_string) if currently in a news blackout window.
        Returns (False, None) if safe to trade.

        Fail-safe: if Finnhub is unreachable, returns (False, None) —
        we never block on API errors, only on confirmed events.
        """
        if not self.api_key:
            return False, None  # key not configured → skip filter silently

        now = now_utc or datetime.now(timezone.utc)
        events = self._get_events(now)

        blackout_delta = timedelta(minutes=self.blackout_minutes)

        for event in events:
            # Guard: only block on HIGH impact USD events
            # (_fetch_events filters too, but direct cache injection in tests bypasses it)
            if event.get("impact", "").lower() != BLOCK_IMPACT:
                continue
            if event.get("country", "").upper() not in BLOCK_CURRENCIES:
                continue

            event_time = self._parse_event_time(event.get("time", ""))
            if event_time is None:
                continue

            window_start = event_time - blackout_delta
            window_end   = event_time + blackout_delta

            if window_start <= now <= window_end:
                name   = event.get("event", "Unknown Event")
                impact = event.get("impact", "")
                country = event.get("country", "")
                reason = (
                    f"HIGH_IMPACT_NEWS_BLACKOUT: {name} "
                    f"({country}, {impact.upper()}) "
                    f"@ {event_time.strftime('%H:%M')} UTC "
                    f"± {self.blackout_minutes}min"
                )
                logger.warning(f"🚫 News blackout active: {reason}")
                return True, reason

        return False, None

    # ── Private ───────────────────────────────────────────────────────────────

    def _get_events(self, now: datetime) -> list:
        """Return cached events or fetch fresh ones from Finnhub."""
        cache_stale = (
            self._cache_ts is None
            or (now - self._cache_ts).total_seconds() > CACHE_TTL_SECONDS
        )
        if cache_stale:
            self._cache    = self._fetch_events(now)
            self._cache_ts = now
        return self._cache

    def _fetch_events(self, now: datetime) -> list:
        """
        Fetch today's HIGH-impact USD events from Finnhub.
        Returns empty list on any error — never raises.
        """
        try:
            date_str = now.strftime("%Y-%m-%d")
            resp = requests.get(
                FINNHUB_CALENDAR_URL,
                params={
                    "from":  date_str,
                    "to":    date_str,
                    "token": self.api_key,
                },
                timeout=5,
            )
            if resp.status_code != 200:
                logger.warning(f"Finnhub returned {resp.status_code} — skipping news filter")
                return []

            all_events = resp.json().get("economicCalendar", [])

            # Keep only HIGH impact events for USD / US
            filtered = [
                e for e in all_events
                if e.get("impact", "").lower() == BLOCK_IMPACT
                and e.get("country", "").upper() in BLOCK_CURRENCIES
            ]

            logger.info(
                f"Finnhub: {len(all_events)} events today, "
                f"{len(filtered)} HIGH-impact USD events"
            )
            return filtered

        except Exception as e:
            logger.error(f"Finnhub fetch error: {e} — news filter disabled for this cycle")
            return []

    @staticmethod
    def _parse_event_time(time_str: str) -> Optional[datetime]:
        """Parse Finnhub time string '2026-05-01 13:30:00' → UTC datetime."""
        try:
            return datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
        except Exception:
            return None