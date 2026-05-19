"""
Celery application — broker, beat schedule, and all task definitions.

Beat schedule (what runs when):
  Every 1 minute  : coinbase_ticker_task   — live price snapshot
  Every 15 minutes: cryptopanic_task       — news headlines
  Every 1 hour    : coinbase_ohlcv_1h_task — hourly candles
  Every 1 hour    : coingecko_market_task  — market cap / volume
  Every 1 hour    : fear_greed_task        — sentiment index
  Every 1 day     : coinbase_ohlcv_1d_task — daily candles
"""
import os

from celery import Celery
from celery.schedules import crontab
from dotenv import load_dotenv

load_dotenv()

app = Celery(
    "crypto_trader",
    broker=os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0"),
    backend=os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/1"),
    include=["celery_app"],
)

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,           # re-queue on worker crash
    worker_prefetch_multiplier=1,  # one task at a time per worker slot
    result_expires=3600,
    # Routing: keep data collection separate from analysis/trading
    task_routes={
        "celery_app.coinbase_ticker_task":   {"queue": "data"},
        "celery_app.coinbase_ohlcv_1h_task": {"queue": "data"},
        "celery_app.coinbase_ohlcv_1d_task": {"queue": "data"},
        "celery_app.coingecko_market_task":  {"queue": "data"},
        "celery_app.fear_greed_task":        {"queue": "data"},
        "celery_app.cryptopanic_task":       {"queue": "data"},
        "celery_app.backfill_history_task":  {"queue": "data"},
    },
    beat_schedule={
        # ── 1-minute: live ticker snapshot ────────────────────────────────────
        "coinbase-ticker-every-minute": {
            "task":     "celery_app.coinbase_ticker_task",
            "schedule": 60.0,
        },
        # ── 15-minute: news ───────────────────────────────────────────────────
        "cryptopanic-every-15min": {
            "task":     "celery_app.cryptopanic_task",
            "schedule": crontab(minute="*/15"),
        },
        # ── Hourly: candles + market data + sentiment ─────────────────────────
        "coinbase-ohlcv-1h-hourly": {
            "task":     "celery_app.coinbase_ohlcv_1h_task",
            "schedule": crontab(minute=2),  # 2 min past the hour (candle closes)
        },
        "coingecko-market-hourly": {
            "task":     "celery_app.coingecko_market_task",
            "schedule": crontab(minute=5),
        },
        "fear-greed-hourly": {
            "task":     "celery_app.fear_greed_task",
            "schedule": crontab(minute=10),
        },
        # ── Daily: daily candles ──────────────────────────────────────────────
        "coinbase-ohlcv-1d-daily": {
            "task":     "celery_app.coinbase_ohlcv_1d_task",
            "schedule": crontab(hour=0, minute=30),  # 00:30 UTC
        },
    },
)


# =============================================================================
# Task definitions
# =============================================================================

@app.task(bind=True, max_retries=3, default_retry_delay=60, name="celery_app.coinbase_ticker_task")
def coinbase_ticker_task(self):
    """Fetch latest prices from Coinbase — every minute."""
    try:
        from app.collectors.coinbase import CoinbaseTickerCollector
        return CoinbaseTickerCollector().run()
    except Exception as exc:
        raise self.retry(exc=exc)


@app.task(bind=True, max_retries=3, default_retry_delay=120, name="celery_app.coinbase_ohlcv_1h_task")
def coinbase_ohlcv_1h_task(self):
    """Fetch hourly OHLCV candles from Coinbase — every hour."""
    try:
        from app.collectors.coinbase import CoinbaseOHLCVCollector
        return CoinbaseOHLCVCollector(granularity="1h", lookback_hours=48).run()
    except Exception as exc:
        raise self.retry(exc=exc)


