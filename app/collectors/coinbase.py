"""
Coinbase Advanced Trade API collector.
Fetches OHLCV candles and latest ticker for each coin in WATCHLIST.

Requires:
  COINBASE_API_KEY    — from CDP portal (view permission only)
  COINBASE_API_SECRET — PEM private key string
  WATCHLIST           — comma-separated product IDs, e.g. "BTC-USD,ETH-USD"
"""
import os
import time
from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy.dialects.mysql import insert as mysql_insert

from app.collectors.base import BaseCollector
from app.database import db_session
from app.models import OHLCV

try:
    from coinbase.rest import RESTClient
except ImportError:
    RESTClient = None


GRANULARITY_MAP = {
    "1m":  "ONE_MINUTE",
    "5m":  "FIVE_MINUTE",
    "15m": "FIFTEEN_MINUTE",
    "1h":  "ONE_HOUR",
    "6h":  "SIX_HOUR",
    "1d":  "ONE_DAY",
}

# Seconds per granularity — used for pagination window sizing
GRAN_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900,
    "1h": 3600, "6h": 21600, "1d": 86400,
}

MAX_CANDLES_PER_REQUEST = 350


def _get_client() -> "RESTClient":
    if RESTClient is None:
        raise RuntimeError("coinbase-advanced-py not installed")
    return RESTClient(
        api_key=os.environ["COINBASE_API_KEY"],
        api_secret=os.environ["COINBASE_API_SECRET"],
    )


def _get_watchlist() -> list[str]:
    raw = os.getenv("WATCHLIST", "BTC-USD,ETH-USD,SOL-USD")
    return [s.strip() for s in raw.split(",") if s.strip()]


class CoinbaseOHLCVCollector(BaseCollector):
    """
    Fetches OHLCV candles from Coinbase for each symbol in WATCHLIST.
    Called by the Celery beat schedule.
    """
    name = "coinbase_ohlcv"

    def __init__(self, granularity: str = "1h", lookback_hours: int = 48):
        self.granularity = granularity
        self.lookback_hours = lookback_hours
        self.client = _get_client()

    def collect(self) -> int:
        symbols = _get_watchlist()
        total = 0
        for symbol in symbols:
            try:
                saved = self._fetch_and_save(symbol)
                total += saved
                time.sleep(0.2)  # stay under 10 req/sec rate limit
            except Exception as e:
                logger.error(f"[coinbase_ohlcv] {symbol} failed: {e}")
        return total

    def _fetch_and_save(self, symbol: str) -> int:
        gran_str = GRANULARITY_MAP.get(self.granularity, "ONE_HOUR")
        gran_secs = GRAN_SECONDS.get(self.granularity, 3600)

        end_ts = int(time.time())
        start_ts = int((datetime.utcnow() - timedelta(hours=self.lookback_hours)).timestamp())

        all_rows = []

        # Paginate: Coinbase max 350 candles per request
        cursor = start_ts
        while cursor < end_ts:
            page_end = min(cursor + MAX_CANDLES_PER_REQUEST * gran_secs, end_ts)

            try:
                resp = self.client.get_candles(
                    product_id=symbol,
                    start=str(cursor),
                    end=str(page_end),
                    granularity=gran_str,
                )
                candles = resp.candles or []
            except Exception as e:
                logger.warning(f"[coinbase_ohlcv] page fetch error {symbol}: {e}")
                break

            for c in candles:
                all_rows.append({
                    "symbol":      symbol,
                    "granularity": self.granularity,
                    "ts":          datetime.utcfromtimestamp(int(c.start)),
                    "open":        float(c.open),
                    "high":        float(c.high),
                    "low":         float(c.low),
                    "close":       float(c.close),
                    "volume":      float(c.volume),
                })

            cursor = page_end
            time.sleep(0.15)

        if not all_rows:
            return 0

        # Upsert — INSERT ... ON DUPLICATE KEY UPDATE (safe for re-runs)
        with db_session() as session:
            stmt = mysql_insert(OHLCV).values(all_rows)
            stmt = stmt.on_duplicate_key_update(
                open=stmt.inserted.open,
                high=stmt.inserted.high,
                low=stmt.inserted.low,
                close=stmt.inserted.close,
                volume=stmt.inserted.volume,
            )
            session.execute(stmt)

        logger.debug(f"[coinbase_ohlcv] {symbol} {self.granularity}: upserted {len(all_rows)} candles")
        return len(all_rows)


class CoinbaseTickerCollector(BaseCollector):
    """
    Fetches the latest trade price for each symbol.
    Runs every minute; primarily used for real-time dashboard updates
    and stop-loss monitoring.
    """
    name = "coinbase_ticker"

    def __init__(self):
        self.client = _get_client()

    def collect(self) -> int:
        symbols = _get_watchlist()
        tickers = []

        for symbol in symbols:
            try:
                info = self._fetch_product(symbol)
                if info:
                    tickers.append(info)
                time.sleep(0.1)
            except Exception as e:
                logger.error(f"[coinbase_ticker] {symbol} failed: {e}")

        # Store as 1-minute OHLCV rows (price = open=high=low=close, volume=0)
        # This gives the analysis pipeline something to work with between hourly candles
        if tickers:
            now = datetime.utcnow().replace(second=0, microsecond=0)
            rows = []
            for t in tickers:
                price = t["price"]
                rows.append({
                    "symbol":      t["symbol"],
                    "granularity": "1m",
                    "ts":          now,
                    "open":        price,
                    "high":        price,
                    "low":         price,
                    "close":       price,
                    "volume":      0,
                })

            with db_session() as session:
                stmt = mysql_insert(OHLCV).values(rows)
                stmt = stmt.on_duplicate_key_update(close=stmt.inserted.close)
                session.execute(stmt)

        return len(tickers)

    def _fetch_product(self, symbol: str) -> dict | None:
        resp = self.client.get_product(product_id=symbol)
        if not resp:
            return None
        return {
            "symbol":        symbol,
            "price":         float(resp.price),
            "volume_24h":    float(getattr(resp, "volume_24h", 0) or 0),
            "pct_change_24h": float(getattr(resp, "price_percentage_change_24h", 0) or 0),
        }

    @staticmethod
    def get_latest_prices() -> dict[str, float]:
        """
        Convenience method: returns {symbol: latest_close} for all watchlist coins.
        Used by the paper trading engine for stop-loss checks.
        """
        client = _get_client()
        prices = {}
        for symbol in _get_watchlist():
            try:
                resp = client.get_product(product_id=symbol)
                prices[symbol] = float(resp.price)
                time.sleep(0.1)
            except Exception as e:
                logger.warning(f"[coinbase_ticker] price fetch failed for {symbol}: {e}")
        return prices
