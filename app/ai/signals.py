"""
AI signal engine.
Assembles a structured market context prompt, calls the configured AI provider,
parses the JSON response, and saves the signal to MySQL.

Signal format returned by AI:
{
  "action":       "BUY" | "SELL" | "HOLD",
  "confidence":   0.0–1.0,
  "target_price": 123.45,   (optional)
  "stop_loss":    120.00,   (optional)
  "reasoning":    "..."
}
"""
import json
import os
import re
from datetime import datetime, timedelta

from loguru import logger

from app.ai.providers import get_provider
from app.analysis.indicators import get_signal_summary
from app.collectors.fear_greed import FearGreedCollector
from app.collectors.cryptopanic import CryptoPanicCollector
from app.database import db_session, get_engine
from app.models import Signal

import pandas as pd
from sqlalchemy import text


# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert cryptocurrency trading analyst.
You analyse market data and return precise, structured trading signals.

RULES:
- Respond ONLY with valid JSON — no markdown, no explanation outside the JSON
- confidence must be between 0.0 and 1.0
- action must be exactly "BUY", "SELL", or "HOLD"
- reasoning must be 2–4 concise sentences explaining the key factors
- If data is insufficient, return HOLD with confidence 0.5

RESPONSE FORMAT (strict):
{
  "action": "BUY",
  "confidence": 0.78,
  "target_price": 50000.00,
  "stop_loss": 47500.00,
  "reasoning": "RSI indicates oversold conditions at 28. MACD shows bullish crossover forming. Fear & Greed at 22 suggests extreme fear — historically a buy signal. Volume is increasing, supporting the reversal thesis."
}"""


# ── Prompt builder ────────────────────────────────────────────────────────────

def _load_recent_ohlcv(symbol: str, granularity: str = "1h", bars: int = 10) -> list[dict]:
    """Load the N most recent OHLCV candles for context."""
    engine = get_engine()
    query = text("""
        SELECT ts, open, high, low, close, volume
        FROM   ohlcv
        WHERE  symbol = :symbol AND granularity = :gran
        ORDER  BY ts DESC
        LIMIT  :limit
    """)
    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params={"symbol": symbol, "gran": granularity, "limit": bars})

    if df.empty:
        return []

    df = df.sort_values("ts")
    return df.to_dict("records")


def build_prompt(symbol: str) -> str:
    """
    Assembles a rich market context prompt for the AI provider.
    Includes: recent OHLCV, technical indicators, Fear & Greed, news headlines.
    """
    coin = symbol.split("-")[0]
    lines = [f"## Market Analysis Request: {symbol}\n"]

    # ── Recent price action ───────────────────────────────────────────────────
    candles = _load_recent_ohlcv(symbol, granularity="1h", bars=10)
    if candles:
        lines.append("### Recent OHLCV (last 10 hourly candles, newest last)")
        lines.append("timestamp | open | high | low | close | volume")
        for c in candles:
            ts = str(c["ts"])[:16]
            lines.append(
                f"{ts} | {float(c['open']):.2f} | {float(c['high']):.2f} | "
                f"{float(c['low']):.2f} | {float(c['close']):.2f} | {float(c['volume']):.2f}"
            )
        current_price = float(candles[-1]["close"])
        lines.append(f"\nCurrent price: ${current_price:,.2f}\n")
    else:
        lines.append("*No recent OHLCV data available.*\n")
        current_price = None

    # ── Technical indicators ──────────────────────────────────────────────────
    summary = get_signal_summary(symbol, granularity="1h")
    if summary.get("available"):
        lines.append("### Technical Indicators")
        for interp in summary.get("interpretations", []):
            lines.append(f"- {interp}")

        raw = summary.get("raw", {})
        lines.append(f"\nRaw values:")
        lines.append(f"  RSI(14)       = {_fmt(raw.get('rsi_14'))}")
        lines.append(f"  MACD          = {_fmt(raw.get('macd'))}")
        lines.append(f"  MACD Signal   = {_fmt(raw.get('macd_signal'))}")
        lines.append(f"  MACD Hist     = {_fmt(raw.get('macd_hist'))}")
        lines.append(f"  BB Upper      = {_fmt(raw.get('bb_upper'))}")
        lines.append(f"  BB Mid        = {_fmt(raw.get('bb_mid'))}")
        lines.append(f"  BB Lower      = {_fmt(raw.get('bb_lower'))}")
        lines.append(f"  EMA(12)       = {_fmt(raw.get('ema_12'))}")
        lines.append(f"  EMA(26)       = {_fmt(raw.get('ema_26'))}")
        lines.append(f"  ATR(14)       = {_fmt(raw.get('atr_14'))}")
        lines.append(f"  Volume SMA(20)= {_fmt(raw.get('volume_sma_20'))}\n")
    else:
        lines.append("*Technical indicators not yet available — insufficient data.*\n")

    # ── Fear & Greed index ────────────────────────────────────────────────────
    fg = FearGreedCollector.get_latest()
    if fg:
        lines.append(f"### Market Sentiment")
        lines.append(f"Fear & Greed Index: {fg['value']}/100 — {fg['label']}")
        if fg["value"] <= 25:
            lines.append("  → Extreme Fear: historically a strong buy signal")
        elif fg["value"] >= 75:
            lines.append("  → Extreme Greed: historically a sell/caution signal")
        lines.append("")

    # ── News headlines ────────────────────────────────────────────────────────
    headlines = CryptoPanicCollector.get_recent_headlines(symbol, limit=5)
    if headlines:
        lines.append("### Recent News Headlines")
        for h in headlines:
            sentiment_icon = {"positive": "↑", "negative": "↓", "neutral": "→"}.get(h["sentiment"], "→")
            lines.append(f"  {sentiment_icon} {h['title']} ({h['sentiment']})")
        lines.append("")

    # ── Final instruction ─────────────────────────────────────────────────────
    lines.append(f"Based on all the above data for {symbol}, provide your trading signal as JSON.")
    if current_price:
        lines.append(f"Set target_price and stop_loss relative to the current price of ${current_price:,.2f}.")

    return "\n".join(lines)


def _fmt(val) -> str:
    """Format a float for display in the prompt."""
    if val is None:
        return "N/A"
    try:
        return f"{float(val):.4f}"
    except (TypeError, ValueError):
        return "N/A"


# ── Response parser ───────────────────────────────────────────────────────────

def parse_signal(raw_text: str) -> dict | None:
    """
    Robustly parse the AI response into a signal dict.
    Handles markdown code fences, extra whitespace, trailing commas.
    """
    if not raw_text:
        return None

    # Strip markdown fences if present
    text = re.sub(r"```(?:json)?", "", raw_text).strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to extract the first {...} block
    match = re.search(r"\{.*?\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # Last resort: remove trailing commas (common LLM mistake) and retry
    cleaned = re.sub(r",\s*([}\]])", r"\1", text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        logger.error(f"[ai] Could not parse signal JSON:\n{raw_text[:500]}")
        return None


def validate_signal(signal: dict) -> bool:
    """Check the parsed signal has all required fields with valid values."""
    if not isinstance(signal, dict):
        return False
    if signal.get("action") not in ("BUY", "SELL", "HOLD"):
        return False
    try:
        conf = float(signal.get("confidence", -1))
        if not (0.0 <= conf <= 1.0):
            return False
    except (TypeError, ValueError):
        return False
    if not signal.get("reasoning"):
        return False
    return True


# ── Main signal generation ────────────────────────────────────────────────────

def generate_signal(symbol: str) -> dict | None:
    """
    Full pipeline: build prompt → call AI → parse → validate → save to DB.
    Returns the saved signal dict or None on failure.
    """
    provider = get_provider()
    logger.info(f"[ai] Generating signal for {symbol} via {provider.name()}")

    # Build the prompt
    prompt = build_prompt(symbol)

    # Call the AI provider
    try:
        raw_response, prompt_tokens, completion_tokens = provider.complete(
            system=SYSTEM_PROMPT,
            user=prompt,
            max_tokens=600,
        )
    except Exception as e:
        logger.error(f"[ai] Provider call failed for {symbol}: {e}")
        return None

    logger.debug(f"[ai] Raw response for {symbol}:\n{raw_response[:300]}")

    # Parse and validate
    signal = parse_signal(raw_response)
    if not signal or not validate_signal(signal):
        logger.error(f"[ai] Invalid signal for {symbol}: {signal}")
        return None

    # Save to DB
    with db_session() as session:
        row = Signal(
            symbol=symbol,
            ts=datetime.utcnow(),
            action=signal["action"],
            confidence=float(signal["confidence"]),
            target_price=signal.get("target_price"),
            stop_loss=signal.get("stop_loss"),
            reasoning=signal["reasoning"],
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        session.add(row)

    logger.info(
        f"[ai] {symbol} → {signal['action']} "
        f"(confidence={signal['confidence']:.0%}) | "
        f"tokens: {prompt_tokens}+{completion_tokens}"
    )
    return signal


def generate_all_signals() -> list[dict]:
    """Run signal generation for every symbol in WATCHLIST."""
    symbols = [s.strip() for s in os.getenv("WATCHLIST", "BTC-USD,ETH-USD,SOL-USD").split(",")]
    results = []
    for symbol in symbols:
        try:
            sig = generate_signal(symbol)
            if sig:
                results.append({"symbol": symbol, **sig})
        except Exception as e:
            logger.error(f"[ai] generate_signal failed for {symbol}: {e}")
    return results
