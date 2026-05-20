"""
Risk manager — enforces all trading rules before any order is placed.
All limits are configurable via .env.
"""
import os
from decimal import Decimal

from loguru import logger

from app.database import db_session
from app.models import Portfolio, Position


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


class RiskManager:
    def __init__(self):
        self.max_position_pct  = _env_float("MAX_POSITION_PCT",  0.10)  # 10% per trade
        self.stop_loss_pct     = _env_float("STOP_LOSS_PCT",     0.03)  # 3% stop-loss
        self.min_confidence    = _env_float("MIN_AI_CONFIDENCE", 0.70)  # 70% min
        self.max_open          = int(_env_float("MAX_OPEN_POSITIONS", 5))

    # ── Pre-trade checks ──────────────────────────────────────────────────────

    def check_signal(self, signal: dict) -> tuple[bool, str]:
        """
        Returns (approved, reason).
        Runs all risk checks before a trade is placed.
        """
        action     = signal.get("action", "HOLD")
        confidence = float(signal.get("confidence", 0))
        symbol     = signal.get("symbol", "")

        if action == "HOLD":
            return False, "Signal is HOLD — no trade needed"

        if confidence < self.min_confidence:
            return False, f"Confidence {confidence:.0%} below minimum {self.min_confidence:.0%}"

        with db_session() as session:
            portfolio = session.query(Portfolio).first()
            if not portfolio:
                return False, "No portfolio found"

            positions = session.query(Position).all()
            open_count = len(positions)
            position_symbols = {p.symbol for p in positions}

            # Max open positions check (only for new BUY positions)
            if action == "BUY" and symbol not in position_symbols:
                if open_count >= self.max_open:
                    return False, f"Max open positions ({self.max_open}) reached"

            # Sufficient cash for BUY
            if action == "BUY":
                cash = float(portfolio.cash_balance)
                if cash < 10:
                    return False, f"Insufficient cash: ${cash:.2f}"

            # Must hold position to SELL
            if action == "SELL" and symbol not in position_symbols:
                return False, f"No position in {symbol} to sell"

        return True, "approved"

    def calculate_position_size(self, symbol: str, price: float) -> float:
        """
        Returns the quantity to buy based on max position size rule.
        """
        with db_session() as session:
            portfolio = session.query(Portfolio).first()
            total_value = float(portfolio.total_value) if portfolio else 10000.0
            cash        = float(portfolio.cash_balance) if portfolio else 10000.0

        max_spend = min(total_value * self.max_position_pct, cash)
        quantity  = max_spend / price if price > 0 else 0

        logger.debug(
            f"[risk] {symbol} position size: "
            f"max_spend=${max_spend:.2f} @ ${price:.2f} = {quantity:.6f} units"
        )
        return quantity

    def calculate_stop_loss(self, entry_price: float) -> float:
        """Returns the stop-loss price for a given entry."""
        return entry_price * (1 - self.stop_loss_pct)

    # ── Stop-loss monitoring ──────────────────────────────────────────────────

    def check_stop_losses(self, current_prices: dict[str, float]) -> list[str]:
        """
        Compares current prices against stop-loss levels.
        Returns list of symbols that have breached their stop-loss.
        """
        triggered = []
        with db_session() as session:
            positions = session.query(Position).all()
            for pos in positions:
                price = current_prices.get(pos.symbol)
                if price and pos.stop_loss and price <= float(pos.stop_loss):
                    logger.warning(
                        f"[risk] STOP-LOSS triggered: {pos.symbol} "
                        f"price=${price:.4f} <= stop=${float(pos.stop_loss):.4f}"
                    )
                    triggered.append(pos.symbol)
        return triggered
