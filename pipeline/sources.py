"""API fetcher clients for BTC Engine Room.

P1 scope: full-history BACKFILL fetchers -- CoinMetricsClient,
BlockchainInfoChartsClient, AlternativeMeFngClient (used once by backfill.py).

P2 scope: single-day LIVE fetchers used by fetch_snapshot.py's daily
failover chains -- MempoolSpaceClient, CoinGeckoClient, CoinbaseClient, plus
BlockchainInfoSimpleClient for the plain-text /q/ endpoints (distinct from
BlockchainInfoChartsClient's JSON charts API).

Every fetcher goes through request_with_retry(), which is the sole place
retry/backoff/User-Agent policy lives (CLAUDE.md Section 4: polite-client
rules are non-negotiable).
"""

from __future__ import annotations

import random
import time
from datetime import datetime, timezone
from typing import Any

import requests

USER_AGENT = "btc-engine-room/1.0 (+https://github.com/fandamentals/bitcoin-engine-room)"
DEFAULT_TIMEOUT = 15
DEFAULT_MAX_RETRIES = 3
MAX_RETRY_AFTER_SECONDS = 60


class SourceFetchError(Exception):
    """Raised when a source is exhausted after all retries."""

    def __init__(self, source_name: str, endpoint: str, last_error: str):
        self.source_name = source_name
        self.endpoint = endpoint
        self.last_error = last_error
        super().__init__(f"{source_name} exhausted retries fetching {endpoint}: {last_error}")


