"""Detekce vyčerpaného limitu předplatného Claude (session / weekly limit).

Claude Code hlásí stav limitů dvěma cestami:

* ``claude --print`` posílá ``{"type": "rate_limit_event", "rate_limit_info": {...}}``;
  při vyčerpání má ``status: "rejected"`` a ``resetsAt`` (unix sekundy).
* Odmítnutý požadavek skončí syntetickou assistant zprávou s ``error: "rate_limit"``
  (``claude-p`` ji forwarduje z transcriptu i s ``apiErrorParams.rate_limit_info`` /
  ``quotaLimits``) a textem typu ``You've hit your session limit · resets 3:40pm (Europe/Prague)``.

Běh se za „narazil na limit“ považuje, jen když skončil chybou a předtím přišel
odmítnutý limit — ``rejected`` pokrytý overage běh nezastaví.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# "resets 3:40pm (Europe/Prague)", "resets 2am (UTC)", "resets Oct 25, 5pm (Europe/Prague)"
_RESETS_TEXT_RE = re.compile(
    r"resets\s+(?:(?P<month>[A-Z][a-z]{2})\s+(?P<day>\d{1,2}),?\s+)?"
    r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>am|pm)\s*\((?P<tz>[^)]+)\)",
    re.IGNORECASE,
)
# Starší formát: "Claude AI usage limit reached|1730000000"
_LEGACY_TEXT_RE = re.compile(r"usage limit reached\|(?P<epoch>\d{9,})", re.IGNORECASE)
_MONTHS = {name: index for index, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1
)}


def rate_limit_info_payload(info: Any) -> Dict[str, Any]:
    """``rate_limit_info`` z Claude → sjednocená data eventu ``rate_limit``."""
    if not isinstance(info, dict):
        return {}
    payload: Dict[str, Any] = {
        "status": info.get("status"),
        "resets_at": info.get("resetsAt") if isinstance(info.get("resetsAt"), (int, float)) else None,
        "rate_limit_type": info.get("rateLimitType"),
    }
    if info.get("isUsingOverage") is not None:
        payload["is_using_overage"] = bool(info.get("isUsingOverage"))
    return {key: value for key, value in payload.items() if value is not None}


def parse_resets_at(text: str, *, now: Optional[datetime] = None) -> Optional[int]:
    """Vytáhne čas obnovy limitu (unix sekundy) z textu chybové hlášky Claude."""
    if not text:
        return None
    legacy = _LEGACY_TEXT_RE.search(text)
    if legacy:
        return int(legacy.group("epoch"))
    match = _RESETS_TEXT_RE.search(text)
    if not match:
        return None
    try:
        tz = ZoneInfo(match.group("tz").strip())
    except (ZoneInfoNotFoundError, ValueError):
        return None
    hour = int(match.group("hour")) % 12 + (12 if match.group("ampm").lower() == "pm" else 0)
    minute = int(match.group("minute") or 0)
    current = (now or datetime.now(timezone.utc)).astimezone(tz)
    month_name = match.group("month")
    if month_name:
        month = _MONTHS.get(month_name.lower())
        if month is None:
            return None
        try:
            candidate = current.replace(
                month=month, day=int(match.group("day")), hour=hour, minute=minute, second=0, microsecond=0
            )
        except ValueError:
            return None
        if candidate < current - timedelta(days=1):
            candidate = candidate.replace(year=candidate.year + 1)
    else:
        candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= current:
            candidate += timedelta(days=1)
    return int(candidate.timestamp())


class RateLimitTracker:
    """Sleduje ``rate_limit`` eventy jednoho běhu a na konci řekne, zda ho zastavil limit."""

    def __init__(self) -> None:
        self._rejected: Optional[Dict[str, Any]] = None

    def observe(self, data: Dict[str, Any]) -> None:
        if data.get("status") != "rejected" or data.get("is_using_overage"):
            return
        # Přesný `resetsAt` z `rate_limit_event` přichází před syntetickou zprávou,
        # jejíž čas je jen odhad z textu — první odmítnutí se známým časem vyhrává.
        if self._rejected is None or self._rejected.get("resets_at") is None:
            self._rejected = dict(data)

    def result_fields(self, *, failed: bool) -> Dict[str, Any]:
        """Pole přidaná do ``result`` eventu, když běh skončil na vyčerpaném limitu."""
        if not failed or self._rejected is None:
            return {}
        fields: Dict[str, Any] = {"rate_limited": True}
        resets_at = self._rejected.get("resets_at")
        if resets_at is not None:
            fields["rate_limit_resets_at"] = (
                datetime.fromtimestamp(resets_at, timezone.utc).isoformat().replace("+00:00", "Z")
            )
        if self._rejected.get("rate_limit_type"):
            fields["rate_limit_type"] = self._rejected["rate_limit_type"]
        return fields


__all__ = ["RateLimitTracker", "parse_resets_at", "rate_limit_info_payload"]
