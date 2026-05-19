"""
CoinGecko collector — free tier, no API key required.
Fetches global market data, individual coin stats, and historical prices.

Rate limit (free): 50 calls/min  →  we sleep 1.5s between calls to be safe.
Optional: set COINGECKO_API_KEY for the paid "Demo" tier (500 calls/min).
"""
import os
import time
from datetime import datetime

import requests
from loguru import logger

from app.collectors.base import BaseCollector
from app.database import db_session
from app.models import OHLCV

# Map Coinbase product IDs → CoinGecko coin IDs
SYMBOL_TO_COINGECKO_ID = {
    "BTC-USD":  "bitcoin",
    "ETH-USD":  "ethereum",
    "SOL-USD":  "solana",
    "DOGE-USD": "dogecoin",
    "AVAX-USD": "avalanche-2",
    "LINK-USD": "chainlink",
    "MATIC-USD": "matic-network",
    "ADA-USD":  "cardano",
    "DOT-USD":  "polkadot",
    "UNI-USD":  "uniswap",
}


COINGECKO_FREE_URL = "https://api.coingecko.com/api/v3"

def _get_headers() -> dict:
    api_key = os.getenv("COINGECKO_API_KEY", "")
    if api_key:
        return {"x-cg-demo-api-key": api_key}
    return {}

def _get(endpoint: str, params: dict = None) -> dict | list:
    url = f"{COINGECKO_FREE_URL}/{endpoint}"
    resp = requests.get(url, headers=_get_headers(), params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _get_watchlist() -> list[str]:
    raw = os.getenv("WATCHLIST", "BTC-USD,ETH-USD,SOL-USD")
    return [s.strip() for s in raw.split(",") if s.strip()]


class CoinGeckoMarketCollector(BaseCollector):
    """
    Fetches current market data (price, volume, market cap, 24h change)
    for all watchlist coins.  Runs hourly.
    Stored as 1-hour OHLCV rows using the current price as close.
    """
    name = "coingecko_market"

    def collect(self) -> int:
        symbols = _get_watchlist()
        coin_ids = [
            SYMBOL_TO_COINGECKO_ID[s] for s in symbols
            if s in SYMBOL_TO_COINGECKO_ID
        ]

        if not coin_ids:
            logger.warning("[coingecko] no known coin IDs in watchlist")
            return 0

        try:
            data = _get("coins/markets", params={
                "vs_currency": "usd",
                "ids": ",".join(coin_ids),
                "order": "market_cap_desc",
                "per_page": 50,
                "page": 1,
                "sparkline": False,
                "price_change_percentage": "24h",
            })
        except Exception as e:
            logger.error(f"[coingecko] market fetch failed: {e}")
            raise

        # Reverse-map coingecko id → symbol
        id_to_symbol = {v: k for k, v in SYMBOL_TO_COINGECKO_ID.items()}
        now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)

        rows = []
        for coin in data:
            symbol = id_to_symbol.get(coin["id"])
            if not symbol:
                continue
            price = coin.get("current_price") or 0
            rows.append({
                "symbol":      symbol,
                "granularity": "1h",
                "ts":          now,
                "open":        coin.get("high_24h") or price,   # approximation
                "high":        coin.get("high_24h") or price,
                "low":         coin.get("low_24h") or price,
                "close":       price,
                "volume":      coin.get("total_volume") or 0,
            })

        if rows:
            from sqlalchemy.dialects.mysql import insert as mysql_insert
            with db_session() as session:
                stmt = mysql_insert(OHLCV).values(rows)
                stmt = stmt.on_duplicate_key_update(
                    close=stmt.inserted.close,
                    volume=stmt.inserted.volume,
                )
                session.execute(stmt)

        logger.debug(f"[coingecko] saved {len(rows)} market rows")
        return len(rows)


class CoinGeckoHistoricalCollector(BaseCollector):
    """
    Back-fills up to 90 days of daily OHLCV history for each coin.
    Runs once on startup / manually triggered.
    """
    name = "coingecko_historical"

    def __init__(self, days: int = 90):
        self.days = days

    def collect(self) -> int:
        symbols = _get_watchlist()
        total = 0
        for symbol in symbols:
            coin_id = SYMBOL_TO_COINGECKO_ID.get(symbol)
            if not coin_id:
                continue
            try:
                saved = self._fetch_history(symbol, coin_id)
                total += saved
                time.sleep(1.5)  # respect free-tier rate limit
            except Exception as e:
                logger.error(f"[coingecko_hist] {symbol} failed: {e}")
        return total

    def _fetch_history(self, symbol: str, coin_id: str) -> int:
        # Returns list of [timestamp_ms, open, high, low, close] OHLC
        ohlc_data = _get(f"coins/{coin_id}/ohlc", params={
            "vs_currency": "usd",
            "days": self.days,
        })

        if not ohlc_data:
            return 0

        rows = []
        for entry in ohlc_data:
            ts_ms, open_, high, low, close = entry
            rows.append({
                "symbol":      symbol,
                "granularity": "1d",
                "ts":          datetime.utcfromtimestamp(ts_ms / 1000),
                "open":        open_,
                "high":        high,
                "low":         low,
                "close":       close,
                "volume":      0,  # CoinGecko OHLC endpoint doesn't include volume
            })

        if rows:
            from sqlalchemy.dialects.mysql import insert as mysql_insert
            with db_session() as session:
                stmt = mysql_insert(OHLCV).values(rows)
                stmt = stmt.on_duplicate_key_update(
                    open=stmt.inserted.open,
                    high=stmt.inserted.high,
                    low=stmt.inserted.low,
                    close=stmt.inserted.close,
                )
                session.execute(stmt)

        logger.debug(f"[coingecko_hist] {symbol}: upserted {len(rows)} daily candles")
        return len(rows)
