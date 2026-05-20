"""
Paper trading engine.
Reads AI signals, applies risk rules, executes virtual trades,
and updates portfolio + position state in MySQL.
"""
import os
from datetime import datetime
from decimal import Decimal

from loguru import logger
from sqlalchemy import text

from app.database import db_session, get_engine
from app.models import Order, Portfolio, Position, Signal
from app.trading.risk import RiskManager

import pandas as pd


risk = RiskManager()

# ── Trading fees ──────────────────────────────────────────────────────────────
# Coinbase Advanced Trade taker fee — 0.40% for accounts under $10K/month
# Configurable via TRADING_FEE_PCT in .env
_FEE_RATE = Decimal(str(float(os.environ.get("TRADING_FEE_PCT", "0.004"))))


# ── Portfolio helpers ──────────────────────────────────────────────────────────

def get_portfolio() -> dict:
    """Returns current portfolio state as a plain dict."""
    with db_session() as session:
        p = session.query(Portfolio).first()
        if not p:
            return {"cash_balance": 10000.0, "total_value": 10000.0}
        return {
            "cash_balance": float(p.cash_balance),
            "total_value":  float(p.total_value),
        }


def get_positions() -> list[dict]:
    """Returns all open positions."""
    with db_session() as session:
        rows = session.query(Position).all()
        return [
            {
                "symbol":        p.symbol,
                "quantity":      float(p.quantity),
                "avg_buy_price": float(p.avg_buy_price),
                "stop_loss":     float(p.stop_loss) if p.stop_loss else None,
                "opened_at":     str(p.opened_at),
            }
            for p in rows
        ]


def _get_current_price(symbol: str) -> float | None:
    """Fetch latest close price from the ohlcv table."""
    engine = get_engine()
    query = text("""
        SELECT close FROM ohlcv
        WHERE symbol = :symbol
        ORDER BY ts DESC LIMIT 1
    """)
    with engine.connect() as conn:
        result = conn.execute(query, {"symbol": symbol}).fetchone()
    return float(result[0]) if result else None


def _update_portfolio_value(session, cash: float):
    """Recalculate total portfolio value = cash + market value of all positions."""
    positions = session.query(Position).all()
    holdings_value = 0.0
    for pos in positions:
        price = _get_current_price(pos.symbol) or float(pos.avg_buy_price)
        holdings_value += float(pos.quantity) * price

    total = cash + holdings_value
    p = session.query(Portfolio).first()
    if p:
        p.cash_balance = Decimal(str(cash))
        p.total_value  = Decimal(str(total))


# ── Trade execution ────────────────────────────────────────────────────────────

def execute_buy(symbol: str, signal: dict) -> dict | None:
    """
    Execute a virtual BUY order.
    Returns the order dict or None if rejected.
    """
    approved, reason = risk.check_signal({**signal, "symbol": symbol})
    if not approved:
        logger.info(f"[trading] BUY {symbol} rejected: {reason}")
        return None

    price = _get_current_price(symbol)
    if not price:
        logger.error(f"[trading] No price for {symbol} — cannot execute BUY")
        return None

    quantity   = risk.calculate_position_size(symbol, price)
    stop_loss  = risk.calculate_stop_loss(price)
    total_cost = quantity * price

    if quantity <= 0:
        logger.warning(f"[trading] Calculated zero quantity for {symbol}")
        return None

    with db_session() as session:
        portfolio = session.query(Portfolio).first()
        cash = float(portfolio.cash_balance)

        # Calculate fee upfront so cash check includes it
        fee_amount     = (Decimal(str(total_cost)) * _FEE_RATE).quantize(Decimal("0.00000001"))
        total_with_fee = total_cost + float(fee_amount)

        # Reject if insufficient cash (including fee)
        if total_with_fee > cash:
            logger.warning(f"[trading] Insufficient cash for {symbol}: need ${total_with_fee:.2f} (inc fee), have ${cash:.2f}")
            return None

        # Only buy if we don't already hold this coin — no averaging into positions
        position = session.query(Position).filter_by(symbol=symbol).first()
        if position:
            logger.info(f"[trading] Already holding {symbol} — skipping BUY to avoid over-exposure")
            return None

        session.add(Position(
            symbol=symbol,
            quantity=Decimal(str(quantity)),
            avg_buy_price=Decimal(str(price)),
            stop_loss=Decimal(str(stop_loss)),
            opened_at=datetime.utcnow(),
        ))

        # Record the order
        order = Order(
            symbol=symbol,
            side="BUY",
            quantity=Decimal(str(quantity)),
            price=Decimal(str(price)),
            total_value=Decimal(str(total_cost)),
            fee=fee_amount,
            signal_id=signal.get("signal_id"),
            reason="AI_SIGNAL",
            executed_at=datetime.utcnow(),
        )
        session.add(order)

        # Deduct cash including fee and update portfolio value
        new_cash = cash - total_with_fee
        _update_portfolio_value(session, new_cash)

    logger.info(
        f"[trading] BUY  {symbol}: {quantity:.6f} units @ ${price:.2f} "
        f"= ${total_cost:.2f} | stop=${stop_loss:.2f}"
    )
    return {"symbol": symbol, "side": "BUY", "quantity": quantity,
            "price": price, "total": total_cost, "stop_loss": stop_loss}


