"""
Crypto Trader — Flask dashboard (Step 6)
Serves the trading dashboard with real-time portfolio, signals, and trade history.
"""
import os
from datetime import datetime

from flask import Flask, jsonify, render_template
from sqlalchemy import text

from app.database import db_session, get_engine
from app.trading.engine import get_performance_stats, get_portfolio, get_positions

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret")


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/portfolio")
def api_portfolio():
    try:
        stats     = get_performance_stats()
        positions = get_positions()
        engine    = get_engine()
        for pos in positions:
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT close FROM ohlcv WHERE symbol=:s ORDER BY ts DESC LIMIT 1"),
                    {"s": pos["symbol"]}
                ).fetchone()
            cp = float(row[0]) if row else pos["avg_buy_price"]
            pos["current_price"]  = cp
            pos["market_value"]   = cp * pos["quantity"]
            pos["unrealised_pnl"] = (cp - pos["avg_buy_price"]) * pos["quantity"]
            pos["unrealised_pct"] = ((cp - pos["avg_buy_price"]) / pos["avg_buy_price"]) * 100
        return jsonify({"stats": stats, "positions": positions})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/signals")
def api_signals():
    try:
        engine = get_engine()
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT id, symbol, action, confidence, target_price,
                       stop_loss, reasoning, created_at
                FROM   signals ORDER BY created_at DESC LIMIT 50
            """)).fetchall()
        return jsonify([{
            "id": r[0], "symbol": r[1], "action": r[2],
            "confidence": float(r[3]),
            "target_price": float(r[4]) if r[4] else None,
            "stop_loss": float(r[5]) if r[5] else None,
            "reasoning": r[6], "created_at": str(r[7]),
        } for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/trades")
def api_trades():
    try:
        engine = get_engine()
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT symbol, side, quantity, price, total_value, fee, pnl, reason, executed_at
                FROM   orders ORDER BY executed_at DESC LIMIT 100
            """)).fetchall()
        return jsonify([{
            "symbol": r[0], "side": r[1],
            "quantity": float(r[2]), "price": float(r[3]),
            "total": float(r[4]),
            "fee": float(r[5]) if r[5] is not None else None,
            "pnl": float(r[6]) if r[6] else None,
            "reason": r[7], "executed_at": str(r[8]),
        } for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/prices")
def api_prices():
    try:
        watchlist = [s.strip() for s in os.environ.get("WATCHLIST", "BTC-USD,ETH-USD,SOL-USD").split(",")]
        engine = get_engine()
        prices = {}
        for symbol in watchlist:
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT close, ts FROM ohlcv WHERE symbol=:s ORDER BY ts DESC LIMIT 1"),
                    {"s": symbol}
                ).fetchone()
            if row:
                prices[symbol] = {"price": float(row[0]), "ts": str(row[1])}
        return jsonify(prices)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/chart/<symbol>")
def api_chart(symbol):
    try:
        engine = get_engine()
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT o.ts, o.open, o.high, o.low, o.close, o.volume,
                       i.rsi_14, i.macd, i.macd_signal,
                       i.bb_upper, i.bb_lower, i.bb_mid
                FROM   ohlcv o
                LEFT   JOIN indicators i
                       ON i.symbol=o.symbol AND i.granularity=o.granularity AND i.ts=o.ts
                WHERE  o.symbol=:s AND o.granularity='1h'
                ORDER  BY o.ts DESC LIMIT 72
            """), {"s": symbol}).fetchall()
        rows = list(reversed(rows))
        return jsonify({
            "symbol":      symbol,
            "labels":      [str(r[0]) for r in rows],
            "open":        [float(r[1]) for r in rows],
            "high":        [float(r[2]) for r in rows],
            "low":         [float(r[3]) for r in rows],
            "close":       [float(r[4]) for r in rows],
            "volumes":     [float(r[5]) for r in rows],
            "rsi":         [float(r[6]) if r[6] else None for r in rows],
            "macd":        [float(r[7]) if r[7] else None for r in rows],
            "macd_signal": [float(r[8]) if r[8] else None for r in rows],
            "bb_upper":    [float(r[9])  if r[9]  else None for r in rows],
            "bb_lower":    [float(r[10]) if r[10] else None for r in rows],
            "bb_mid":      [float(r[11]) if r[11] else None for r in rows],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/sentiment")
def api_sentiment():
    try:
        engine = get_engine()
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT fear_greed_value, fear_greed_label, ts
                FROM market_sentiment ORDER BY ts DESC LIMIT 1
            """)).fetchone()
        if row:
            return jsonify({"value": row[0], "label": row[1], "ts": str(row[2])})
        return jsonify({"value": None, "label": "Unknown"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