def request_with_retry(
    method: str,
    url: str,
    *,
    source_name: str,
    params: dict[str, Any] | None = None,
    extra_headers: dict[str, str] | None = None,
    json_body: dict | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    sleep_fn=time.sleep,
) -> requests.Response:
    """Fetch a URL with polite-client retry/backoff discipline.

    Retries on 429 (honoring Retry-After) and on 5xx/connection/timeout
    errors, with exponential backoff plus jitter. Raises SourceFetchError
    once max_retries is exhausted. `extra_headers` merges on top of the
    mandatory User-Agent (used by pipeline/gh_issues.py for GitHub's auth
    header); `json_body` is passed through for POST/PATCH calls.
    """
    headers = {"User-Agent": USER_AGENT, **(extra_headers or {})}
    last_error = "unknown error"

    for attempt in range(max_retries + 1):
        try:
            response = requests.request(
                method, url, params=params, headers=headers, timeout=timeout, json=json_body
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_error = str(exc)
            if attempt >= max_retries:
                break
            _sleep_backoff(attempt, sleep_fn)
            continue

        if response.status_code == 429:
            last_error = "HTTP 429 rate limited"
            if attempt >= max_retries:
                break
            retry_after = response.headers.get("Retry-After")
            delay = _parse_retry_after(retry_after) if retry_after else _backoff_delay(attempt)
            sleep_fn(min(delay, MAX_RETRY_AFTER_SECONDS))
            continue

        if response.status_code >= 500:
            last_error = f"HTTP {response.status_code}"
            if attempt >= max_retries:
                break
            _sleep_backoff(attempt, sleep_fn)
            continue

        response.raise_for_status()
        return response

    raise SourceFetchError(source_name, url, last_error)


def _backoff_delay(attempt: int) -> float:
    return (2**attempt) + random.uniform(0, 0.5)


def _sleep_backoff(attempt: int, sleep_fn) -> None:
    sleep_fn(_backoff_delay(attempt))


def _parse_retry_after(value: str) -> float:
    try:
        return float(value)
    except ValueError:
        return DEFAULT_MAX_RETRIES * 2.0


def _to_date_str(timestamp) -> str:
    """Normalize a unix timestamp (int/float/str seconds) to a UTC date string."""
    return datetime.fromtimestamp(int(float(timestamp)), tz=timezone.utc).strftime("%Y-%m-%d")


def _today_utc_date() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")


class CoinMetricsClient:
    """Coin Metrics Community API v4 -- no key required for community endpoints."""

    BASE_URL = "https://api.coinmetrics.io/v4/timeseries/asset-metrics"
    SOURCE_NAME = "coinmetrics"

    def fetch_asset_metrics(
        self,
        metrics: list[str],
        start_time: str,
        *,
        asset: str = "btc",
        page_size: int = 10000,
        request_fn=request_with_retry,
    ) -> list[dict]:
        """Fetch one or more daily metrics for `asset` from `start_time` onward.

        Follows `next_page_url` until exhausted. Returns the raw concatenated
        `data` rows (each row is a dict keyed by metric name plus `time`);
        callers split this into per-metric series and drop nulls per metric.
        """
        params = {
            "assets": asset,
            "metrics": ",".join(metrics),
            "frequency": "1d",
            "page_size": page_size,
            "start_time": start_time,
        }
        rows: list[dict] = []
        url = self.BASE_URL
        first_request = True

        while url:
            response = request_fn(
                "GET",
                url,
                source_name=self.SOURCE_NAME,
                params=params if first_request else None,
            )
            first_request = False
            payload = response.json()
            rows.extend(payload.get("data", []))
            url = payload.get("next_page_url") or None

        return rows

    @staticmethod
    def split_metric_series(rows: list[dict], metric: str) -> list[dict]:
        """Extract one metric's series from CoinMetrics' combined rows, dropping nulls."""
        series = []
        for row in rows:
            raw_value = row.get(metric)
            if raw_value is None:
                continue
            series.append(
                {
                    "date": row["time"][:10],
                    "value": float(raw_value),
                    "source": CoinMetricsClient.SOURCE_NAME,
                }
            )
        return series


class BlockchainInfoChartsClient:
    """blockchain.com Charts API -- free JSON, full history, no key."""

    BASE_URL = "https://api.blockchain.info/charts/{chart}"
    SOURCE_NAME = "blockchain_info"

    # Native units returned by blockchain.info; converted to canonical units on parse.
    # Verified live (2026-07): the API's own "unit" field reports "Hash Rate TH/s",
    # not GH/s -- confirmed against a real network-hashrate magnitude check.
    UNIT_CONVERSIONS = {
        "hash-rate": lambda th_s: th_s / 1e6,  # TH/s -> EH/s
    }

    def fetch_chart(self, chart: str, *, request_fn=request_with_retry) -> list[dict]:
        url = self.BASE_URL.format(chart=chart)
        response = request_fn(
            "GET",
            url,
            source_name=self.SOURCE_NAME,
            params={"timespan": "all", "format": "json", "sampled": "false"},
        )
        payload = response.json()
        return self.parse_chart_values(chart, payload.get("values", []))

    @classmethod
    def parse_chart_values(cls, chart: str, values: list[dict]) -> list[dict]:
        convert = cls.UNIT_CONVERSIONS.get(chart, lambda v: v)
        rows = []
        for point in values:
            rows.append(
                {
                    "date": _to_date_str(point["x"]),
                    "value": convert(float(point["y"])),
                    "source": cls.SOURCE_NAME,
                }
            )
        # Some charts (e.g. total-bitcoins) are natively per-block, not per-day,
        # even with sampled=false. Collapse to one row per date, keeping the
        # last (latest) value of that UTC day -- a no-op for genuinely daily
        # charts, essential for higher-frequency ones.
        return cls._dedupe_keep_last_per_date(rows)

    @staticmethod
    def _dedupe_keep_last_per_date(rows: list[dict]) -> list[dict]:
        by_date = {row["date"]: row for row in rows}
        return sorted(by_date.values(), key=lambda r: r["date"])


class AlternativeMeFngClient:
    """alternative.me Fear & Greed index -- free JSON, no key.

    Full history via `?limit=0`. Data begins ~2018-02-01, not Bitcoin's genesis.
    """

    URL = "https://api.alternative.me/fng/"
    SOURCE_NAME = "alternative_me"

    def fetch_full_history(self, *, request_fn=request_with_retry) -> list[dict]:
        response = request_fn(
            "GET",
            self.URL,
            source_name=self.SOURCE_NAME,
            params={"limit": 0, "format": "json"},
        )
        payload = response.json()
        return self.parse_fng_data(payload.get("data", []))

    @classmethod
    def parse_fng_data(cls, data: list[dict]) -> list[dict]:
        rows = []
        for entry in data:
            rows.append(
                {
                    "date": _to_date_str(entry["timestamp"]),
                    "value": int(entry["value"]),
                    "classification": entry["value_classification"],
                    "source": cls.SOURCE_NAME,
                }
            )
        return rows

    def fetch_latest(self, *, request_fn=request_with_retry) -> dict:
        """Fetch just today's reading, for the daily snapshot (P2)."""
        response = request_fn(
            "GET", self.URL, source_name=self.SOURCE_NAME, params={"limit": 1, "format": "json"}
        )
        rows = self.parse_fng_data(response.json().get("data", []))
        return rows[0]


class MempoolSpaceClient:
    """mempool.space -- free REST + WebSocket, no auth. P2 (live snapshot) scope.

    REST polling only here; the browser's WebSocket connection (Section 7,
    P3) is separate and out of scope for the Python pipeline.
    """

    BASE_URL = "https://mempool.space/api"
    SOURCE_NAME = "mempool_space"

    def fetch_price(self, *, request_fn=request_with_retry) -> dict:
        response = request_fn(
            "GET", f"{self.BASE_URL}/v1/prices", source_name=self.SOURCE_NAME
        )
        payload = response.json()
        return {"date": _today_utc_date(), "value": float(payload["USD"]), "source": self.SOURCE_NAME}

    def fetch_hashrate(self, *, request_fn=request_with_retry) -> dict:
        response = request_fn(
            "GET", f"{self.BASE_URL}/v1/mining/hashrate/3d", source_name=self.SOURCE_NAME
        )
        payload = response.json()
        hash_per_sec = float(payload["currentHashrate"])
        return {"date": _today_utc_date(), "value": hash_per_sec / 1e18, "source": self.SOURCE_NAME}

    def fetch_difficulty(self, *, request_fn=request_with_retry) -> dict:
        # Live-verified 2026-07: /v1/difficulty-adjustment returns retarget
        # PROGRESS fields (progressPercent, difficultyChange, ETA...) but no
        # raw current-difficulty value -- that's only in /v1/mining/hashrate/3d's
        # `currentDifficulty` field, despite the name of that endpoint.
        response = request_fn(
            "GET", f"{self.BASE_URL}/v1/mining/hashrate/3d", source_name=self.SOURCE_NAME
        )
        payload = response.json()
        return {
            "date": _today_utc_date(),
            "value": float(payload["currentDifficulty"]),
            "source": self.SOURCE_NAME,
        }

    def fetch_tip_height(self, *, request_fn=request_with_retry) -> int:
        response = request_fn(
            "GET", f"{self.BASE_URL}/blocks/tip/height", source_name=self.SOURCE_NAME
        )
        return int(response.text)


class CoinGeckoClient:
    """CoinGecko keyless public price endpoint. Attribution required (CLAUDE.md)."""

    URL = "https://api.coingecko.com/api/v3/simple/price"
    SOURCE_NAME = "coingecko"

    def fetch_price(self, *, request_fn=request_with_retry) -> dict:
        response = request_fn(
            "GET",
            self.URL,
            source_name=self.SOURCE_NAME,
            params={"ids": "bitcoin", "vs_currencies": "usd"},
        )
        value = response.json()["bitcoin"]["usd"]
        return {"date": _today_utc_date(), "value": float(value), "source": self.SOURCE_NAME}


class CoinbaseClient:
    """Coinbase public spot-price endpoint, no auth."""

    URL = "https://api.coinbase.com/v2/prices/BTC-USD/spot"
    SOURCE_NAME = "coinbase"

    def fetch_price(self, *, request_fn=request_with_retry) -> dict:
        response = request_fn("GET", self.URL, source_name=self.SOURCE_NAME)
        value = response.json()["data"]["amount"]
        return {"date": _today_utc_date(), "value": float(value), "source": self.SOURCE_NAME}


class BlockchainInfoSimpleClient:
    """blockchain.info's plain-text /q/ endpoints (distinct from the JSON Charts API)."""

    BASE_URL = "https://blockchain.info/q"
    SOURCE_NAME = "blockchain_info"

    def fetch_difficulty(self, *, request_fn=request_with_retry) -> dict:
        response = request_fn(
            "GET", f"{self.BASE_URL}/getdifficulty", source_name=self.SOURCE_NAME
        )
        return {"date": _today_utc_date(), "value": float(response.text), "source": self.SOURCE_NAME}

    def fetch_total_supply(self, *, request_fn=request_with_retry) -> dict:
        response = request_fn("GET", f"{self.BASE_URL}/totalbc", source_name=self.SOURCE_NAME)
        satoshis = float(response.text)
        return {"date": _today_utc_date(), "value": satoshis / 1e8, "source": self.SOURCE_NAME}
