import logging
import requests
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

FINNHUB_CALENDAR_URL = "https://finnhub.io/api/v1/calendar/economic"
BLOCK_CURRENCIES = {"USD", "US"}
BLOCK_IMPACT = "high"
BLACKOUT_MINUTES = 15
CACHE_TTL_SECONDS = 3600

HARD_BLOCK_KEYWORDS = {
    "fomc",
    "federal funds rate",
    "interest rate decision",
    "cpi",
    "core cpi",
    "consumer price index",
    "non farm payroll",
    "nonfarm payroll",
    "nfp",
    "pce",
    "core pce",
    "powell",
    "jerome powell",
}

SOFT_BLOCK_KEYWORDS = {
    "gdp",
    "retail sales",
    "ism manufacturing",
    "ism services",
    "jobless claims",
    "initial jobless claims",
    "unemployment claims",
}

IGNORE_KEYWORDS = {
    "existing home sales",
    "pending home sales",
    "new home sales",
    "housing starts",
    "building permits",
}

class NewsFilter:
    def __init__(self, api_key: str, blackout_minutes: int = BLACKOUT_MINUTES):
        self.api_key = api_key
        self.blackout_minutes = blackout_minutes
        self._cache: list = []
        self._cache_ts: Optional[datetime] = None

    def is_news_blackout(self, now_utc: Optional[datetime] = None) -> tuple[bool, Optional[str]]:
        if not self.api_key:
            return False, None

        now = now_utc or datetime.now(timezone.utc)
        events = self._get_events(now)
        blackout_delta = timedelta(minutes=self.blackout_minutes)

        for event in events:
            if event.get("impact", "").lower() != BLOCK_IMPACT:
                continue
            if event.get("country", "").upper() not in BLOCK_CURRENCIES:
                continue

            name = event.get("event", "Unknown Event")
            tier = self._classify_event(name)

            if tier == "ignore":
                continue

            event_time = self._parse_event_time(event.get("time", ""))
            if event_time is None:
                continue

            window_start = event_time - blackout_delta
            window_end = event_time + blackout_delta

            if window_start <= now <= window_end:
                if tier == "hard":
                    reason = (
                        f"HARD_NEWS_BLACKOUT: {name} "
                        f"@ {event_time.strftime('%H:%M')} UTC "
                        f"± {self.blackout_minutes}min"
                    )
                    logger.warning(reason)
                    return True, reason

                logger.info(
                    f"SOFT news event active but not blocking: {name} "
                    f"@ {event_time.strftime('%H:%M')} UTC"
                )

        return False, None

    def _get_events(self, now: datetime) -> list:
        cache_stale = (
            self._cache_ts is None
            or (now - self._cache_ts).total_seconds() > CACHE_TTL_SECONDS
        )
        if cache_stale:
            self._cache = self._fetch_events(now)
            self._cache_ts = now
        return self._cache

    def _fetch_events(self, now: datetime) -> list:
        try:
            date_str = now.strftime("%Y-%m-%d")
            resp = requests.get(
                FINNHUB_CALENDAR_URL,
                params={"from": date_str, "to": date_str, "token": self.api_key},
                timeout=5,
            )
            if resp.status_code != 200:
                logger.warning(f"Finnhub returned {resp.status_code} — skipping news filter")
                return []

            all_events = resp.json().get("economicCalendar", [])
            filtered = [
                e for e in all_events
                if e.get("impact", "").lower() == BLOCK_IMPACT
                and e.get("country", "").upper() in BLOCK_CURRENCIES
            ]
            return filtered

        except Exception as e:
            logger.error(f"Finnhub fetch error: {e} — news filter disabled for this cycle")
            return []

    @staticmethod
    def _parse_event_time(time_str: str) -> Optional[datetime]:
        try:
            return datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except Exception:
            return None

    @staticmethod
    def _classify_event(event_name: str) -> str:
        name = event_name.lower().strip()

        if any(k in name for k in IGNORE_KEYWORDS):
            return "ignore"
        if any(k in name for k in HARD_BLOCK_KEYWORDS):
            return "hard"
        if any(k in name for k in SOFT_BLOCK_KEYWORDS):
            return "soft"

        return "soft"