@app.task(bind=True, max_retries=3, default_retry_delay=300, name="celery_app.coinbase_ohlcv_1d_task")
def coinbase_ohlcv_1d_task(self):
    """Fetch daily OHLCV candles from Coinbase — once a day."""
    try:
        from app.collectors.coinbase import CoinbaseOHLCVCollector
        return CoinbaseOHLCVCollector(granularity="1d", lookback_hours=24 * 30).run()
    except Exception as exc:
        raise self.retry(exc=exc)


@app.task(bind=True, max_retries=2, default_retry_delay=60, name="celery_app.coingecko_market_task")
def coingecko_market_task(self):
    """Fetch market data from CoinGecko — every hour."""
    try:
        from app.collectors.coingecko import CoinGeckoMarketCollector
        return CoinGeckoMarketCollector().run()
    except Exception as exc:
        raise self.retry(exc=exc)


@app.task(bind=True, max_retries=2, default_retry_delay=120, name="celery_app.fear_greed_task")
def fear_greed_task(self):
    """Fetch Fear & Greed index — every hour."""
    try:
        from app.collectors.fear_greed import FearGreedCollector
        return FearGreedCollector().run()
    except Exception as exc:
        raise self.retry(exc=exc)


@app.task(bind=True, max_retries=2, default_retry_delay=120, name="celery_app.cryptopanic_task")
def cryptopanic_task(self):
    """Fetch latest crypto news — every 15 minutes."""
    try:
        from app.collectors.cryptopanic import CryptoPanicCollector
        return CryptoPanicCollector().run()
    except Exception as exc:
        raise self.retry(exc=exc)


@app.task(bind=True, max_retries=1, name="celery_app.backfill_history_task")
def backfill_history_task(self, days: int = 90):
    """
    One-time back-fill of 90 days of daily history via CoinGecko.
    Trigger manually: celery_app.backfill_history_task.delay(days=90)
    """
    try:
        from app.collectors.coingecko import CoinGeckoHistoricalCollector
        return CoinGeckoHistoricalCollector(days=days).run()
    except Exception as exc:
        raise self.retry(exc=exc)


if __name__ == "__main__":
    app.start()


# =============================================================================
# Step 3 & 4 — Analysis + AI signal tasks (appended)
# =============================================================================

# ── Add routes for new queues ─────────────────────────────────────────────────
app.conf.task_routes.update({
    "celery_app.run_analysis_task":      {"queue": "analysis"},
    "celery_app.generate_signals_task":  {"queue": "analysis"},
})

# ── Add to beat schedule ──────────────────────────────────────────────────────
app.conf.beat_schedule.update({
    # Technical analysis runs at :20 past each hour (after candles + market data arrive)
    "technical-analysis-hourly": {
        "task":     "celery_app.run_analysis_task",
        "schedule": crontab(minute=20),
        "args":     ("1h",),
    },
    # AI signals run at :30 (after analysis is ready)
    "ai-signals-hourly": {
        "task":     "celery_app.generate_signals_task",
        "schedule": crontab(minute=30),
    },
})


@app.task(bind=True, max_retries=2, default_retry_delay=60, name="celery_app.run_analysis_task")
def run_analysis_task(self, granularity: str = "1h"):
    """Compute RSI, MACD, Bollinger etc. for all watchlist coins."""
    try:
        from app.analysis.indicators import run_analysis
        return run_analysis(granularity=granularity)
    except Exception as exc:
        raise self.retry(exc=exc)


@app.task(bind=True, max_retries=2, default_retry_delay=120, name="celery_app.generate_signals_task")
def generate_signals_task(self):
    """Call AI provider and generate buy/sell/hold signals for all coins."""
    try:
        from app.ai.signals import generate_all_signals
        results = generate_all_signals()
        return len(results)
    except Exception as exc:
        raise self.retry(exc=exc)


@app.task(bind=True, max_retries=1, name="celery_app.generate_single_signal_task")
def generate_single_signal_task(self, symbol: str):
    """Generate a signal for one coin on demand — useful for testing."""
    try:
        from app.ai.signals import generate_signal
        return generate_signal(symbol)
    except Exception as exc:
        raise self.retry(exc=exc)
