"""
CryptoPanic news collector — free API key from cryptopanic.com/api.
Fetches latest news headlines, sentiment votes, and relevant currencies.
Runs every 15 minutes.
"""
import os
from datetime import datetime

import requests
from loguru import logger

from app.collectors.base import BaseCollector
from app.database import db_session
from app.models import NewsEvent

API_BASE = "https://cryptopanic.com/api/v1/posts/"


def _get_watchlist_currencies() -> list[str]:
    """Extract coin names from the WATCHLIST env (BTC-USD → BTC)."""
    raw = os.getenv("WATCHLIST", "BTC-USD,ETH-USD,SOL-USD")
    return [s.split("-")[0] for s in raw.split(",") if s.strip()]


class CryptoPanicCollector(BaseCollector):
    name = "cryptopanic"

    def __init__(self):
        self.api_key = os.getenv("CRYPTOPANIC_API_KEY", "")
        if not self.api_key:
            logger.warning("[cryptopanic] no API key set — collector will be skipped")

    def collect(self) -> int:
        if not self.api_key:
            return 0

        currencies = _get_watchlist_currencies()
        saved = 0

        for currency in currencies:
            try:
                count = self._fetch_for_currency(currency)
                saved += count
            except Exception as e:
                logger.error(f"[cryptopanic] {currency} failed: {e}")

        return saved

    def _fetch_for_currency(self, currency: str) -> int:
        params = {
            "auth_token":  self.api_key,
            "currencies":  currency,
            "filter":      "hot",          # hot | rising | bullish | bearish | important
            "public":      "true",
            "kind":        "news",
        }

        try:
            resp = requests.get(API_BASE, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"[cryptopanic] HTTP error for {currency}: {e}")
            raise

        results = data.get("results", [])
        saved = 0

        with db_session() as session:
            for item in results:
                source_id = str(item.get("id", ""))
                if not source_id:
                    continue

                # Skip already-stored news
                exists = session.query(NewsEvent).filter_by(source_id=source_id).first()
                if exists:
                    continue

                # Parse published_at
                pub_str = item.get("published_at", "")
                try:
                    published_at = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                    published_at = published_at.replace(tzinfo=None)  # store as UTC naive
                except Exception:
                    published_at = datetime.utcnow()

                # Extract currencies mentioned
                currencies_list = [
                    c.get("code", "") for c in item.get("currencies", [])
                ]
                currencies_str = ",".join(filter(None, currencies_list))

                # Sentiment from votes
                votes = item.get("votes", {})
                pos = int(votes.get("positive", 0) or 0)
                neg = int(votes.get("negative", 0) or 0)
                if pos > neg * 2:
                    sentiment = "positive"
                elif neg > pos * 2:
                    sentiment = "negative"
                else:
                    sentiment = "neutral"

                session.add(NewsEvent(
                    source_id=source_id,
                    title=item.get("title", "")[:2000],
                    url=(item.get("url") or "")[:1000],
                    published_at=published_at,
                    currencies=currencies_str,
                    sentiment=sentiment,
                    votes_positive=pos,
                    votes_negative=neg,
                ))
                saved += 1

        if saved:
            logger.debug(f"[cryptopanic] {currency}: saved {saved} new articles")
        return saved

    @staticmethod
    def get_recent_headlines(symbol: str, limit: int = 5) -> list[dict]:
        """
        Returns recent headlines for a symbol — used by the AI prompt builder.
        symbol: Coinbase format, e.g. "BTC-USD"
        """
        currency = symbol.split("-")[0]
        with db_session() as session:
            rows = (
                session.query(NewsEvent)
                .filter(NewsEvent.currencies.like(f"%{currency}%"))
                .order_by(NewsEvent.published_at.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "title":     r.title,
                    "sentiment": r.sentiment,
                    "published": str(r.published_at),
                    "votes_pos": r.votes_positive,
                    "votes_neg": r.votes_negative,
                }
                for r in rows
            ]
