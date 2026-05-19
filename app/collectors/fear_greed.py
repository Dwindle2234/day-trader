"""
Fear & Greed index collector — alternative.me API (completely free, no key).
https://alternative.me/crypto/fear-and-greed-index/

Runs hourly. Stores current index value and label.
"""
from datetime import datetime

import requests
from loguru import logger

from app.collectors.base import BaseCollector
from app.database import db_session
from app.models import MarketSentiment

API_URL = "https://api.alternative.me/fng/?limit=1&format=json"


class FearGreedCollector(BaseCollector):
    name = "fear_greed"

    def collect(self) -> int:
        try:
            resp = requests.get(API_URL, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"[fear_greed] fetch failed: {e}")
            raise

        entries = data.get("data", [])
        if not entries:
            logger.warning("[fear_greed] empty response")
            return 0

        entry = entries[0]
        value = int(entry.get("value", 0))
        label = entry.get("value_classification", "Unknown")
        ts    = datetime.utcfromtimestamp(int(entry.get("timestamp", 0)))

        with db_session() as session:
            # Avoid duplicate for same hour
            existing = session.query(MarketSentiment).filter(
                MarketSentiment.ts == ts
            ).first()

            if not existing:
                session.add(MarketSentiment(
                    ts=ts,
                    fear_greed_value=value,
                    fear_greed_label=label,
                ))
                logger.info(f"[fear_greed] index={value} ({label}) at {ts}")
                return 1

        logger.debug(f"[fear_greed] already have entry for {ts}, skipping")
        return 0

    @staticmethod
    def get_latest() -> dict | None:
        """Quick helper for the AI engine to pull the latest value."""
        try:
            resp = requests.get(API_URL, timeout=8)
            data = resp.json()
            entry = data["data"][0]
            return {
                "value": int(entry["value"]),
                "label": entry["value_classification"],
            }
        except Exception as e:
            logger.warning(f"[fear_greed] get_latest failed: {e}")
            return None
