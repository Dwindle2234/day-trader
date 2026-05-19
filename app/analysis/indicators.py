"""
Technical analysis pipeline.
Reads OHLCV rows from MySQL, computes indicators using pandas-ta,
and upserts results back into the indicators table.

Indicators computed:
  RSI(14), MACD(12,26,9), Bollinger Bands(20,2),
  EMA(12), EMA(26), ATR(14), Volume SMA(20)
"""
import os
from datetime import datetime, timedelta

import pandas as pd
import pandas_ta as ta
from loguru import logger
from sqlalchemy import text
from sqlalchemy.dialects.mysql import insert as mysql_insert

from app.database import db_session, get_engine
from app.models import OHLCV, Indicator


def _get_watchlist() -> list[str]:
    raw = os.getenv("WATCHLIST", "BTC-USD,ETH-USD,SOL-USD")
    return [s.strip() for s in raw.split(",") if s.strip()]


def _load_ohlcv(symbol: str, granularity: str, lookback_bars: int = 200) -> pd.DataFrame:
    """Load the most recent N candles for a symbol/granularity into a DataFrame."""
    engine = get_engine()
    query = text("""
        SELECT ts, open, high, low, close, volume
        FROM   ohlcv
        WHERE  symbol      = :symbol
          AND  granularity = :gran
        ORDER  BY ts DESC
        LIMIT  :limit
    """)
    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params={
            "symbol": symbol,
            "gran":   granularity,
            "limit":  lookback_bars,
        })

    if df.empty:
        return df

    df = df.sort_values("ts").reset_index(drop=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute all technical indicators on a OHLCV DataFrame.
    Returns a new DataFrame with indicator columns appended.
    Requires at least 26 rows for MACD; fewer rows = NaN for that indicator.
    """
    if df.empty or len(df) < 5:
        return df

    # ── RSI(14) ───────────────────────────────────────────────────────────────
    rsi = ta.rsi(df["close"], length=14)
    df["rsi_14"] = rsi

    # ── MACD(12, 26, 9) ───────────────────────────────────────────────────────
    macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
    if macd is not None and not macd.empty:
        df["macd"]        = macd.get("MACD_12_26_9")
        df["macd_signal"] = macd.get("MACDs_12_26_9")
        df["macd_hist"]   = macd.get("MACDh_12_26_9")
    else:
        df["macd"] = df["macd_signal"] = df["macd_hist"] = None

    # ── Bollinger Bands(20, 2) ────────────────────────────────────────────────
    bb = ta.bbands(df["close"], length=20, std=2)
    if bb is not None and not bb.empty:
        df["bb_upper"] = bb.get("BBU_20_2.0")
        df["bb_mid"]   = bb.get("BBM_20_2.0")
        df["bb_lower"] = bb.get("BBL_20_2.0")
    else:
        df["bb_upper"] = df["bb_mid"] = df["bb_lower"] = None

    # ── EMA(12) and EMA(26) ───────────────────────────────────────────────────
    df["ema_12"] = ta.ema(df["close"], length=12)
    df["ema_26"] = ta.ema(df["close"], length=26)

    # ── ATR(14) ───────────────────────────────────────────────────────────────
    atr = ta.atr(df["high"], df["low"], df["close"], length=14)
    df["atr_14"] = atr

    # ── Volume SMA(20) ────────────────────────────────────────────────────────
    df["volume_sma_20"] = ta.sma(df["volume"], length=20)

    return df


def _safe_float(val) -> float | None:
    """Convert pandas/numpy scalar to Python float, returning None for NaN."""
    try:
        f = float(val)
        return None if (f != f) else f   # NaN check: NaN != NaN
    except (TypeError, ValueError):
        return None


def save_indicators(symbol: str, granularity: str, df: pd.DataFrame) -> int:
    """Upsert computed indicator rows into the indicators table."""
    if df.empty:
        return 0

    rows = []
    indicator_cols = [
        "rsi_14", "macd", "macd_signal", "macd_hist",
        "bb_upper", "bb_mid", "bb_lower",
        "ema_12", "ema_26", "atr_14", "volume_sma_20",
    ]

    for _, row in df.iterrows():
        # Skip rows where all indicators are NaN (not enough history yet)
        values = {col: _safe_float(row.get(col)) for col in indicator_cols}
        if all(v is None for v in values.values()):
            continue

        rows.append({
            "symbol":      symbol,
            "granularity": granularity,
            "ts":          row["ts"],
            **values,
        })

    if not rows:
        return 0

    with db_session() as session:
        stmt = mysql_insert(Indicator).values(rows)
        stmt = stmt.on_duplicate_key_update(
            rsi_14=stmt.inserted.rsi_14,
            macd=stmt.inserted.macd,
            macd_signal=stmt.inserted.macd_signal,
            macd_hist=stmt.inserted.macd_hist,
            bb_upper=stmt.inserted.bb_upper,
            bb_mid=stmt.inserted.bb_mid,
            bb_lower=stmt.inserted.bb_lower,
            ema_12=stmt.inserted.ema_12,
            ema_26=stmt.inserted.ema_26,
            atr_14=stmt.inserted.atr_14,
            volume_sma_20=stmt.inserted.volume_sma_20,
        )
        session.execute(stmt)

    return len(rows)


def run_analysis(granularity: str = "1h") -> int:
    """
    Main entry point called by Celery.
    Runs the full analysis pipeline for every symbol in WATCHLIST.
    Returns total indicator rows saved.
    """
    symbols = _get_watchlist()
    total = 0

    for symbol in symbols:
        try:
            df = _load_ohlcv(symbol, granularity, lookback_bars=200)
            if df.empty:
                logger.warning(f"[analysis] No OHLCV data for {symbol} {granularity} — skipping")
                continue

            df = compute_indicators(df)
            saved = save_indicators(symbol, granularity, df)
            total += saved
            logger.debug(f"[analysis] {symbol} {granularity}: saved {saved} indicator rows")

        except Exception as e:
            logger.error(f"[analysis] {symbol} {granularity} failed: {e}")

    logger.info(f"[analysis] {granularity} run complete — {total} rows saved across {len(symbols)} symbols")
    return total


def get_latest_indicators(symbol: str, granularity: str = "1h") -> dict | None:
    """
    Returns the most recent indicator row for a symbol as a plain dict.
    Used by the AI signal engine to build its prompt.
    """
    engine = get_engine()
    query = text("""
        SELECT *
        FROM   indicators
        WHERE  symbol      = :symbol
          AND  granularity = :gran
        ORDER  BY ts DESC
        LIMIT  1
    """)
    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params={"symbol": symbol, "gran": granularity})

    if df.empty:
        return None

    row = df.iloc[0].to_dict()
    # Convert Decimal/numpy types to plain Python floats
    return {k: _safe_float(v) if k not in ("symbol", "granularity", "ts") else v
            for k, v in row.items()}


def get_signal_summary(symbol: str, granularity: str = "1h") -> dict:
    """
    Returns a human-readable interpretation of the latest indicators.
    Used to augment the AI prompt with pre-interpreted signals.
    """
    ind = get_latest_indicators(symbol, granularity)
    if not ind:
        return {"available": False}

    signals = {"available": True, "raw": ind, "interpretations": []}
    interp = signals["interpretations"]

    # RSI interpretation
    rsi = ind.get("rsi_14")
    if rsi is not None:
        if rsi < 30:
            interp.append(f"RSI={rsi:.1f} → OVERSOLD (potential buy)")
        elif rsi > 70:
            interp.append(f"RSI={rsi:.1f} → OVERBOUGHT (potential sell)")
        else:
            interp.append(f"RSI={rsi:.1f} → neutral")

    # MACD interpretation
    macd = ind.get("macd")
    macd_sig = ind.get("macd_signal")
    macd_hist = ind.get("macd_hist")
    if macd is not None and macd_sig is not None:
        cross = "bullish crossover" if macd > macd_sig else "bearish crossover"
        hist_dir = "expanding" if (macd_hist or 0) > 0 else "contracting"
        interp.append(f"MACD={macd:.4f} signal={macd_sig:.4f} → {cross}, histogram {hist_dir}")

    # Bollinger Band interpretation
    bb_upper = ind.get("bb_upper")
    bb_lower = ind.get("bb_lower")
    bb_mid   = ind.get("bb_mid")
    close    = ind.get("close") if "close" in ind else None

    if bb_upper and bb_lower and bb_mid:
        bb_width = ((bb_upper - bb_lower) / bb_mid * 100) if bb_mid else 0
        interp.append(f"Bollinger upper={bb_upper:.2f} mid={bb_mid:.2f} lower={bb_lower:.2f} width={bb_width:.1f}%")

    # EMA trend
    ema_12 = ind.get("ema_12")
    ema_26 = ind.get("ema_26")
    if ema_12 and ema_26:
        trend = "bullish (short > long)" if ema_12 > ema_26 else "bearish (short < long)"
        interp.append(f"EMA12={ema_12:.2f} EMA26={ema_26:.2f} → {trend}")

    return signals
