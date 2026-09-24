"""Foot traffic forecasts of BestTime.app, the optional source of the busy hours of a store."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

API = "https://besttime.app/api/v1"
TIMEOUT = aiohttp.ClientTimeout(total=60)


class BestTimeError(Exception):
    """BestTime.app rejected the request or could not be reached"""


def week_hours(analysis: Any) -> list[list[int | None]]:
    """
    Expected busyness (0-100 %) per weekday (Monday first) and hour of the day.

    A day of BestTime runs from 6:00 to 5:00, its last six hours belong to the next day.
    """
    hours: list[list[int | None]] = [[None] * 24 for _ in range(7)]
    for day in analysis if isinstance(analysis, list) else []:
        info = day.get("day_info") if isinstance(day, dict) else None
        raw = day.get("day_raw") if isinstance(day, dict) else None
        day_int = info.get("day_int") if isinstance(info, dict) else None
        if not isinstance(day_int, int) or not 0 <= day_int <= 6 or not isinstance(raw, list) or len(raw) != 24:
            continue
        for index, value in enumerate(raw):
            weekday = day_int if index < 18 else (day_int + 1) % 7
            hours[weekday][(6 + index) % 24] = value if isinstance(value, int) else None
    return hours


async def async_new_forecast(session: aiohttp.ClientSession, api_key: str, name: str, address: str) -> dict[str, Any]:
    """
    Create the forecast of a venue, it costs 2 credits (1 if the venue is not found).

    Returns venue_id, venue_name, venue_address and hours (see week_hours).
    """
    params = {"api_key_private": api_key, "venue_name": name, "venue_address": address}
    try:
        async with session.post(f"{API}/forecasts", params=params, timeout=TIMEOUT) as response:
            data = await response.json(content_type=None)
            status = response.status
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        raise BestTimeError(str(exc) or type(exc).__name__) from exc
    if not isinstance(data, dict) or status >= 400 or data.get("status") != "OK":
        message = data.get("message") if isinstance(data, dict) else None
        raise BestTimeError(str(message or f"HTTP {status}"))
    venue = data.get("venue_info") if isinstance(data.get("venue_info"), dict) else {}
    return {
        "venue_id": venue.get("venue_id"),
        "venue_name": venue.get("venue_name") or name,
        "venue_address": venue.get("venue_address") or address,
        "hours": week_hours(data.get("analysis")),
    }
