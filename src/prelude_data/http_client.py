"""Polite HTTP: identified User-Agent, global 1 req/sec throttle, retries."""

from __future__ import annotations

import logging
import time

import requests

from . import config

log = logging.getLogger(__name__)

_session: requests.Session | None = None
_last_request_at = 0.0


def session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(
            {
                "User-Agent": config.USER_AGENT,
                "Accept-Encoding": "gzip, deflate",
            }
        )
    return _session


def _throttle() -> None:
    global _last_request_at
    wait = config.MIN_SECONDS_BETWEEN_REQUESTS - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def get(url: str, *, ok_codes: tuple[int, ...] = (200,)) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(1, config.RETRIES + 1):
        _throttle()
        try:
            resp = session().get(url, timeout=config.REQUEST_TIMEOUT)
            if resp.status_code in ok_codes:
                return resp
            last_error = RuntimeError(f"HTTP {resp.status_code} for {url}")
            # 4xx (other than 429) won't improve with retries.
            if 400 <= resp.status_code < 500 and resp.status_code != 429:
                break
        except requests.RequestException as exc:  # DNS, timeout, reset...
            last_error = exc
        log.warning("attempt %d/%d failed for %s: %s", attempt, config.RETRIES, url, last_error)
        time.sleep(2**attempt)
    raise RuntimeError(f"GET failed after {config.RETRIES} attempts: {url}") from last_error


def get_text(url: str) -> str:
    return get(url).text


def get_json(url: str) -> dict:
    return get(url).json()
