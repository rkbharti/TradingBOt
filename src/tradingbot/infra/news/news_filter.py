import logging
import os
import requests
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

FINNHUB_CALENDAR_URL = "https://finnhub.io/api/v1/calendar/economic"
BLOCK_CURRENCIES = {"USD", "US"}
BLOCK_IMPACT = "high"
BLACKOUT_MINUTES = 15
CACHE_TTL_SECONDS = 3600
STRICT_MODE = os.getenv("NEWS_BLOCK_STRICT_MODE", "false").lower() == "true"

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
    def __init__(self, api_key: Optional[str] = None, blackout_minutes: int = BLACKOUT_MINUTES):
        self.api_key = api_key
        self.blackout_minutes = blackout_minutes
        self._cache: list = []
        self._cache_ts: Optional[datetime] = None

    def is_news_blackout(self, now_utc: Optional[datetime] = None, symbol: str = "XAUUSD") -> tuple[bool, Optional[str]]:
        now = now_utc or datetime.now(timezone.utc)
        events = self._get_events(now)
        blackout_delta = timedelta(minutes=self.blackout_minutes)

        # Determine currencies to block based on symbol
        symbol_upper = symbol.upper()
        # strip any broker suffixes (e.g., .pro, .raw, .a, .b, .m)
        clean_symbol = "".join(c for c in symbol_upper if c.isalpha())
        
        if len(clean_symbol) == 6:
            base = clean_symbol[:3]
            quote = clean_symbol[3:]
            target_currencies = {base, quote}
        else:
            if "USD" in clean_symbol or "XAU" in clean_symbol or "GOLD" in clean_symbol:
                target_currencies = {"USD", "US"}
            else:
                target_currencies = {"USD", "US"}
                
        # Also map USD/US to block both
        if "USD" in target_currencies:
            target_currencies.add("US")
        if "US" in target_currencies:
            target_currencies.add("USD")

        for event in events:
            if event.get("impact", "").lower() != BLOCK_IMPACT:
                continue
            if event.get("country", "").upper() not in target_currencies:
                continue

            name = event.get("event", "Unknown Event")
            tier = self._classify_event(name)

            if tier == "ignore":
                continue

            country = event.get("country", "US")
            event_time = self._parse_event_time(event.get("time", ""), country=country)
            if event_time is None:
                continue

            window_start = event_time - blackout_delta
            window_end = event_time + blackout_delta

            if window_start <= now <= window_end:
                if tier == "hard" or STRICT_MODE:
                    block_type = "HARD" if tier == "hard" else "SOFT"
                    reason = (
                        f"{block_type}_NEWS_BLACKOUT: {name} ({country}) "
                        f"@ {event_time.strftime('%H:%M')} UTC "
                        f"± {self.blackout_minutes}min"
                    )
                    logger.warning(reason)
                    return True, reason

                logger.info(
                    f"SOFT news event active but not blocking: {name} ({country}) "
                    f"@ {event_time.strftime('%H:%M')} UTC"
                )

        return False, None

    def _load_cache_from_disk(self) -> None:
        """Load cached economic events from logs/news_cache.json."""
        from pathlib import Path
        import json
        CACHE_FILE = Path(__file__).resolve().parents[4] / "logs" / "news_cache.json"
        if CACHE_FILE.exists():
            try:
                data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
                ts_str = data.get("cache_ts")
                if ts_str:
                    self._cache_ts = datetime.fromisoformat(ts_str).astimezone(timezone.utc)
                    self._cache = data.get("events", [])
                    logger.info(f"📂 Loaded news cache from disk, timestamp: {self._cache_ts}")
            except Exception as e:
                logger.warning(f"Could not load news cache from disk: {e}")

    def _save_cache_to_disk(self) -> None:
        """Save cached economic events to logs/news_cache.json."""
        from pathlib import Path
        import json
        CACHE_FILE = Path(__file__).resolve().parents[4] / "logs" / "news_cache.json"
        try:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "cache_ts": self._cache_ts.isoformat() if self._cache_ts else None,
                "events": self._cache
            }
            CACHE_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Could not save news cache to disk: {e}")

    def _get_events(self, now: datetime) -> list:
        # Load cache from disk if memory cache is empty
        if not self._cache and not self._cache_ts:
            self._load_cache_from_disk()

        cache_stale = (
            self._cache_ts is None
            or (now - self._cache_ts).total_seconds() > CACHE_TTL_SECONDS
        )
        if cache_stale:
            fresh_events = self._fetch_events(now)
            if fresh_events:
                self._cache = fresh_events
                self._cache_ts = now
                self._save_cache_to_disk()
            else:
                if self._cache_ts is None:
                    # No cache available, try to fetch again next cycle
                    pass
                else:
                    logger.warning(
                        "⚠️ ForexFactory news fetch failed. Retaining previously cached news events for safety."
                    )
                    # Try to refresh again in 5 minutes
                    self._cache_ts = now - timedelta(seconds=CACHE_TTL_SECONDS - 300)
        return self._cache

    def _fetch_events(self, now: datetime) -> list:
        try:
            url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
            }
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code != 200:
                logger.warning(f"ForexFactory calendar returned {resp.status_code} — skipping news filter")
                return []

            raw_events = resp.json()
            normalized_events = []
            for item in raw_events:
                normalized_events.append({
                    "event": item.get("title", ""),
                    "country": item.get("country", ""),
                    "impact": item.get("impact", ""),
                    "time": item.get("date", ""),
                })

            filtered = [
                e for e in normalized_events
                if e.get("impact", "").lower() == BLOCK_IMPACT
            ]
            return filtered

        except Exception as e:
            logger.error(f"ForexFactory news fetch error: {e} — news filter disabled for this cycle")
            return []

    @staticmethod
    def _parse_event_time(time_str: str, country: str = "US") -> Optional[datetime]:
        try:
            import pytz
            clean_str = time_str.strip()
            
            # Case 1: Try ISO 8601 parsing directly (useful for ForexFactory json date formats)
            try:
                return datetime.fromisoformat(clean_str).astimezone(timezone.utc)
            except Exception:
                pass

            # Case 2: ISO 8601 with trailing Z
            if clean_str.endswith("Z"):
                try:
                    return datetime.strptime(clean_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                except ValueError:
                    pass
            
            # Case 3: Standard YYYY-MM-DD HH:MM:SS format
            dt = datetime.strptime(clean_str, "%Y-%m-%d %H:%M:%S")
            if country.upper() in {"US", "USD"}:
                ny_tz = pytz.timezone("America/New_York")
                dt_localized = ny_tz.localize(dt)
                return dt_localized.astimezone(timezone.utc)
            else:
                return dt.replace(tzinfo=timezone.utc)
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