def execute_sell(symbol: str, signal: dict, reason: str = "AI_SIGNAL") -> dict | None:
    """
    Execute a virtual SELL order (full position).
    Returns the order dict or None if rejected.
    """
    if reason == "AI_SIGNAL":
        approved, rej_reason = risk.check_signal({**signal, "symbol": symbol})
        if not approved:
            logger.info(f"[trading] SELL {symbol} rejected: {rej_reason}")
            return None

    price = _get_current_price(symbol)
    if not price:
        logger.error(f"[trading] No price for {symbol} — cannot execute SELL")
        return None

    with db_session() as session:
        position = session.query(Position).filter_by(symbol=symbol).first()
        if not position:
            logger.warning(f"[trading] No position in {symbol} to sell")
            return None

        quantity    = float(position.quantity)
        avg_cost    = float(position.avg_buy_price)
        proceeds    = quantity * price
        cost_basis  = quantity * avg_cost
        pnl         = proceeds - cost_basis

        # Calculate fee — deducted from proceeds
        fee_amount   = (Decimal(str(proceeds)) * _FEE_RATE).quantize(Decimal("0.00000001"))
        net_proceeds = proceeds - float(fee_amount)
        pnl          = net_proceeds - cost_basis   # recalculate P&L net of fees

        # Record order
        order = Order(
            symbol=symbol,
            side="SELL",
            quantity=Decimal(str(quantity)),
            price=Decimal(str(price)),
            total_value=Decimal(str(proceeds)),
            fee=fee_amount,
            pnl=Decimal(str(pnl)),
            signal_id=signal.get("signal_id"),
            reason=reason,
            executed_at=datetime.utcnow(),
        )
        session.add(order)

        # Remove position
        session.delete(position)

        # Return net proceeds (after fee) and update portfolio
        portfolio = session.query(Portfolio).first()
        new_cash = float(portfolio.cash_balance) + net_proceeds
        _update_portfolio_value(session, new_cash)

    pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
    logger.info(
        f"[trading] SELL {symbol}: {quantity:.6f} units @ ${price:.2f} "
        f"= ${proceeds:.2f} | P&L: {pnl_str}"
    )
    return {"symbol": symbol, "side": "SELL", "quantity": quantity,
            "price": price, "total": proceeds, "pnl": pnl}


# ── Main trading cycle ─────────────────────────────────────────────────────────

def run_trading_cycle() -> dict:
    """
    Main entry point called by Celery.
    1. Check stop-losses on all open positions
    2. Read latest AI signals and execute trades
    Returns summary of actions taken.
    """
    from app.collectors.coinbase import CoinbaseTickerCollector

    summary = {"stop_losses": [], "buys": [], "sells": [], "skipped": []}

    # ── Step 1: Check stop-losses ──────────────────────────────────────────────
    try:
        prices = CoinbaseTickerCollector.get_latest_prices()
        triggered = risk.check_stop_losses(prices)
        for symbol in triggered:
            result = execute_sell(symbol, {}, reason="STOP_LOSS")
            if result:
                summary["stop_losses"].append(symbol)
    except Exception as e:
        logger.error(f"[trading] Stop-loss check failed: {e}")

    # ── Step 2: Process latest AI signals ─────────────────────────────────────
    engine = get_engine()
    query = text("""
        SELECT id, symbol, action, confidence, target_price, stop_loss, reasoning
        FROM   signals
        WHERE  ts >= NOW() - INTERVAL 90 MINUTE
        ORDER  BY ts DESC
    """)

    seen_symbols = set()
    with engine.connect() as conn:
        rows = conn.execute(query).fetchall()

    for row in rows:
        signal = {
            "signal_id":   row[0],
            "symbol":      row[1],
            "action":      row[2],
            "confidence":  float(row[3]),
            "target_price": float(row[4]) if row[4] else None,
            "stop_loss":   float(row[5]) if row[5] else None,
            "reasoning":   row[6],
        }
        symbol = signal["symbol"]

        # Only process one signal per symbol per cycle
        if symbol in seen_symbols:
            continue
        seen_symbols.add(symbol)

        action = signal["action"]

        if action == "BUY":
            result = execute_buy(symbol, signal)
            if result:
                summary["buys"].append(symbol)
            else:
                summary["skipped"].append(symbol)

        elif action == "SELL":
            result = execute_sell(symbol, signal)
            if result:
                summary["sells"].append(symbol)
            else:
                summary["skipped"].append(symbol)

        else:
            summary["skipped"].append(f"{symbol}(HOLD)")

    logger.info(
        f"[trading] Cycle complete — "
        f"buys={summary['buys']} sells={summary['sells']} "
        f"stop_losses={summary['stop_losses']} skipped={summary['skipped']}"
    )
    return summary


# ── Performance stats ──────────────────────────────────────────────────────────

def get_performance_stats() -> dict:
    """Returns P&L stats for the dashboard."""
    engine = get_engine()

    query = text("""
        SELECT
            COUNT(*)                                    AS total_trades,
            SUM(CASE WHEN side='BUY'  THEN 1 ELSE 0 END) AS buys,
            SUM(CASE WHEN side='SELL' THEN 1 ELSE 0 END) AS sells,
            COALESCE(SUM(pnl), 0)                       AS total_pnl,
            COALESCE(SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END), 0) AS winning_trades,
            COALESCE(SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END), 0) AS losing_trades,
            COALESCE(MAX(pnl), 0)                       AS best_trade,
            COALESCE(MIN(pnl), 0)                       AS worst_trade,
            COALESCE(SUM(fee), 0)                       AS total_fees
        FROM orders
    """)

    initial = float(os.environ.get("PAPER_TRADING_INITIAL_BALANCE", 10000))

    with engine.connect() as conn:
        row = conn.execute(query).fetchone()

    portfolio = get_portfolio()
    cash = portfolio["cash_balance"]

    # Calculate live total value from current prices rather than stale DB value
    with db_session() as session:
        positions = session.query(Position).all()
        holdings_value = 0.0
        for pos in positions:
            price = _get_current_price(pos.symbol)
            if price:
                holdings_value += float(pos.quantity) * price
            else:
                holdings_value += float(pos.quantity) * float(pos.avg_buy_price)

    total_value = cash + holdings_value
    total_return_pct = ((total_value - initial) / initial) * 100

    total_trades  = row[0] or 0
    winning       = row[4] or 0
    win_rate      = (winning / total_trades * 100) if total_trades > 0 else 0

    return {
        "initial_balance":  initial,
        "current_value":    total_value,
        "cash_balance":     portfolio["cash_balance"],
        "total_return_pct": round(total_return_pct, 2),
        "total_pnl":        float(row[3] or 0),
        "total_trades":     total_trades,
        "buys":             row[1] or 0,
        "sells":            row[2] or 0,
        "winning_trades":   winning,
        "losing_trades":    row[5] or 0,
        "win_rate":         round(win_rate, 1),
        "best_trade":       float(row[6] or 0),
        "worst_trade":      float(row[7] or 0),
        "total_fees":       float(row[8] or 0),
        "fee_rate_pct":     float(_FEE_RATE) * 100,
    }
